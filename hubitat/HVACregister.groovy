// VERSION: 0.1

metadata {
    definition (name: "HVAC register driver", namespace: "dimenstein", author: "Simon Dimenstein", importUrl: "") {

//       capability "Actuator"
// for hubitat comment the next line and uncomment the one after that is currently commented
//		capability "Button"
		capability "PushableButton"		// hubitat changed `Button` to `PushableButton`  2018-04-20
		capability "Sensor"
		capability "SwitchLevel"

		capability "Health Check"
        
		attribute "HVACRegisterState", "enum", ['on', 'off']
        attribute "value", "number"
        attribute "level", "number"


		
		command "off"
		command "on"
		
        command "setLevel(value)", [value]
		
    }
	preferences {
		input(name:"serverIP",type:"string",title:"Server IP Address",defaultValue:"",required:true)
		input(name:"serverPort",type:"string",title:"Server Port",defaultValue:"80",required:true)
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
	sendCommandPost("angle=0")
    state.HVACRegisterState = "off"
    state.value = 0
    state.level = 0
    sendEvent(name: "HVACRegisterState", value: "off", descriptionText: "$device.displayName HVACRegisterState $state.HVACRegisterState", displayed: true, isStateChange: true)
    sendEvent(name: "pushed", value: 1,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
    sendEvent(name: "value", value: 0, descriptionText: "$device.displayName value $state.value", displayed: true, isStateChange: true)
    sendEvent(name: "level", value: 0, descriptionText: "$device.displayName value $state.level", displayed: true, isStateChange: true)
   
}

def on() {
	def logprefix = "[on] "
    logger(logprefix,"trace")
	sendCommandPost("angle=45")
    state.HVACRegisterState = "heat"
    state.value = 45
    state.level = 45
    sendEvent(name: "HVACRegisterState", value: "on", descriptionText: "$device.displayName HVACRegisterState $state.HVACRegisterState", displayed: true, isStateChange: true)
    sendEvent(name: "pushed", value: 2,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
    sendEvent(name: "value", value: 45, descriptionText: "$device.displayName value $state.value", displayed: true, isStateChange: true)
    sendEvent(name: "level", value: 45, descriptionText: "$device.displayName value $state.level", displayed: true, isStateChange: true)

}

def setLevel(value) {
	def logprefix = "[setLevel] "
    logger(logprefix,"trace")
	sendCommandPost("angle=$value")
    state.HVACRegisterState = "value"
    state.value = value
    state.level = value
    sendEvent(name: "HVACRegisterState", value: value, descriptionText: "$device.displayName HVACRegisterState $state.HVACRegisterState", displayed: true, isStateChange: true)
    //sendEvent(name: "pushed", value: 1,  descriptionText: "$device.displayName button $pushed was pushed", displayed: true, isStateChange: true)
    sendEvent(name: "value", value: value, descriptionText: "$device.displayName value $state.value", displayed: true, isStateChange: true)
    sendEvent(name: "level", value: value, descriptionText: "$device.displayName value $state.level", displayed: true, isStateChange: true)
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
		case 2:		on(); break //sendEvent("pushed", value: 2, descriptionText: "$device.displayName button $button was pushed", displayed: true,isStateChange: true);		break
        
	
		default:    sendEvent(name: "pushableButton", value: buton, descriptionText: "$device.displayName button $buton was pushed", displayed: true); break
	}
}


// *** [ Communication Methods ] **********************************************
def sendCommandPost(cmdDetails="") {
	def logprefix = "[sendCommandGet] "
	logger(logprefix+"cmdDetails:${cmdDetails}","trace")
	def postParams = [
		uri: "http://${serverIP}:${serverPort}/move?${cmdDetails}",
		requestContentType: 'application/json',
		contentType: 'application/json'
	]
	logger(logprefix+postParams)
	asynchttpPost("sendCommandCallback", postParams, null)
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
