#include <DHT.h>
#include <Wire.h>
#include <Adafruit_MLX90640.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <ArduinoJson.h>
#include <DFRobotDFPlayerMini.h>

// ─── PIN CONFIG (GaKuna node ESP32) ───────────
const int DHT_PIN      = 4;    // DHT22 temperature/humidity data (4.7–10k pull-up to 3.3V)
#define   DHT_TYPE     DHT22
const int LEFT_LED     = 18;   // Green LED #1 (left arrow) — via transistor
const int CROSS_LED    = 23;   // Red LED (stop/cross) — via transistor
const int RIGHT_LED    = 19;   // Green LED #2 (right arrow) — via transistor
const int DF_RX_PIN    = 16;   // DFPlayer TX → ESP32 RX (Serial2)
const int DF_TX_PIN    = 17;   // DFPlayer RX → ESP32 TX (Serial2, 1kΩ series)
const int MLX_SDA_PIN  = 21;   // MLX90640 SDA (I2C)
const int MLX_SCL_PIN  = 22;   // MLX90640 SCL (I2C)
const uint8_t MLX_ADDR = 0x33; // MLX90640 default I2C address

// ─── AUDIO TRACK MAPPING ─────────────────────
// Put these files on your SD card in /mp3/ folder:
// 0001.mp3 = "Go left."
// 0002.mp3 = "Go right."
// 0003.mp3 = "Do not proceed here."
// 0004.mp3 = "Go down."
// 0005.mp3 = "Go up."
// 0006.mp3 = "Go to the other side."
#define TRACK_LEFT   1
#define TRACK_RIGHT  2
#define TRACK_STOP   3
#define TRACK_DOWN   4
#define TRACK_UP     5
#define TRACK_OTHER_SIDE 6

// ─── NETWORK CONFIG ───────────────────────────
const char* SSID        = "gakunapi_wifi";
const char* PASSWORD    = "gakunapass";
const char* PI_IP       = "192.168.1.13";
const int   PI_PORT     = 5001;   // Must match ESP32_SERVER_PORT in server.py
const char* NODE_ID     = "node_1"; // Must match an ID in the server node registry

// ─── OBJECTS ──────────────────────────────────
DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_MLX90640 mlx;
float mlxFrame[32 * 24];          // 32x24 thermal frame (°C)
bool  mlxReady = false;
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
    else if (command == "DOWN")  dfPlayer.play(TRACK_DOWN);
    else if (command == "UP")    dfPlayer.play(TRACK_UP);
    else if (command == "OTHER_SIDE") dfPlayer.play(TRACK_OTHER_SIDE);
}

float readThermalMax() {
    // Returns hottest valid pixel temperature (°C), or NAN on failure
    if (!mlxReady) return NAN;
    if (mlx.getFrame(mlxFrame) != 0) {
        Serial.println("MLX90640 frame read failed!");
        return NAN;
    }
    float maxT = -1000.0;
    for (int i = 0; i < 32 * 24; i++) {
        float t = mlxFrame[i];
        if (isnan(t) || t < -40.0 || t > 300.0) continue;  // skip bad pixels
        if (t > maxT) maxT = t;
    }
    return (maxT > -1000.0) ? maxT : NAN;
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
    dht.begin();

    // MLX90640 thermal sensor (I2C)
    Wire.begin(MLX_SDA_PIN, MLX_SCL_PIN);
    Wire.setClock(400000);
    if (!mlx.begin(MLX_ADDR, &Wire)) {
        Serial.println("MLX90640 not found — check wiring!");
    } else {
        mlx.setMode(MLX90640_CHESS);
        mlx.setResolution(MLX90640_ADC_18BIT);
        mlx.setRefreshRate(MLX90640_8_HZ);
        mlxReady = true;
        Serial.println("MLX90640 ready.");
    }

    // LED pins
    pinMode(LEFT_LED,  OUTPUT);
    pinMode(RIGHT_LED, OUTPUT);
    pinMode(CROSS_LED, OUTPUT);

    // DFPlayer
    Serial2.begin(9600, SERIAL_8N1, DF_RX_PIN, DF_TX_PIN);
    Serial.println("Initializing DFPlayer...");
    if (!dfPlayer.begin(Serial2)) {
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

        // Read temperature (DHT22)
        float tempC = dht.readTemperature();
        if (isnan(tempC)) {
            Serial.println("DHT22 read failed!");
            tempC = -127.0;  // Keep payload valid for server.py
        }

        // Read thermal hotspot (MLX90640)
        float thermalMax = readThermalMax();

        // Build JSON — matches what server.py expects
        DynamicJsonDocument doc(8192);
        doc["node_id"] = NODE_ID;
        doc["temp"]    = tempC;
        doc["thermal"] = isnan(thermalMax) ? -127.0 : thermalMax;
        doc["seismic"] = 0.0;    // Placeholder — add MPU6050 reading here later
        doc["blocked"] = false;  // Placeholder — add HC-SR04 reading here later

        // Full 32x24 frame so the Pi can render a thermal preview
        if (mlxReady) {
            JsonArray tf = doc.createNestedArray("thermal_frame");
            for (int i = 0; i < 32 * 24; i++) {
                float t = mlxFrame[i];
                if (isnan(t) || t < -40.0 || t > 300.0) tf.add(-127);
                else                                     tf.add((int)roundf(t));
            }
        }

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
            if (command.startsWith("PLAY:")) {
                String trackText = command.substring(5);
                int track = trackText.toInt();
                if (track >= TRACK_LEFT && track <= TRACK_OTHER_SIDE &&
                    trackText == String(track)) {
                    dfPlayer.play(track);
                } else {
                    Serial.println("Invalid sound track.");
                }
            } else {
                setLEDs(command);
                playAudio(command);
            }
        }
    }
}