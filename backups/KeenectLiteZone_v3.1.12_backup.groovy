/**
*V3.1.13
*  V3.1.12 - Finalized with recursion fixes, state persistence, and master sync (Feb 2025)
 *
 *  Copyright 2019 Craig Romei
 *  Copyright 2022 Simon Dimenstein
 *
 *  Licensed under the Apache License, Version 2.0
 */

definition(
    name: "KeenectLiteZone",
    namespace: "Craig.Romei",
    author: "Craig Romei",
    description: "Zone application for 'Keenect', do not install directly.",
    category: "My Apps",
    parent: "Craig.Romei:KeenectLiteMaster",
    iconUrl: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg",
    iconX2Url: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg",
    iconX3Url: "https://raw.githubusercontent.com/napalmcsr/SmartThingsStuff/master/smartapps/keenect/clipart-thermometer-thermometer-clipart-free-6-thermometer-clip-art-clipartix-free-clipart.jpg",
    importUrl: "https://raw.githubusercontent.com/napalmcsr/Hubitat_Napalmcsr/master/Apps/KeenectLite/KeenectLiteZone.src"
)

preferences {
    page(name: "pageConfig")
}

def pageConfig() {
    dynamicPage(name: "", title: "", install: true, uninstall: true, refreshInterval:0) {
        section("Pause") {
            input "PauseZone", "bool", title: "Pause this zone", defaultValue: false
        }
        section("Devices") {
            input "tStat", "capability.thermostat", title: "Zone Thermostat", required: true
            input "vents", "capability.switchLevel", title: "Zone Vents", multiple: true, required: true
            input "tempSensor", "capability.temperatureMeasurement", title: "Zone Temperature Sensor", required: true
            input name: "tempSensorRefreshTime", title: "Refresh temp sensor (minutes)", type: "number", defaultValue: ""
            input "DashboardTileUpdate", "capability.sensor", title: "Display Tile (optional)"
        }
        section("Settings") {
            input "VentControlType", "enum", title: "Vent reactivity", defaultValue: "Normal", options: ["Aggressive", "Normal", "Slow", "Binary"]
            input "reverseActing", "bool", title: "Reverse acting vents?", defaultValue: false
            input name: "heatMinVo", title: "Heating min vent opening", type: "number", defaultValue: "0"
            input name: "heatMaxVo", title: "Heating max vent opening", type: "number", defaultValue: "100"
            input name: "coldMinVo", title: "Cooling min vent opening", type: "number", defaultValue: "0"
            input name: "coldMaxVo", title: "Cooling max vent opening", type: "number", defaultValue: "100"
            input name: "FanVo", title: "Fan mode vent opening", type: "number", defaultValue: "0"
            input "excludeFromRecirculation", "bool", title: "Exclude this zone from smart recirculation", defaultValue: false
        }
        section("Logging") {
            input "logLevel", "enum", title: "IDE logging level", options: getLogLevels(), defaultValue: "2"
        }
        section() {
            label title: "Zone name", required: false
        }
    }
}

def installed() {
    initialize()
}

def updated() {
    unsubscribe()
    initialize()
}

