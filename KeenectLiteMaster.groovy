/**
*  
*V2.1.13 - circulate
*V2.1.12 - Fixed "cool off" (June 2025) 
*  V2.1.11 - Finalized with recursion fixes, state refresh, and diagnostic logging (Feb 2025)
 *
 *  Copyright 2019 Craig Romei
 *  Copyright 2020 Simon Dimenstein
 *
 *  Licensed under the Apache License, Version 2.0
 */

import groovy.transform.Field

@Field action = ['HEATING':1, 'COOLING':1, 'IDLE':0, 'OFF':0]

definition(
    name: "KeenectLiteMaster",
    namespace: "Craig.Romei",
    author: "Craig Romei",
    description: "Keen Vent Manager",
    category: "My Apps",
    installOnOpen: true,
    iconUrl: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg",
    iconX2Url: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg",
    iconX3Url: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg"
)

preferences {
    page(name: "pageConfig")
}

def pageConfig() {
    dynamicPage(name: "", title: "", install: true, uninstall: true, refreshInterval:0) {
        section("Setup") {
            input name: "HVACSwitch", title: "HVAC Control Switch", type: "capability.switch", required: true
            input name: "isACcapable", title: "System is AC capable", type: "bool", defaultValue: true
            input name: "masterHVACMode", title: "Select HVAC Mode (Heat/Cool/Off)", type: "enum", options: ["HEAT", "COOL", "OFF"], defaultValue: "HEAT"
            input "ventClosureDelay", "number", title: "Vent Closure Delay (seconds) after HVAC off", defaultValue: 120
        }
        section("Circulation Options") {
    		input name: "HVACModeCirculate", title: "Keep fan running when HVAC off (continuous circulation)", type: "bool", defaultValue: false
    		input name: "enableRecirculation", title: "Enable smart recirculation (opens vents during idle)", type: "bool", defaultValue: false
    		input name: "recirculationDelay", title: "Smart recirculation delay (minutes)", type: "number", defaultValue: 15
		}
        section("Zones") {
            app(name: "childZones", appName: "KeenectLiteZone", namespace: "Craig.Romei", title: "Create New Vent Zone...", multiple: true)
        }
        section("Logging") {
            input "logLevel", "enum", title: "IDE logging level", options: getLogLevels(), defaultValue: "2"
        }
    }
}

def installed() {
    initialize()
}

def updated() {
    initialize()
}

def initialize() {
    setVersion()
    infolog "Initializing ${state.InternalName} v${state.version}"
    unsubscribe()
    state.action = 0
    state.childTemps = [:]
    state.childSetpoint = [:]
    state.childVentOpening = [:]
    state.childState = [:]
    if (state.childAck == null) state.childAck = [:]
    state.debounceSetChildAction = false
    state.lastDebounceReset = now()
    state.stats = [:]
    state.stats.hvacActions = []
    state.stats.lastHvacAction = now()
    state.mainState = "IDLE"
    state.retryCount = 0
    
    // Add recirculation state variables
    state.recirculationActive = false
    state.lastAllIdleTime = null

    if (!HVACSwitch) {
        infolog "ERROR: HVACSwitch not defined!"
        return
    }
    subscribe(HVACSwitch, "HVACstate", OperatingStateHandler)
    infolog "Master HVAC mode is ${masterHVACMode}"
    childApps.each { child ->
        try {
            child.initialize()
            state.childState[child.label] = child.ParentGetTstatState()
            debuglog "Initialized ${child.label} with state ${state.childState[child.label]}"
        } catch (Exception e) {
            infolog "Failed to initialize ${child.label}: ${e.message}"
            state.childState[child.label] = "IDLE"
        }
    }
    schedule('0/15 * * ? * * *', "SetChildAction", [data:"Parent"])
    schedule('0 */5 * * * ?', "checkHVACConsistency")
    schedule('0 0 * * * ?', "logSystemStats")
    
    // Add recirculation check schedule
    if (enableRecirculation) {
        schedule('0 */3 * * * ?', "checkRecirculation")
        infolog "Smart recirculation enabled with ${recirculationDelay ?: 15} minute delay"
    }
}

