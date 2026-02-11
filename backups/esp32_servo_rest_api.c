 /*
 *  Simple hello world Json REST response
  *  by Mischianti Renzo <https://www.mischianti.org>
 *
 *  https://www.mischianti.org/
 *
 */
 
#include "Arduino.h"
#include "ArduinoOTA.h"
//#include <WiFi.h>
//#include <WiFiClient.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include "heltec.h"
//#include "images.h"
#include <ArduinoJson.h>
#include <ESP32Servo.h>

Servo myservo;  // create servo object to control a servo
// twelve servo objects can be created on most boards

// OTA Updates
boolean ENABLE_OTA = true;     // this will allow you to load firmware to the device over WiFi (see OTA for ESP8266)
String OTA_Password = "";      // Set an OTA password here -- leave blank if you don't want to be prompted for password


// GPIO the servo is attached to
static const int servoPin = 13;
 
const char* ssid = "easy";
const char* password = "YOUR_WIFI_PASSWORD";
 
WebServer server(80);

void WiFi_Connected(WiFiEvent_t event, WiFiEventInfo_t info){
  Serial.println("Connected to AP successfully!");
}

void Get_IPAddress(WiFiEvent_t event, WiFiEventInfo_t info){
  Serial.println("WIFI is connected!");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);
  Heltec.display->drawString(0, 0, "Server connected");
  Heltec.display->drawString(0, 26, WiFi.localIP().toString().c_str()); //String(WiFi.localIP()));
  Heltec.display->display();
}

void Wifi_disconnected(WiFiEvent_t event, WiFiEventInfo_t info){
  Serial.println("Disconnected from WIFI access point");
  Serial.print("WiFi lost connection. Reason: ");
  Serial.println(info.disconnected.reason);
  Serial.println("Reconnecting...");
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);
  Heltec.display->drawString(0, 0, "--connection lost--");
  Heltec.display->drawString(0, 26, String(info.disconnected.reason)); 
  Heltec.display->display();
  WiFi.begin(ssid, password);
}

// JSON data buffer
StaticJsonDocument<250> jsonDocument;
char buffer[250];
void create_json(char *tag, float value, char *unit) {  
  jsonDocument.clear();
  jsonDocument["type"] = tag;
  jsonDocument["value"] = value;
  jsonDocument["unit"] = unit;
  serializeJson(jsonDocument, buffer);  
}
 
void add_json_object(char *tag, float value, char *unit) {
  JsonObject obj = jsonDocument.createNestedObject();
  obj["type"] = tag;
  obj["value"] = value;
  obj["unit"] = unit; 
}
 
// Serving Hello world
void getHelloWord() {
    server.send(200, "text/json", "{\"name\": \"Hello world\"}");
    Heltec.display->clear(); 
    Heltec.display->drawString(0, 35, "hello world"); 
    Heltec.display->display(); 
}
// Serving Move
void getMove() {
    server.send(200, "text/json", "{\"name\": \"Move\"}");
    Heltec.display->clear(); 
    Heltec.display->drawString(0, 35, "move"); 
    Heltec.display->display(); 
} 

void handlePost() {
  if (server.hasArg("plain") == false) {
    //handle error here
  }
  //String body = server.arg("angle");
  //deserializeJson(jsonDocument, body);
  //int move = jsonDocument["move"];
  //String sender = jsonDocument["sender"];
  int angle =  server.arg("angle").toInt();
  Heltec.display->clear();
  //Heltec.display->drawString(0, 0, String(move));
  //Heltec.display->drawString(0, 15, sender);
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);
  Heltec.display->drawString(0, 0, "Server connected");
  Heltec.display->drawString(0, 26, WiFi.localIP().toString().c_str()); //String(WiFi.localIP()));
  Heltec.display->drawString(0, 38, "Angle");
  Heltec.display->drawString(50, 38, String(angle)); 
  Heltec.display->display();
  myservo.write(angle);
  // Respond to the client
  server.send(200, "application/json", "{}");
}
// Define routing
void restServerRouting() {
    server.on("/", HTTP_GET, []() {
        server.send(200, F("text/html"),
            F("Welcome to the REST Web Server"));
        Heltec.display->clear();
        Heltec.display->drawString(0, 35, "/"); 
        Heltec.display->display();    
    });
    server.on(F("/helloWorld"), HTTP_GET, getHelloWord);
    server.on(F("/move"), HTTP_POST, handlePost);
}
 