def initialize() {
    setVersion()
    infolog "Initializing ${state.InternalName} v${state.version}"
    subscribe(tempSensor, "temperature", tempHandler)
    subscribe(vents, "level", ventHandler)
    subscribe(tStat, "heatingSetpoint", setTstatHSP)
    subscribe(tStat, "coolingSetpoint", setTstatCSP)
    subscribe(tStat, "thermostatOperatingState", setTstatState)
    state.zoneHeatSetpoint = tStat.currentValue("heatingSetpoint").toFloat() + 1
    state.zoneColdSetpoint = tStat.currentValue("coolingSetpoint").toFloat() - 1
    state.currentTemperature = tempSensor.currentValue("temperature").toFloat()
    state.activeSetPoint = state.currentTemperature < (state.zoneColdSetpoint + state.zoneHeatSetpoint) / 2 ? state.zoneHeatSetpoint : state.zoneColdSetpoint
    state.thermostatState = tStat.currentValue("thermostatOperatingState").toUpperCase()
    state.mainTstatState = parent.getMainTstatState() ?: "IDLE"
    if (!state.stats) state.stats = [:]
    if (!state.stats.zoneStateChanges) state.stats.zoneStateChanges = []
    state.ventClosureDelay = 120
    state.stats.lastStateChange = now()
    state.idleNotified = false
    state.closureScheduled = false
    
    // Handle recirculation state on restart
    def wasInRecirculation = state.recirculationMode ?: false
    state.recirculationMode = false  // Always clear on restart for safety
    
    state.VentOpeningMap = [:]
    vents.each { vent ->
        state.VentOpeningMap[vent.displayName] = vent.currentValue("level")
    }
    
    // Determine initial vent state
    if (state.thermostatState == "HEATING" || state.thermostatState == "COOLING" || state.thermostatState == "FAN ONLY") {
        def initialOpening = state.thermostatState == "HEATING" ? heatMaxVo.toInteger() : coldMaxVo.toInteger()
        setVents(initialOpening)
        infolog "Zone active in ${state.thermostatState} mode, vents set to ${initialOpening}%"
    } else {
        // Zone is IDLE - check if master is in recirculation mode
        def masterRecirculating = false
        try {
            masterRecirculating = parent.state?.recirculationActive ?: false
        } catch (Exception e) {
            debuglog "Could not check master recirculation state: ${e.message}"
        }
        
        if (masterRecirculating && !excludeFromRecirculation) {
            // Rejoin recirculation mode after restart
            infolog "Rejoining recirculation mode after restart"
            runIn(3, "rejoinRecirculationMode")  // Delay to ensure master is ready
        } else {
            closeVentsLocally()
            infolog "Zone idle, vents closed"
        }
    }
    
    if (wasInRecirculation) {
        infolog "Cleared recirculation mode on zone restart for safety"
    }
    
    infolog "Initialization complete"
}

def rejoinRecirculationMode() {
    try {
        def masterRecirculating = parent.state?.recirculationActive ?: false
        if (masterRecirculating && !excludeFromRecirculation) {
            enterRecirculationMode()
            infolog "Successfully rejoined recirculation mode"
        } else {
            debuglog "Master no longer in recirculation mode"
        }
    } catch (Exception e) {
        infolog "Failed to rejoin recirculation mode: ${e.message}"
    }
}

def ParentGetTstatState() {
    debuglog "Parent query for ${app.label}, state: ${state.thermostatState}"
    return state.thermostatState
}

def setTstatState(evt) {
    infolog "Thermostat state changed to ${evt.value}"
    state.thermostatState = evt.value.toUpperCase()
    parent.SetChildAction(app.label)
    zoneEvaluate()
}

def setTstatHSP(evt) {
    def newSP = evt.value.toFloat() + 1
    if (state.lastSPChange && (now() - state.lastSPChange < 5000)) {
        debuglog "Debouncing rapid SP change from ${state.zoneHeatSetpoint} to ${newSP}"
        return
    }
    infolog "Heating setpoint changed to ${evt.value}"
    state.zoneHeatSetpoint = newSP
    state.lastSPChange = now()
    zoneEvaluate()
}

def setTstatCSP(evt) {
    def newSP = evt.value.toFloat() - 1
    if (state.lastSPChange && (now() - state.lastSPChange < 5000)) {
        debuglog "Debouncing rapid SP change from ${state.zoneColdSetpoint} to ${newSP}"
        return
    }
    infolog "Cooling setpoint changed to ${evt.value}"
    state.zoneColdSetpoint = newSP
    state.lastSPChange = now()
    zoneEvaluate()
}

def tempHandler(evt) {
    infolog "Temperature changed to ${evt.value}"
    state.currentTemperature = evt.value.toFloat()
    zoneEvaluate()
}

def ventHandler(evt) {
    infolog "Vent ${evt.device} changed to ${evt.value}"
    state.VentOpeningMap = [:]
    vents.each { vent ->
        state.VentOpeningMap[vent.displayName] = vent.currentValue("level")
    }
    sendDisplayTile()
}

