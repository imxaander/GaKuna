#include <OneWire.h>
#include <DallasTemperature.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <ArduinoJson.h>
#include <SoftwareSerial.h>
#include <DFRobotDFPlayerMini.h>

// ─── PIN CONFIG ───────────────────────────────
const int ONE_WIRE_BUS = 15;   // DS18B20 temperature sensor
const int LEFT_LED     = 25;    // Left arrow LED
const int CROSS_LED    = 26;    // Stop/Cross LED
const int RIGHT_LED    = 27;    // Right arrow LED
const int DF_RX_PIN    = 16;   // DFPlayer TX → ESP32 RX (GPIO16)
const int DF_TX_PIN    = 17;   // DFPlayer RX → ESP32 TX (GPIO17)

// ─── AUDIO TRACK MAPPING ─────────────────────
// Put these files on your SD card in /mp3/ folder:
// 0001.mp3 = "Go left."
// 0002.mp3 = "Go right."
// 0003.mp3 = "Do not proceed here."
// 0004.mp3 = "Evacuate immediately."
#define TRACK_LEFT   1
#define TRACK_RIGHT  2
#define TRACK_STOP   3
#define TRACK_ALARM  4

// ─── NETWORK CONFIG ───────────────────────────
const char* SSID        = "gakunapi_wifi";
const char* PASSWORD    = "gakunapass";
const char* PI_IP       = "10.42.0.1";
const int   PI_PORT     = 5001;   // Must match ESP32_SERVER_PORT in server.py

// ─── OBJECTS ──────────────────────────────────
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature tempSensor(&oneWire);
SoftwareSerial dfSerial(DF_RX_PIN, DF_TX_PIN);
DFRobotDFPlayerMini dfPlayer;
WiFiClient client;

// ─── STATE TRACKING ───────────────────────────
String lastCommand   = "";       // Avoid replaying audio on every loop
unsigned long lastSend = 0;      // Throttle sensor sends
const int SEND_INTERVAL = 1000;   // Send sensor data every 500ms

// ─── HELPERS ──────────────────────────────────
void setLEDs(String command) {
    digitalWrite(LEFT_LED,  LOW);
    digitalWrite(CROSS_LED, LOW);
    digitalWrite(RIGHT_LED, LOW);

    if      (command == "LEFT")  digitalWrite(LEFT_LED,  HIGH);
    else if (command == "RIGHT") digitalWrite(RIGHT_LED, HIGH);
    else if (command == "STOP")  digitalWrite(CROSS_LED, HIGH);
    // "OFF" = all stay LOW
}

void playAudio(String command) {
    // Only play if command actually changed
    if (command == lastCommand) return;
    lastCommand = command;

    if      (command == "LEFT")  dfPlayer.play(TRACK_LEFT);
    else if (command == "RIGHT") dfPlayer.play(TRACK_RIGHT);
    else if (command == "STOP")  dfPlayer.play(TRACK_STOP);
    else if (command == "OFF")   dfPlayer.stop();
}

void connectToWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(SSID, PASSWORD);
    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print(".");
        delay(200);
    }
    Serial.println("\nWiFi connected — IP: " + WiFi.localIP().toString());
}

void connectToPi() {
    Serial.print("Connecting to Pi...");
    while (!client.connect(PI_IP, PI_PORT)) {
        Serial.print(".");
        delay(1000);
    }
    Serial.println("\nConnected to Pi!");
}

// ─── SETUP ────────────────────────────────────
void setup() {
    Serial.begin(115200);

    // Temperature sensor
    tempSensor.begin();

    // LED pins
    pinMode(LEFT_LED,  OUTPUT);
    pinMode(RIGHT_LED, OUTPUT);
    pinMode(CROSS_LED, OUTPUT);

    // DFPlayer
    dfSerial.begin(9600);
    Serial.println("Initializing DFPlayer...");
    if (!dfPlayer.begin(dfSerial)) {
        Serial.println("DFPlayer not found — check wiring and SD card!");
    } else {
        Serial.println("DFPlayer ready.");
        dfPlayer.volume(25);  // 0-30
    }

    // WiFi + Pi connection
    connectToWiFi();
    connectToPi();

    // Startup indication
    setLEDs("OFF");
    Serial.println("GaKuna Node Ready.");
}

// ─── LOOP ─────────────────────────────────────
void loop() {
    // Reconnect if dropped
    if (!client.connected()) {
        Serial.println("Pi disconnected, reconnecting...");
        client.stop();
        lastCommand = "";  // Reset so audio replays after reconnect
        connectToPi();
    }

    // Send sensor data every SEND_INTERVAL ms
    unsigned long now = millis();
    if (now - lastSend >= SEND_INTERVAL) {
        lastSend = now;

        // Read temperature
        tempSensor.requestTemperatures();
        float tempC = tempSensor.getTempCByIndex(0);

        // Build JSON — matches what server.py expects
        StaticJsonDocument<128> doc;
        doc["temp"]    = tempC;
        doc["seismic"] = 0.0;    // Placeholder — add MPU6050 reading here later
        doc["blocked"] = false;  // Placeholder — add HC-SR04 reading here later

        String payload;
        serializeJson(doc, payload);
        payload += "\n";

        client.print(payload);
        Serial.println("Sent: " + payload);
    }

    // Check for command from Pi
    if (client.available()) {
        String command = client.readStringUntil('\n');
        command.trim();
        command.toUpperCase();

        if (command.length() > 0) {
            Serial.println("Command: " + command);
            setLEDs(command);
            //playAudio(command);
        }
    }
}