def getMainTstatState() {
    def hvacState = HVACSwitch.currentValue("HVACstate")?.toUpperCase() ?: "IDLE"
    debuglog "HVACSwitch State: ${hvacState}"
    return hvacState
}

def OperatingStateHandler(evt) {
    debuglog "HVAC state changed to ${evt.value.toUpperCase()}"
}

def SetChildAction(FromApp) {
    debuglog "Call for action from ${FromApp}"
    if (state.debounceSetChildAction && (now() - state.lastDebounceReset > 20000)) {
        infolog "Debounce stuck for over 20s, forcing reset"
        state.debounceSetChildAction = false
    }
    if (!state.debounceSetChildAction || FromApp == app.label) {
        state.debounceSetChildAction = true
        state.lastDebounceReset = now()
        runIn(2, "debounceSetChildAction")
        state.action = 0
        childApps.each { child ->
            def childState = child.ParentGetTstatState()
            state.childState[child.label] = childState
            infolog "Child app: ${child.label} HVAC state: ${childState}"
            def contribution = action[childState] ?: 0
            state.action += contribution
            debuglog "Action for ${child.label}: ${childState}, contributes: ${contribution}, running total: ${state.action}"
        }
        debuglog "Total action count: ${state.action}"
        
        if (state.action > 0) {
            // Zone demand detected - exit recirculation if active
            if (state.recirculationActive) {
                stopRecirculation("Zone heating/cooling demand")
            }
            infolog "Activating HVAC for ${state.action} zones"
            callAction(1)
        } else {
            infolog "All zones idle"
            if (!state.recirculationActive) {
                // Only turn off HVAC if not recirculating
                def hvacState = getMainTstatState()
                if (hvacState != "IDLE" && hvacState != "OFF") {
                    infolog "HVAC state mismatch, forcing OFF"
                    callAction(0)
                }
            }
            // If recirculating, leave system as-is (fan running, vents open)
        }
    } else {
        debuglog "Debounce active, skipping"
    }
}

def debounceSetChildAction() {
    debuglog "Debounce reset"
    state.debounceSetChildAction = false
    state.lastDebounceReset = now()
}

def callAction(OnOff) {
    debuglog "Call for Action: ${OnOff ? 'ON' : 'OFF'}"
    def now = now()
    if (!state.stats.hvacActions) state.stats.hvacActions = []
    if (!state.stats.totalOnTime) state.stats.totalOnTime = 0

    def lastAction = state.stats.hvacActions ? state.stats.hvacActions[-1] : null
    if (lastAction && OnOff != (lastAction.action == "ON")) {
        def duration = now - state.stats.lastHvacAction
        state.stats.hvacActions << [timestamp: now, action: OnOff ? "ON" : "OFF", duration: duration / 1000]
        if (lastAction.action == "ON") state.stats.totalOnTime += duration / 1000
        infolog "${new Date(now).format('HH:mm:ss')} - HVAC ${OnOff ? 'ON' : 'OFF'}, ${duration / 1000}s"
        if (state.stats.hvacActions.size() > 10) state.stats.hvacActions = state.stats.hvacActions[-10..-1]
    } else if (!lastAction) {
        state.stats.hvacActions << [timestamp: now, action: OnOff ? "ON" : "OFF", duration: 0]
        infolog "${new Date(now).format('HH:mm:ss')} - HVAC ${OnOff ? 'ON' : 'OFF'}, 0s"
    }
    state.stats.lastHvacAction = now

    if (OnOff) {
        switch (masterHVACMode) {
            case "HEAT":
                HVACSwitch.push(2)
                HVACSwitch.push(6)
                state.mainState = "HEATING"
                break
            case "COOL":
                HVACSwitch.push(4)
                HVACSwitch.push(6)
                state.mainState = "COOLING"
                break
        }
    } else {
        // HVAC shutdown sequence
        infolog "Initiating HVAC shutdown sequence"
        
        // Step 1: Turn off the correct mode
        switch (state.mainState) {
            case "HEATING":
                infolog "Shutting down heating mode"
                HVACSwitch.push(3)
                pauseExecution(500)
                break
            case "COOLING":
                infolog "Shutting down cooling mode"  
                HVACSwitch.push(5)
                pauseExecution(500)
                break
            default:
                infolog "Shutting down unknown mode - sending general off"
                HVACSwitch.push(1)
                pauseExecution(500)
        }
        
        // Step 2: Enhanced fan control logic
        if (state.recirculationActive) {
            // Keep fan running for recirculation
            HVACSwitch.push(6)
            infolog "Keeping fan on for smart recirculation mode"
        } else if (HVACModeCirculate) {
            // Traditional circulation - fan stays on
            HVACSwitch.push(6)
            infolog "Keeping fan on per continuous circulation setting"
        } else {
            // Turn fan off
            HVACSwitch.push(7)
            infolog "Turning fan off"
        }
        
        state.mainState = "IDLE"
        runIn(5, "verifyHVACOff")
        
        // Only schedule vent closure if not in recirculation mode
        if (!state.recirculationActive) {
            def delay = ventClosureDelay ?: 120
            state.ventsClosingScheduled = true
            state.lastVentScheduleTime = now
            runIn(delay, "closeAllZoneVents")
            infolog "Scheduled closeAllZoneVents in ${delay}s for final shutdown"
            runIn(delay + 30, "forceCloseAllVents")
        }
    }
}