def zoneEvaluate() {
    infolog "Running zoneEvaluate for ${app.label}"
    state.currentTemperature = tempSensor.currentValue("temperature").toFloat()
    def tStatState = tStat.currentValue("thermostatOperatingState").toUpperCase()
    state.zoneHeatSetpoint = tStat.currentValue("heatingSetpoint").toFloat() + 1
    state.zoneColdSetpoint = tStat.currentValue("coolingSetpoint").toFloat() - 1
    debuglog "Fresh data - Temp: ${state.currentTemperature}, Heat SP: ${state.zoneHeatSetpoint}, Cool SP: ${state.zoneColdSetpoint}, Tstat State: ${tStatState}"
    if (state.recirculationMode && tStatState == "IDLE") {
        debuglog "Zone in recirculation mode and idle, maintaining current vent position"
        sendDisplayTile()
        acknowledgeStateUpdate()
        return
    }
    def oldState = state.thermostatState
    def nowTime = now()

    if (tStatState != state.thermostatState) {
        state.thermostatState = tStatState
        state.stats.lastStateChange = nowTime
    }

    if (state.thermostatState != oldState) {
        def duration = nowTime - (state.stats.lastStateChange ?: nowTime)
        state.stats.zoneStateChanges << [timestamp: nowTime, fromState: oldState, toState: state.thermostatState, duration: duration / 1000]
        state.stats.lastStateChange = nowTime
        debuglog "State change: ${oldState} to ${state.thermostatState}, duration: ${duration / 1000}s"
        if (state.thermostatState == "IDLE" && !state.idleNotified) {
            def mainState = parent.getMainTstatState()
            def allOtherZonesIdle = parent.allZonesIdleExcept(this)
            def recentIdleTransition = (now() - state.stats.lastStateChange) < 5000
            debuglog "Main state: ${mainState}, All other zones idle: ${allOtherZonesIdle}"
            if (!allOtherZonesIdle && !recentIdleTransition) {
                debuglog "Not last zone, closing vents immediately"
                closeVentsLocally()
            } else if (!state.closureScheduled) {
                debuglog "Last zone or simultaneous satisfaction, delaying vent closure"
                def delay = state.ventClosureDelay ?: 120
                runIn(delay, "closeVentsLocally")
                infolog "Scheduled vent closure for last zone in ${delay}s"
                state.closureScheduled = true
            }
            sendStatstoParent()
            parent.SetChildAction(app.label)
            state.idleNotified = true
        }
    } else if (state.thermostatState != "IDLE") {
        state.idleNotified = false
        state.closureScheduled = false
        unschedule("closeVentsLocally")
        debuglog "Cancelled pending vent closure due to state change"
    }

    def hysteresis = 0.5
    def shouldBeIdle = (state.thermostatState == "HEATING" && state.currentTemperature >= state.zoneHeatSetpoint + hysteresis) ||
                       (state.thermostatState == "COOLING" && state.currentTemperature <= state.zoneColdSetpoint - hysteresis)
    if (shouldBeIdle) {
        state.thermostatState = "IDLE"
        state.stats.lastStateChange = nowTime
        def mainState = parent.getMainTstatState()
        def allOtherZonesIdle = parent.allZonesIdleExcept(this)
        def recentIdleTransition = (now() - state.stats.lastStateChange) < 5000
        debuglog "Main state: ${mainState}, All other zones idle: ${allOtherZonesIdle}"
        if (!allOtherZonesIdle && !recentIdleTransition) {
            debuglog "Not last zone, closing vents immediately due to setpoint/temp"
            closeVentsLocally()
        } else if (!state.closureScheduled) {
            debuglog "Last zone satisfied due to setpoint/temp, delaying vent closure"
            def delay = state.ventClosureDelay ?: 120
            runIn(delay, "closeVentsLocally")
            infolog "Scheduled vent closure for last zone in ${delay}s"
            state.closureScheduled = true
        }
        if (!state.idleNotified) {
            sendStatstoParent()
            parent.SetChildAction(app.label)
            state.idleNotified = true
        }
    }

    Map VentParams = state.thermostatState == "HEATING" ? SetHeatVentParams() :
                     state.thermostatState == "COOLING" ? SetCoolVentParams() :
                     state.thermostatState == "FAN ONLY" ? SetFanVentParams() :
                     SetIdleVentParams()
    CalculteVent(VentParams)
    state.zoneVoLocal = VentParams.ventOpening

    if (state.thermostatState == "HEATING" || state.thermostatState == "COOLING" || state.thermostatState == "FAN ONLY") {
        setVents(VentParams.ventOpening)
    }
    sendDisplayTile()
    acknowledgeStateUpdate()
    infolog "zoneEvaluate complete"
}

def closeVentsLocally() {
    debuglog "Closing vents locally for ${app.label}"
    if (!PauseZone) {
        vents.each { vent ->
            try {
                vent.setLevel(0)
                state.VentOpeningMap[vent.displayName] = 0
            } catch (Exception e) {
                infolog "Failed to close ${vent}, retrying in 5s: ${e.message}"
                runIn(5, "retryCloseVent", [data: vent])
            }
        }
    }
    state.closureScheduled = false
    sendDisplayTile()
}

