// VERSION: 0.3
// typo in call


metadata {
    definition (name: "HVAC driver", namespace: "dimenstein", author: "Simon Dimenstein", importUrl: "") {

        capability "Actuator"
// for hubitat comment the next line and uncomment the one after that is currently commented
//		capability "Button"
		capability "PushableButton"		// hubitat changed `Button` to `PushableButton`  2018-04-20
		capability "Sensor"
		capability "Switch"
		capability "Health Check"

		attribute "HVACstate", "enum", ['heating', 'cooling', 'idle', 'off']


		command "off"
		command "heatOn"
		command "heatOff"
		command "coolOn"
		command "coolOff"
		command "fanOn"
		command "fanOff"
        command "push", ["number"]

    }
	preferences {
		input(name:"serverIP",type:"string",title:"Server IP Address",defaultValue:"",required:true)
		input(name:"serverPort",type:"string",title:"Server Port",defaultValue:"5000",required:true)
		input(name:"serverPassword",type:"string",title:"Server Password",defaultValue:"",required:false)
		input(name:"loggingLevel",type:"enum",title:"Logging Level",description:"Set the level of logging.",options:["none","debug","trace","info","warn","error"],defaultValue:"debug",required:true)
    }
}

// *** [ Initialization Methods ] *********************************************
def installed() {
	def logprefix = "[installed] "
    logger(logprefix,"trace!")

    initialize()
}
def updated() {
	def logprefix = "[updated] "
	logger(logprefix,"trace!")
	initialize()
}
def initialize() {
	def logprefix = "[initialize] "
    logger(logprefix,"trace!")
    sendEvent(name: "numberOfButtons", value: 7, descriptionText: "set number of buttons to 8.", displayed: true)
}

// *** [ Device Methods ] *****************************************************
def off() {
	def logprefix = "[off] "
    logger(logprefix,"trace")
	sendCommandGet("/off")
    state.HVACstate = "off"
    sendEvent(name: "HVACstate", value: "off", descriptionText: "$device.displayName HVACstate $state.HVACstate", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 1,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}

def heatOn() {
	def logprefix = "[heatOn] "
    logger(logprefix,"trace")
	sendCommandGet("/HEAT/on")
    state.HVACstate = "heat"
    sendEvent(name: "HVACstate", value: "heating", descriptionText: "$device.displayName HVACstate $state.HVACstate", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 2,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)

}
def heatOff() {
	def logprefix = "[heatOff] "
    logger(logprefix,"trace")
	sendCommandGet("/HEAT/off")
    state.HVACstate = "idle"
    sendEvent(name: "HVACstate", value: "idle", descriptionText: "$device.displayName HVACstate $state.HVACstate", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 3,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}
def coolOn() {
	def logprefix = "[coolOn] "
    logger(logprefix,"trace")
	sendCommandGet("/COOL/on")
    state.HVACstate = "cool"
    sendEvent(name: "HVACstate", value: "cooling", descriptionText: "$device.displayName HVACstate $state.HVACstate", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 4,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}
def coolOff() {
	def logprefix = "[coolOff] "
    logger(logprefix,"trace")
	sendCommandGet("/COOL/off")
    state.HVACstate = "idle"
    sendEvent(name: "HVACstate", value: "idle", descriptionText: "$device.displayName HVACstate idle", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 5,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}
def fanOn() {
	def logprefix = "[fanOn] "
    logger(logprefix,"trace")
	sendCommandGet("/FAN/on")
    state.fan = "on"
    sendEvent(name: "fan", value: "on", descriptionText: "$device.displayName fan on", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 6,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}
def fanOff() {
	def logprefix = "[fanOff] "
    logger(logprefix,"trace")
	sendCommandGet("/FAN/off")
    state.fan = "off"
    sendEvent(name: "fan", value: "off", descriptionText: "$device.displayName fan off", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 7, descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
}
def nullo() {
    def logprefix = "[nothing] "
    logger(logprefix,"trace")
}
def push(button)		{
	//ifDebug("$button")

    //sendEvent(name: "pushed", value: button)

	switch(button)		{
		case 1:		off(); break //sendEvent("pushed", value: 1, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
		case 2:		heatOn(); break //sendEvent("pushed", value: 2, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
        case 3:		heatOff(); break //sendEvent("pushed", value: 3, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
        case 4:		coolOn(); break //sendEvent("pushed", value: 4, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
		case 5:		coolOff(); break //sendEvent("pushed", value: 5, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
        case 6:		fanOn(); break //sendEvent("pushed", value: 6, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
        case 7:		fanOff(); break //sendEvent("pushed", value: 7, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break

		default:    break //sendEvent(name: "pushableButton", value: button, descriptionText: "$device.displayName button $buton was pushed", displayed: true); break
	}
}


// *** [ Communication Methods ] **********************************************
def sendCommandGet(cmdDetails="") {
// def sendCommandGet(cmdDetails) {
    def logprefix = "[sendCommandGet] "
	//logger(logprefix+"cmdDetails:${cmdDetails}","trace")
	def getParams = [
		uri: "http://${serverIP}:${serverPort}/0${cmdDetails}",
		requestContentType: 'application/json',
		contentType: 'application/json'
	]
	logger(logprefix+getParams)
	asynchttpGet("sendCommandCallback", getParams, null)
}
def sendCommandCallback(response, data) {
	def logprefix = "[sendCommandCallback] "
    logger(logprefix+"response.status: ${response.status}","trace")
	if (response?.status == 200) {
		logger(logprefix+"response.data: ${response.data}")
		def jsonData = parseJson(response.data)
		if (jsonData?.ip4 || jsonData?.status == "OK") {
			logger(logprefix+"Updating last activity.")
			sendEvent([name:"refresh"])
		}
	}
}

// *** [ Logger ] *************************************************************
private logger(loggingText,loggingType="debug") {
	def internalLogging = false
	def internalLoggingSize = 500
	if (internalLogging) { if (!state.logger) {	state.logger = [] }	} else { state.logger = [] }

	loggingType = loggingType.toLowerCase()
	def forceLog = false
	if (loggingType.endsWith("!")) {
		forceLog = true
		loggingType = loggingType.substring(0, loggingType.length() - 1)
	}
	def loggingTypeList = ["trace","debug","warn","info","error"]
	if (!loggingTypeList.contains(loggingType)) { loggingType="debug" }
	if ((!loggingLevel||loggingLevel=="none") && loggingType == "error") {
	} else if (forceLog) {
	} else if (loggingLevel == "debug" || (loggingType == "error")) {
	} else if (loggingLevel == "trace" && (loggingType == "trace" || loggingType == "info")) {
	} else if (loggingLevel == "info"  && (loggingType == "info")) {
	} else if (loggingLevel == "warn"  && (loggingType == "warn")) {
	} else { loggingText = null }
	if (loggingText) {
		log."${loggingType}" loggingText
		if (internalLogging) {
			if (state.logger.size() >= internalLoggingSize) { state.logger.pop() }
			state.logger.push("<b>log.${loggingType}:</b>\t${loggingText}")
		}
	}
}
