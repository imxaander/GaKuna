#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include "esp_wifi.h"
#include <string.h>

// ─── CAMERA PINS (AI Thinker ESP32-CAM) ──────
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ─── NETWORK CONFIG ───────────────────────────
const char* SSID          = "gakunapi_wifi";
const char* PASSWORD      = "gakunapass";
const char* STREAM_HOST   = "192.168.1.13";
const uint16_t STREAM_PORT = 8081;
const char* CAMERA_ID     = "cam_1"; // Must match an ID in the server's CAMERA_CONFIG

// ─── WEB SERVER ───────────────────────────────
WebServer server(80);
WiFiClient streamClient;

void ensureStreamConnection() {
    if (streamClient.connected()) return;
    streamClient.stop();
    Serial.printf("[CAM] Connecting to frame stream %s:%u\n",
                  STREAM_HOST, STREAM_PORT);
    if (!streamClient.connect(STREAM_HOST, STREAM_PORT)) return;

    size_t idLength = strlen(CAMERA_ID);
    if (idLength == 0 || idLength > 32) {
        Serial.println("[CAM] Invalid CAMERA_ID length");
        streamClient.stop();
        return;
    }

    uint8_t idLengthByte = (uint8_t)idLength;
    if (streamClient.write(&idLengthByte, sizeof(idLengthByte)) != sizeof(idLengthByte) ||
        streamClient.write((const uint8_t*)CAMERA_ID, idLength) != idLength) {
        Serial.println("[CAM] Failed to send camera ID");
        streamClient.stop();
        return;
    }
    Serial.printf("[CAM] Stream connected as %s\n", CAMERA_ID);
}

void sendStreamFrame(camera_fb_t* fb) {
    if (!fb) return;
    ensureStreamConnection();
    if (!streamClient.connected()) return;
    if (fb->len == 0 || fb->len > 200000) {
        Serial.printf("[CAM] Skipping invalid frame size: %u\n", (unsigned)fb->len);
        return;
    }

    uint32_t length = fb->len;
    uint8_t header[4] = {
        (uint8_t)(length & 0xff),
        (uint8_t)((length >> 8) & 0xff),
        (uint8_t)((length >> 16) & 0xff),
        (uint8_t)((length >> 24) & 0xff)
    };
    if (streamClient.write(header, sizeof(header)) != sizeof(header) ||
        streamClient.write(fb->buf, fb->len) != fb->len) {
        Serial.println("[CAM] Frame send failed; reconnecting");
        streamClient.stop();
    }
}

// ─── CAMERA INIT ──────────────────────────────
void initCamera() {
    camera_config_t config = {};
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer   = LEDC_TIMER_0;
    config.pin_d0       = Y2_GPIO_NUM;
    config.pin_d1       = Y3_GPIO_NUM;
    config.pin_d2       = Y4_GPIO_NUM;
    config.pin_d3       = Y5_GPIO_NUM;
    config.pin_d4       = Y6_GPIO_NUM;
    config.pin_d5       = Y7_GPIO_NUM;
    config.pin_d6       = Y8_GPIO_NUM;
    config.pin_d7       = Y9_GPIO_NUM;
    config.pin_xclk     = XCLK_GPIO_NUM;
    config.pin_pclk     = PCLK_GPIO_NUM;
    config.pin_vsync    = VSYNC_GPIO_NUM;
    config.pin_href     = HREF_GPIO_NUM;
    config.pin_sscb_sda = SIOD_GPIO_NUM;
    config.pin_sscb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn     = PWDN_GPIO_NUM;
    config.pin_reset    = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240
    config.jpeg_quality = 20;

    if (psramFound()) {
        config.fb_count    = 1;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode   = CAMERA_GRAB_WHEN_EMPTY;
    } else {
        Serial.println("[CAM] WARNING: PSRAM not found — using one DRAM frame buffer");
        config.fb_count    = 1;
        config.fb_location = CAMERA_FB_IN_DRAM;
        config.grab_mode   = CAMERA_GRAB_WHEN_EMPTY;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("Camera init failed: 0x%x\n", err);
        while (true) delay(1000);
    }
    Serial.println("[CAM] Camera ready");
}

// ─── ROUTE: /capture ──────────────────────────
// Pi calls this to get one JPEG snapshot
void handleCapture() {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        server.send(503, "text/plain", "Camera capture failed");
        return;
    }

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send_P(200, "image/jpeg",
                  (const char*)fb->buf, fb->len);

    esp_camera_fb_return(fb);
    Serial.println("[CAM] Frame served");
}

// ─── ROUTE: / ─────────────────────────────────
// Simple status page
void handleRoot() {
    String ip = WiFi.localIP().toString();
    String html = "<h2>GaKuna ESP32-CAM</h2>";
    html += "<p>IP: " + ip + "</p>";
    html += "<p><a href='/capture'>Capture snapshot</a></p>";
    html += "<img src='/capture' style='max-width:100%'>";
    server.send(200, "text/html", html);
}

// ─── WIFI ─────────────────────────────────────
void connectToWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(SSID, PASSWORD);
    Serial.print("Connecting to WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print(".");
        delay(300);
    }
    Serial.println("\nWiFi connected!");
    Serial.println("Camera URL: http://" +
    WiFi.localIP().toString() + "/capture");

    // Reduce TX power
    WiFi.setTxPower(WIFI_POWER_8_5dBm);
}

// ─── SETUP ────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Serial.println("\nGaKuna ESP32-CAM Starting...");

    initCamera();
    connectToWiFi();

    server.on("/",        handleRoot);
    server.on("/capture", handleCapture);
    server.begin();

    Serial.println("[HTTP] Server started");
}

// ─── LOOP ─────────────────────────────────────
void loop() {
    // Reconnect WiFi if dropped
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi lost, reconnecting...");
        connectToWiFi();
        return;
    }

    server.handleClient();

    ensureStreamConnection();
    if (streamClient.connected()) {
        camera_fb_t* fb = esp_camera_fb_get();
        if (fb) {
            sendStreamFrame(fb);
            esp_camera_fb_return(fb);
        }
    }
    delay(100);
}