// Manage not found URL
void handleNotFound() {
  String message = "File Not Found\n\n";
  message += "URI: ";
  message += server.uri();
  message += "\nMethod: ";
  message += (server.method() == HTTP_GET) ? "GET" : "POST";
  message += "\nArguments: ";
  message += server.args();
  message += "\n";
  for (uint8_t i = 0; i < server.args(); i++) {
    message += " " + server.argName(i) + ": " + server.arg(i) + "\n";
  }
  server.send(404, "text/plain", message);
  Heltec.display->clear(); 
  Heltec.display->drawString(0, 35, "404"); 
  Heltec.display->display();
}
 
void setup(void) {
  Serial.begin(115200);
  //WiFi.mode(WIFI_STA);
  //WiFi.begin(ssid, password);
  //Serial.println("");
 
  // Wait for connection
  //while (WiFi.status() != WL_CONNECTED) {
  //  delay(500);
  //  Serial.print(".");
  
  Heltec.begin(true /*DisplayEnable Enable*/, false /*LoRa Disable*/, true /*Serial Enable*/);
  WiFi.disconnect(true);
  delay(1000);

  WiFi.onEvent(WiFi_Connected,SYSTEM_EVENT_STA_CONNECTED);
  WiFi.onEvent(Get_IPAddress, SYSTEM_EVENT_STA_GOT_IP);
  WiFi.onEvent(Wifi_disconnected, SYSTEM_EVENT_STA_DISCONNECTED); 
  WiFi.begin(ssid, password);
  Serial.println("Searching for WiFi network...");
  Serial.println("");
  Serial.print("Connected to ");
  Serial.println(ssid);
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
  Heltec.display->flipScreenVertically();
  Heltec.display->clear();
  Heltec.display->setFont(ArialMT_Plain_10);
  Heltec.display->drawString(0, 0, "Server started");
  Heltec.display->display();

  // Activate mDNS this is used to be able to connect to the server
  // with local DNS hostmane esp8266.local
  if (MDNS.begin("HVAC1stfloor")) {
    Serial.println("MDNS responder started");
  }

 if (ENABLE_OTA) {
    ArduinoOTA.onStart([]() {
      Serial.println("Start");
    });
    ArduinoOTA.onEnd([]() {
      Serial.println("\nEnd");
    });
    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
      Serial.printf("Progress: %u%%\r", (progress / (total / 100)));
    });
    ArduinoOTA.onError([](ota_error_t error) {
      Serial.printf("Error[%u]: ", error);
      if (error == OTA_AUTH_ERROR) Serial.println("Auth Failed");
      else if (error == OTA_BEGIN_ERROR) Serial.println("Begin Failed");
      else if (error == OTA_CONNECT_ERROR) Serial.println("Connect Failed");
      else if (error == OTA_RECEIVE_ERROR) Serial.println("Receive Failed");
      else if (error == OTA_END_ERROR) Serial.println("End Failed");
    });
    ArduinoOTA.setHostname("HVAC1stfloor"); 
    if (OTA_Password != "") {
      ArduinoOTA.setPassword(((const char *)OTA_Password.c_str()));
    }
    ArduinoOTA.begin();
  }
 myservo.attach(servoPin);  // attaches the servo on the servoPin to the servo object
  // Set server routing
  restServerRouting();
  // Set not found response
  server.onNotFound(handleNotFound);
  // Start server
  server.begin();
  Serial.println("HTTP server started");
  



  
}
 
void loop(void) {
  server.handleClient();
  
}