def closeAllZoneVents() {
    if (state.recirculationActive) {
        infolog "Skipping vent closure - smart recirculation mode active"
        return
    }
    
    infolog "Closing all zone vents at ${new Date(now()).format('HH:mm:ss')}"
    childApps.each { child ->
        if (!child.PauseZone) {
            try {
                child.closeVentsAfterHVACOff()
                infolog "Closed vents for ${child.label}"
            } catch (Exception e) {
                infolog "Failed to close vents for ${child.label}: ${e.message}"
            }
        } else {
            infolog "Skipping ${child.label} due to pause"
        }
    }
    state.ventsClosingScheduled = false
    state.lastVentScheduleTime = null
}

def forceCloseAllVents() {
    def hvacState = getMainTstatState()
    if (hvacState == "IDLE" || hvacState == "OFF") {
        infolog "Forcing closure of all vents as HVAC is ${hvacState} and vents not closed"
        childApps.each { child ->
            try {
                child.closeVentsAfterHVACOff()
                infolog "Forced vents closed for ${child.label}"
            } catch (Exception e) {
                infolog "Force close failed for ${child.label}: ${e.message}"
            }
        }
    }
    state.ventsClosingScheduled = false
    state.lastVentScheduleTime = null
}

def verifyHVACOff() {
    def hvacState = HVACSwitch.currentValue("HVACstate")?.toUpperCase()
    debuglog "Verifying HVAC off, state: ${hvacState}"
    if (hvacState != "IDLE" && hvacState != "OFF") {
        state.retryCount = (state.retryCount ?: 0) + 1
        if (state.retryCount <= 3) {
            infolog "HVAC failed to turn off, retrying (${state.retryCount}/3)"
            callAction(0)
        } else {
            infolog "HVAC state stuck, forcing vent closure anyway"
            closeAllZoneVents()
            state.retryCount = 0
        }
    } else {
        state.retryCount = 0
    }
}

def checkHVACConsistency() {
    debuglog "Running HVAC consistency check"
    def allIdle = childApps.every { child ->
        def childState = state.childState[child.label] ?: child.ParentGetTstatState()
        debuglog "Check - ${child.label}: ${childState}"
        childState == "IDLE" || childState == "OFF"
    }
    def hvacState = HVACSwitch.currentValue("HVACstate")?.toUpperCase()
    if (allIdle && hvacState != "IDLE" && hvacState != "OFF") {
        infolog "HVAC stuck on, forcing off"
        callAction(0)
    }
}

def allZonesIdleExcept(exceptZone) {
    def mainState = getMainTstatState()
    def allIdle = childApps.every { child ->
        if (child == exceptZone) {
            return true
        }
        def childState = state.childState[child.label] ?: "IDLE"
        debuglog "Checking zone ${child.label}: ${childState}"
        childState == "IDLE" || childState == "OFF"
    }
    debuglog "All zones idle except ${exceptZone?.label ?: 'unknown'}: ${allIdle}"
    return allIdle
}