def closeVentsAfterHVACOff() {
    debuglog "Closing vents after HVAC off for ${app.label}"
    if (!PauseZone) {
        vents.each { vent ->
            try {
                vent.setLevel(0)
                state.VentOpeningMap[vent.displayName] = 0
            } catch (Exception e) {
                infolog "Failed to close ${vent}, retrying in 5s: ${e.message}"
                runIn(5, "retryCloseVent", [data: vent])
            }
        }
    }
    sendDisplayTile()
}

def retryCloseVent(vent) {
    try {
        vent.setLevel(0)
        state.VentOpeningMap[vent.displayName] = 0
        debuglog "Retry succeeded for ${vent}"
    } catch (Exception e) {
        infolog "Retry failed for ${vent}: ${e.message}"
    }
}

def forceCloseVents() {
    def mainState = parent.getMainTstatState()
    if (mainState == "IDLE" || mainState == "OFF") {
        debuglog "Forcing vent closure for ${app.label} as HVAC is off"
        closeVentsLocally()
    } else {
        debuglog "Skipping force closure for ${app.label}, HVAC still on: ${mainState}"
    }
}

def CalculteVent(Map VentParams) {
    VentParams.ventOpening = Math.round(VentParams.tempDelta * VentParams.ventSlope.toInteger() + VentParams.ventIntercept.toInteger())
    if (reverseActing) VentParams.ventOpening = 100 - VentParams.ventOpening
    if (VentParams.ventOpening > VentParams.MaxVo.toInteger()) VentParams.ventOpening = VentParams.MaxVo.toInteger()
    if (VentParams.ventOpening < VentParams.MinVo.toInteger()) VentParams.ventOpening = VentParams.MinVo.toInteger()
}

def SetHeatVentParams() {
    Map resultMap = [:]
    state.activeSetPoint = state.zoneHeatSetpoint
    resultMap.tempDelta = state.zoneHeatSetpoint - state.currentTemperature
    switch (VentControlType) {
        case "Aggressive":
            resultMap.ventSlope = (heatMaxVo.toInteger() - heatMinVo.toInteger()) * 2
            resultMap.ventIntercept = (heatMaxVo.toInteger() - heatMinVo.toInteger()) / 5 + heatMinVo.toInteger()
            break
        case "Normal":
            resultMap.ventSlope = (heatMaxVo.toInteger() - heatMinVo.toInteger())
            resultMap.ventIntercept = heatMinVo.toInteger()
            break
        case "Slow":
            resultMap.ventSlope = (heatMaxVo.toInteger() - heatMinVo.toInteger()) / 2
            resultMap.ventIntercept = heatMinVo.toInteger()
            break
        case "Binary":
            resultMap.ventSlope = 10000
            resultMap.ventIntercept = heatMinVo.toInteger()
            break
    }
    resultMap.ventOpening = 50
    resultMap.MaxVo = heatMaxVo
    resultMap.MinVo = heatMinVo
    return resultMap
}

def SetCoolVentParams() {
    Map resultMap = [:]
    state.activeSetPoint = state.zoneColdSetpoint
    resultMap.tempDelta = state.currentTemperature - state.zoneColdSetpoint
    switch (VentControlType) {
        case "Aggressive":
            resultMap.ventSlope = (coldMaxVo.toInteger() - coldMinVo.toInteger()) * 2
            resultMap.ventIntercept = (coldMaxVo.toInteger() - coldMinVo.toInteger()) / 5 + coldMinVo.toInteger()
            break
        case "Normal":
            resultMap.ventSlope = (coldMaxVo.toInteger() - coldMinVo.toInteger())
            resultMap.ventIntercept = coldMinVo.toInteger()
            break
        case "Slow":
            resultMap.ventSlope = (coldMaxVo.toInteger() - coldMinVo.toInteger()) / 2
            resultMap.ventIntercept = coldMinVo.toInteger()
            break
        case "Binary":
            resultMap.ventSlope = 10000
            resultMap.ventIntercept = coldMinVo.toInteger()
            break
    }
    resultMap.ventOpening = 50
    resultMap.MaxVo = coldMaxVo
    resultMap.MinVo = coldMinVo
    return resultMap
}

def SetFanVentParams() {
    Map resultMap = [:]
    resultMap.tempDelta = 0.1
    resultMap.ventSlope = 0
    resultMap.ventIntercept = FanVo.toInteger()
    resultMap.ventOpening = FanVo.toInteger()
    resultMap.MaxVo = 100
    resultMap.MinVo = 0
    return resultMap
}