def AcknowledgeChildStateUpdate(childName) {
    debuglog "State update from ${childName} acknowledged"
    if (state.childAck == null) state.childAck = [:]
    state.childAck[childName] = true
    if (state.childAck.size() == childApps.size()) {
        state.childAck = [:]
    }
}

def SetChildStats(ZoneStat) {
    debuglog "SetChildStats: ${ZoneStat}"
    state.childTemps[ZoneStat.title] = ZoneStat.currentTemperature
    state.childSetpoint[ZoneStat.title] = ZoneStat.setpoint
    state.childVentOpening[ZoneStat.title] = ZoneStat.vent
    state.childState[ZoneStat.title] = ZoneStat.state
}

def logSystemStats() {
    infolog "System Stats: HVAC Actions - ${state.stats.hvacActions.size()}, Total ON Time: ${state.stats.totalOnTime ?: 0}s"
    if (logLevel?.toInteger() >= 2) {
        state.stats.hvacActions.each { action ->
            infolog "  ${new Date(action.timestamp).format('HH:mm:ss')} - HVAC ${action.action}, ${action.duration}s"
        }
    }
    childApps.each { child ->
        try {
            child.logZoneStats()
        } catch (Exception e) {
            infolog "Failed to log stats for ${child.label}: ${e.message}"
        }
    }
}

def checkRecirculation() {
    if (!enableRecirculation) return
    
    // Don't interfere with active HVAC operations
    if (state.debounceSetChildAction) {
        debuglog "Skipping recirculation check - system busy"
        return
    }
    
    def allZonesIdle = childApps.every { child ->
        def childState = state.childState[child.label] ?: child.ParentGetTstatState()
        return (childState == "IDLE" || childState == "OFF")
    }
    
    def hvacState = getMainTstatState()
    def currentTime = now()
    def delayMs = (recirculationDelay ?: 15) * 60000
    
    if (allZonesIdle && (hvacState == "IDLE" || hvacState == "OFF")) {
        if (!state.lastAllIdleTime) {
            state.lastAllIdleTime = currentTime
            infolog "All zones idle, starting smart recirculation timer (${recirculationDelay ?: 15} minutes)"
        } else if ((currentTime - state.lastAllIdleTime) >= delayMs && !state.recirculationActive) {
            startRecirculation()
        }
    } else {
        // Any zone calling - exit recirculation
        if (state.recirculationActive) {
            stopRecirculation("Zone demand detected")
        }
        state.lastAllIdleTime = null
    }
}

def startRecirculation() {
    infolog "Starting smart recirculation mode - opening participating vents"
    state.recirculationActive = true
    
    // Notify participating zones to open vents
    childApps.each { child ->
        if (!child.excludeFromRecirculation) {
            try {
                child.enterRecirculationMode()
            } catch (Exception e) {
                infolog "Failed to set recirculation for ${child.label}: ${e.message}"
            }
        }
    }
    
    // Ensure fan is running (may already be on from HVACModeCirculate)
    HVACSwitch.push(6)  // fanOn
    infolog "Smart recirculation mode active"
}

def stopRecirculation(reason = "System demand") {
    infolog "Stopping smart recirculation mode: ${reason}"
    state.recirculationActive = false
    state.lastAllIdleTime = null
    
    // Smart fan control when exiting recirculation
    if (HVACModeCirculate) {
        infolog "Leaving fan on per continuous circulation setting"
        // Fan stays on due to HVACModeCirculate
    } else {
        HVACSwitch.push(7)  // fanOff
        infolog "Turning fan off"
    }
    
    // Reset all zones (close vents if appropriate)
    childApps.each { child ->
        try {
            child.exitRecirculationMode()
        } catch (Exception e) {
            infolog "Failed to exit recirculation for ${child.label}: ${e.message}"
        }
    }
}

def debuglog(statement) {
    def logL = logLevel?.toInteger() ?: 0
    if (logL >= 2) log.debug(statement)
}

def infolog(statement) {
    def logL = logLevel?.toInteger() ?: 0
    if (logL >= 1) log.info(statement)
}

def getLogLevels() {
    return [["0":"None"], ["1":"Running"], ["2":"NeedHelp"]]
}

def setVersion() {
    state.version = "2.1.12"
    state.InternalName = "KeenectLiteMaster"
}