def SetIdleVentParams() {
    Map resultMap = [:]
    resultMap.tempDelta = 0.1
    resultMap.ventSlope = 0
    resultMap.ventIntercept = 0
    resultMap.ventOpening = 0
    resultMap.MaxVo = 100
    resultMap.MinVo = 0
    return resultMap
}

def setVents(newVo) {
    debuglog "Setting vents to ${newVo}"
    if (!PauseZone && (state.thermostatState == "HEATING" || state.thermostatState == "COOLING" || state.thermostatState == "FAN ONLY")) {
        vents.each { vent ->
            def currentLevel = state.VentOpeningMap[vent.displayName]?.toInteger() ?: vent.currentValue("level").toInteger()
            if ((currentLevel - newVo.toInteger()).abs() > 4) {
                try {
                    vent.setLevel(newVo)
                    debuglog "Set ${vent} to ${newVo}"
                    runIn(60, "ventcheck")
                } catch (Exception e) {
                    infolog "Failed to set ${vent} to ${newVo}: ${e.message}, retrying in 5s"
                    runIn(5, "retrySetVent", [data: [vent: vent, level: newVo]])
                }
            }
        }
    }
}

def retrySetVent(data) {
    def vent = data.vent
    def level = data.level
    try {
        vent.setLevel(level)
        debuglog "Retry succeeded for ${vent} to ${level}"
    } catch (Exception e) {
        infolog "Retry failed for ${vent} to ${level}: ${e.message}"
    }
}

def ventcheck() {
    debuglog "Checking vents"
    if (state.thermostatState == "HEATING" || state.thermostatState == "COOLING" || state.thermostatState == "FAN ONLY") {
        setVents(state.zoneVoLocal)
    }
}

def sendDisplayTile() {
    if (DashboardTileUpdate) {
        def reportString = "${app.label}<br>Mode: ${state.mainTstatState}<br>Setpoint: ${state.activeSetPoint}<br>Temp: ${state.currentTemperature}<br>"
        vents.each { vent ->
            reportString += "${vent.displayName}: ${vent.currentValue('level')}<br>"
        }
        DashboardTileUpdate.SetKeenectData(reportString)
    }
}

def sendStatstoParent() {
    Map ChildMap = [
        title: app.label,
        setpoint: state.activeSetPoint,
        currentTemperature: state.currentTemperature,
        vent: state.zoneVoLocal,
        state: state.thermostatState
    ]
    try {
        parent.SetChildStats(ChildMap)
        debuglog "Stats sent to parent"
    } catch (Exception e) {
        infolog "Failed to send stats: ${e.message}"
    }
}

def acknowledgeStateUpdate() {
    parent.AcknowledgeChildStateUpdate(app.label)
}

def logZoneStats() {
    infolog "Zone ${app.label} Stats: State Changes - ${state.stats.zoneStateChanges.size()}"
    if (logLevel?.toInteger() >= 2) {
        state.stats.zoneStateChanges.each { change ->
            infolog "  ${new Date(change.timestamp).format('HH:mm:ss')} - ${change.fromState} to ${change.toState}, ${change.duration}s"
        }
    }
}

def enterRecirculationMode() {
    debuglog "Entering smart recirculation mode for ${app.label}"
    if (excludeFromRecirculation) {
        infolog "Zone ${app.label} excluded from smart recirculation"
        return
    }
    
    state.recirculationMode = true
    def fanOpening = FanVo?.toInteger() ?: 30
    
    if (!PauseZone) {
        vents.each { vent ->
            try {
                vent.setLevel(fanOpening)
                state.VentOpeningMap[vent.displayName] = fanOpening
                debuglog "Set ${vent} to ${fanOpening}% for recirculation"
            } catch (Exception e) {
                infolog "Failed to set ${vent} for recirculation: ${e.message}"
                runIn(5, "retrySetVent", [data: [vent: vent, level: fanOpening]])
            }
        }
    }
    
    sendDisplayTile()
    infolog "Zone ${app.label} vents set to ${fanOpening}% for smart recirculation"
}

def exitRecirculationMode() {
    debuglog "Exiting smart recirculation mode for ${app.label}"
    state.recirculationMode = false
    
    // Close vents when exiting recirculation if zone is idle
    if (state.thermostatState == "IDLE") {
        closeVentsLocally()
        infolog "Zone ${app.label} vents closed after exiting recirculation"
    } else {
        infolog "Zone ${app.label} exited recirculation, maintaining vents for active ${state.thermostatState} mode"
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
    state.version = "3.1.12"
    state.InternalName = "KeenectLiteZone"
}
