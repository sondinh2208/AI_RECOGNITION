#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

#include "secrets.h"

#ifndef WIFI_SECONDARY_SSID
#define WIFI_SECONDARY_SSID ""
#endif

#ifndef WIFI_SECONDARY_PASSWORD
#define WIFI_SECONDARY_PASSWORD ""
#endif

#ifndef FACECHECK_SECONDARY_SERVER
#define FACECHECK_SECONDARY_SERVER FACECHECK_SERVER
#endif

namespace {

constexpr int kTftMosi = 13;
constexpr int kTftMiso = 12;
constexpr int kTftSclk = 14;
constexpr int kTftCs = 15;
constexpr int kTftDc = 2;
constexpr int kTftReset = -1;
constexpr int kBacklight = 27;
constexpr uint32_t kBackground = 0x101827;
constexpr uint32_t kPanel = 0x1D2939;
constexpr uint32_t kBlue = 0x2672FF;
constexpr uint32_t kGreen = 0x12B76A;
constexpr uint32_t kRed = 0xF04438;
constexpr uint32_t kOrange = 0xF79009;
constexpr uint32_t kMuted = 0x98A2B3;
constexpr uint32_t kTargetDisplayFps = 5;
constexpr uint32_t kFrameFetchIntervalMs = 1000 / kTargetDisplayFps;
constexpr int32_t kAttendanceImageWidth = 480;
constexpr int32_t kAttendanceImageHeight = 250;
constexpr int32_t kAttendanceImageX = 0;
constexpr int32_t kAttendanceImageY = 0;

class FaceCheckDisplay : public lgfx::LGFX_Device {
 public:
  FaceCheckDisplay() {
    auto busConfig = bus_.config();
    busConfig.spi_host = HSPI_HOST;
    busConfig.spi_mode = 0;
    // Day du 480x320 can bang thong SPI cao. Bo ST7796 nay dung duong noi
    // ngan tren PCB nen thu muc 80 MHz de dua toc do hien thi ve 8-10 FPS.
    busConfig.freq_write = 80000000;
    busConfig.freq_read = 16000000;
    busConfig.spi_3wire = true;
    busConfig.use_lock = true;
    busConfig.dma_channel = 1;
    busConfig.pin_sclk = kTftSclk;
    busConfig.pin_mosi = kTftMosi;
    busConfig.pin_miso = kTftMiso;
    busConfig.pin_dc = kTftDc;
    bus_.config(busConfig);
    panel_.setBus(&bus_);

    auto panelConfig = panel_.config();
    panelConfig.pin_cs = kTftCs;
    panelConfig.pin_rst = kTftReset;
    panelConfig.pin_busy = -1;
    panelConfig.memory_width = 320;
    panelConfig.memory_height = 480;
    panelConfig.panel_width = 320;
    panelConfig.panel_height = 480;
    panelConfig.offset_x = 0;
    panelConfig.offset_y = 0;
    panelConfig.offset_rotation = 0;
    panelConfig.readable = true;
    panelConfig.invert = false;
    panelConfig.rgb_order = false;
    panelConfig.dlen_16bit = false;
    panelConfig.bus_shared = true;
    panel_.config(panelConfig);

    auto lightConfig = light_.config();
    lightConfig.pin_bl = kBacklight;
    lightConfig.invert = false;
    lightConfig.freq = 44100;
    lightConfig.pwm_channel = 7;
    light_.config(lightConfig);
    panel_.setLight(&light_);
    setPanel(&panel_);
  }

 private:
  lgfx::Bus_SPI bus_;
#if defined(FACECHECK_PANEL_ILI9488)
  lgfx::Panel_ILI9488 panel_;
#else
  lgfx::Panel_ST7796 panel_;
#endif
  lgfx::Light_PWM light_;
};

struct KioskState {
  String mode = "idle";
  String state = "idle";
  String title = "DANG KET NOI";
  String message = "Khoi dong FaceCheck...";
  String name;
  String employeeId;
  float fps = 0;
  float brightness = 0;
  bool lightOk = false;
  int faces = 0;
  int sampleCount = 0;
  int sampleTarget = 0;
};

FaceCheckDisplay display;
String facecheckServer = FACECHECK_SERVER;
KioskState kiosk;
volatile uint32_t lastSuccessfulRequest = 0;
uint32_t renderedFrameCount = 0;
uint32_t renderedWindowStarted = 0;
float renderedFpsValue = 0.0f;
String previousStatusKey;
String previousMode = "idle";

bool isEnrollmentMode() {
  return kiosk.mode == "add_employee";
}
HTTPClient frameHttp;
WiFiClient frameClient;
bool frameHttpReady = false;
bool frameAreaPrepared = false;
uint32_t totalHeaderMs = 0;
uint32_t totalReadMs = 0;
uint32_t totalDrawMs = 0;
uint32_t maxHeaderMs = 0;
uint32_t maxReadMs = 0;
uint32_t maxDrawMs = 0;
volatile uint32_t droppedFrameCount = 0;
volatile uint32_t fetchFailureCount = 0;
volatile uint32_t fetchRetryCount = 0;
uint32_t reportedDroppedFrames = 0;
uint32_t reportedFetchFailures = 0;
uint32_t reportedFetchRetries = 0;

// Backend gioi han JPEG TFT duoi 16 KB. Hai slot 20 KB giu nguyen chat luong
// nhung tong RAM buffer van bang mot slot 40 KB cua firmware cu.
constexpr size_t kMaxJpegSize = 20000;

struct FrameSlot {
  uint8_t jpeg[kMaxJpegSize];
  size_t length = 0;
  uint32_t headerMs = 0;
  uint32_t readMs = 0;
  char mode[24] = {};
  char state[32] = {};
  char title[96] = {};
  char name[64] = {};
  char employeeId[32] = {};
  float fps = 0;
  float brightness = 0;
  int sampleCount = 0;
  int sampleTarget = 0;
};

static FrameSlot s_frameSlots[2];
QueueHandle_t freeFrameQueue = nullptr;
QueueHandle_t readyFrameQueue = nullptr;
SemaphoreHandle_t frameHttpMutex = nullptr;
TaskHandle_t frameFetchTaskHandle = nullptr;

void selectFaceCheckServer() {
  if (strlen(WIFI_SECONDARY_SSID) > 0 &&
      WiFi.SSID() == WIFI_SECONDARY_SSID) {
    facecheckServer = FACECHECK_SECONDARY_SERVER;
  } else {
    facecheckServer = FACECHECK_SERVER;
  }
  Serial.printf("[SERVER] SSID=%s | %s\n",
                WiFi.SSID().c_str(), facecheckServer.c_str());
}

bool faceCheckServerReachable(const String& server) {
  String address = server;
  if (address.startsWith("http://")) address.remove(0, 7);
  const int slash = address.indexOf('/');
  if (slash >= 0) address.remove(slash);
  uint16_t port = 80;
  const int colon = address.lastIndexOf(':');
  if (colon >= 0) {
    port = static_cast<uint16_t>(address.substring(colon + 1).toInt());
    address.remove(colon);
  }
  WiFiClient probe;
  probe.setTimeout(1200);
  const bool connected = probe.connect(address.c_str(), port, 1200);
  probe.stop();
  return connected;
}

bool connectConfiguredNetwork(const char* ssid, const char* password,
                              const char* server) {
  if (strlen(ssid) == 0) return false;
  WiFi.disconnect(true);
  delay(100);
  WiFi.mode(WIFI_STA);
  if (strlen(password) == 0) {
    WiFi.begin(ssid);
  } else {
    WiFi.begin(ssid, password);
  }
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 7000) {
    delay(250);
  }
  if (WiFi.status() != WL_CONNECTED) return false;
  if (!faceCheckServerReachable(server)) {
    Serial.printf("[WIFI] %s co song nhung khong thay FaceCheck tai %s\n",
                  ssid, server);
    return false;
  }
  facecheckServer = server;
  return true;
}

void centerText(const String& text, int y, int size, uint32_t color) {
  display.setFont(&fonts::Font0);
  display.setTextDatum(lgfx::textdatum_t::top_center);
  display.setTextSize(size);
  display.setTextColor(color, kBackground);
  display.drawString(text, display.width() / 2, y);
}

String fitTextToWidth(const String& source, int32_t maxWidth) {
  if (display.textWidth(source) <= maxWidth) return source;

  String fitted = source;
  while (fitted.length() > 0 && display.textWidth(fitted + "...") > maxWidth) {
    fitted.remove(fitted.length() - 1);
  }
  fitted.trim();
  return fitted.length() ? fitted + "..." : "";
}

bool isSuccessState() {
  return kiosk.state == "success" || kiosk.state == "duplicate" || kiosk.state == "ready" ||
         (kiosk.employeeId.length() > 0 && kiosk.state != "unknown" && kiosk.state != "ambiguous" && kiosk.state != "error");
}

uint32_t statusColor() {
  if (isSuccessState()) return kGreen;
  if (kiosk.state == "unknown" || kiosk.state == "ambiguous" || kiosk.state == "error") return kRed;
  if (kiosk.state == "validating" || kiosk.state == "strict_confirmation") return kOrange;
  return kBlue;
}

void drawConnectionScreen(const String& detail) {
  frameAreaPrepared = false;
  display.fillScreen(kBackground);
  centerText("FaceCheck", 42, 4, TFT_WHITE);
  display.fillCircle(240, 135, 28, kBlue);
  centerText("...", 119, 3, TFT_WHITE);
  centerText("DANG KET NOI LAPTOP", 184, 2, TFT_WHITE);
  centerText(detail, 222, 1, kMuted);
  centerText(WiFi.localIP().toString(), 252, 1, kMuted);
}

void drawMetricsPanel() {
  constexpr int kMetricsX = 350;
  constexpr int kMetricsRight = 455;
  // Chi ve lai cot thong so nho. Truoc day moi cua so do FPS lai ve ca
  // status panel 480x70, tao mot spike SPI khong nam trong so lieu DRAW.
  display.fillRect(kMetricsX, 250, 130, 70, kPanel);
  display.drawFastVLine(kMetricsX, 260, 50, 0x344054);
  display.setFont(&fonts::Font0);
  display.setTextSize(1);
  display.setTextDatum(lgfx::textdatum_t::top_right);
  const uint32_t fpsColor =
      renderedFpsValue > 0.0f &&
              renderedFpsValue < static_cast<float>(kTargetDisplayFps) - 0.5f
          ? kRed
          : kMuted;
  display.setTextColor(fpsColor, kPanel);
  display.drawString(String(renderedFpsValue, 1) + " FPS", kMetricsRight, 264);
  if (kiosk.mode == "add_employee" && kiosk.sampleTarget > 0) {
    display.drawString(String(kiosk.sampleCount) + "/" + String(kiosk.sampleTarget),
                       kMetricsRight, 292);
  } else if (kiosk.brightness > 0) {
    display.drawString(String(static_cast<int>(kiosk.brightness)) + "% SANG",
                       kMetricsRight, 292);
  }
}

void drawStatusPanel() {
  constexpr int kTextX = 65;
  constexpr int kMetricsX = 350;
  constexpr int kTextWidth = kMetricsX - kTextX - 10;
  const uint32_t color = statusColor();
  display.fillRect(0, 250, 480, 70, kPanel);
  display.fillRect(0, 250, 7, 70, color);
  display.fillCircle(35, 285, 20, color);
  display.setTextDatum(lgfx::textdatum_t::middle_center);
  display.setTextColor(TFT_WHITE, color);
  display.setTextSize(2);
  display.drawString(
      isSuccessState() ? "OK" :
      (kiosk.state == "unknown" || kiosk.state == "error" ? "X" :
      (kiosk.state == "validating" || kiosk.state == "strict_confirmation" ? "!" : "o")),
      35, 285);

  display.setFont(&fonts::Font2);
  display.setTextDatum(lgfx::textdatum_t::top_left);
  display.setTextColor(TFT_WHITE, kPanel);
  display.setTextSize(1);
  String title = kiosk.name.length() ? kiosk.name : kiosk.title;
  title = fitTextToWidth(title, kTextWidth);
  display.setClipRect(kTextX, 250, kTextWidth, 70);
  display.drawString(title, kTextX, 262);

  display.setFont(&fonts::Font0);
  display.setTextColor(kMuted, kPanel);
  display.setTextSize(1);
  String subtitle;
  if (kiosk.employeeId.length()) {
    subtitle = kiosk.employeeId;
  } else if (kiosk.mode == "add_employee") {
    subtitle = "GIU YEN TRONG KHUNG";
  } else {
    subtitle = "NHIN THANG VAO CAMERA";
  }
  subtitle = fitTextToWidth(subtitle, kTextWidth);
  display.drawString(subtitle, kTextX, 289);
  display.clearClipRect();

  drawMetricsPanel();
}

void copyHeader(char* destination, size_t capacity, const String& value) {
  if (capacity == 0) return;
  strlcpy(destination, value.c_str(), capacity);
}

bool fetchFrame(uint8_t slotIndex) {
  if (frameHttpMutex == nullptr ||
      xSemaphoreTake(frameHttpMutex, pdMS_TO_TICKS(1500)) != pdTRUE) {
    return false;
  }
  if (WiFi.status() != WL_CONNECTED) {
    xSemaphoreGive(frameHttpMutex);
    return false;
  }

  FrameSlot& slot = s_frameSlots[slotIndex];
  const uint32_t requestStarted = millis();
  if (!frameHttpReady) {
    // LAN binh thuong chi 50-70 ms. Khong de mot request hong khoa pipeline
    // 1.2 giay va lam mat 5-6 frame lien tiep.
    frameHttp.setConnectTimeout(150);
    frameHttp.setTimeout(150);
    frameHttp.setReuse(true);
    frameClient.setNoDelay(true);
    frameClient.setTimeout(150);
    static const char* headerKeys[] = {
        "X-FaceCheck-Mode", "X-FaceCheck-State", "X-FaceCheck-Title",
        "X-FaceCheck-Name", "X-FaceCheck-Employee", "X-FaceCheck-Fps",
        "X-FaceCheck-Brightness", "X-FaceCheck-Sample-Count",
        "X-FaceCheck-Sample-Target"};
    frameHttp.collectHeaders(headerKeys, sizeof(headerKeys) / sizeof(headerKeys[0]));
    if (!frameHttp.begin(frameClient, facecheckServer + "/frame.jpg")) {
      xSemaphoreGive(frameHttpMutex);
      return false;
    }
    frameHttpReady = true;
  }

  frameHttp.addHeader("X-FaceCheck-Token", FACECHECK_DISPLAY_TOKEN);
  const int code = frameHttp.GET();
  if (code != HTTP_CODE_OK) {
    frameHttp.end();
    frameHttpReady = false;
    xSemaphoreGive(frameHttpMutex);
    return false;
  }

  copyHeader(slot.mode, sizeof(slot.mode), frameHttp.header("X-FaceCheck-Mode"));
  copyHeader(slot.state, sizeof(slot.state), frameHttp.header("X-FaceCheck-State"));
  copyHeader(slot.title, sizeof(slot.title), frameHttp.header("X-FaceCheck-Title"));
  copyHeader(slot.name, sizeof(slot.name), frameHttp.header("X-FaceCheck-Name"));
  copyHeader(slot.employeeId, sizeof(slot.employeeId),
             frameHttp.header("X-FaceCheck-Employee"));
  slot.fps = frameHttp.header("X-FaceCheck-Fps").toFloat();
  slot.brightness = frameHttp.header("X-FaceCheck-Brightness").toFloat();
  slot.sampleCount = frameHttp.header("X-FaceCheck-Sample-Count").toInt();
  slot.sampleTarget = frameHttp.header("X-FaceCheck-Sample-Target").toInt();
  const int length = frameHttp.getSize();
  WiFiClient* stream = frameHttp.getStreamPtr();
  const uint32_t headersReceived = millis();
  if (stream == nullptr || length <= 0 ||
      static_cast<size_t>(length) > sizeof(slot.jpeg)) {
    frameHttp.end();
    frameHttpReady = false;
    xSemaphoreGive(frameHttpMutex);
    return false;
  }

  const size_t received = stream->readBytes(slot.jpeg, length);
  const uint32_t jpegReceived = millis();
  if (received != static_cast<size_t>(length)) {
    // Byte thieu lam HTTP keep-alive lech frame; tao lai socket o lan sau.
    frameHttp.end();
    frameHttpReady = false;
    xSemaphoreGive(frameHttpMutex);
    return false;
  }

  slot.length = received;
  slot.headerMs = headersReceived - requestStarted;
  slot.readMs = jpegReceived - headersReceived;
  lastSuccessfulRequest = jpegReceived;
  xSemaphoreGive(frameHttpMutex);
  return true;
}

void frameFetchTask(void* parameter) {
  (void)parameter;
  for (;;) {
    const uint32_t cycleStarted = millis();
    if (WiFi.status() == WL_CONNECTED) {
      uint8_t slotIndex = 0;
      if (xQueueReceive(freeFrameQueue, &slotIndex,
                        pdMS_TO_TICKS(kFrameFetchIntervalMs)) == pdTRUE) {
        bool fetched = fetchFrame(slotIndex);
        if (!fetched && WiFi.status() == WL_CONNECTED) {
          // Keep-alive cu thinh thoang bi server dong. fetchFrame da end()
          // socket loi; thu dung mot lan tren ket noi moi, khong retry vo han.
          ++fetchRetryCount;
          vTaskDelay(pdMS_TO_TICKS(1));
          fetched = fetchFrame(slotIndex);
        }
        if (fetched) {
          // Queue ready chi dai 1: frame moi ghi de frame cu chua kip ve.
          uint8_t staleIndex = 0;
          if (xQueueReceive(readyFrameQueue, &staleIndex, 0) == pdTRUE) {
            xQueueSend(freeFrameQueue, &staleIndex, 0);
            ++droppedFrameCount;
          }
          if (xQueueSend(readyFrameQueue, &slotIndex, 0) != pdTRUE) {
            xQueueSend(freeFrameQueue, &slotIndex, 0);
            ++droppedFrameCount;
          }
        } else {
          xQueueSend(freeFrameQueue, &slotIndex, 0);
          ++fetchFailureCount;
        }
      }
    }
    const uint32_t elapsed = millis() - cycleStarted;
    vTaskDelay(pdMS_TO_TICKS(
        elapsed < kFrameFetchIntervalMs ? kFrameFetchIntervalMs - elapsed : 1));
  }
}

bool drawFrame(uint8_t slotIndex) {
  FrameSlot& slot = s_frameSlots[slotIndex];
  const String modeBeforeFrame = kiosk.mode;
  kiosk.mode = slot.mode;
  kiosk.state = slot.state;
  kiosk.title = slot.title;
  kiosk.name = slot.name;
  kiosk.employeeId = slot.employeeId;
  kiosk.fps = slot.fps;
  kiosk.brightness = slot.brightness;
  kiosk.sampleCount = slot.sampleCount;
  kiosk.sampleTarget = slot.sampleTarget;

  if (modeBeforeFrame != kiosk.mode) {
    display.fillScreen(TFT_BLACK);
    frameAreaPrepared = false;
    previousStatusKey = "";
    previousMode = kiosk.mode;
  }
  const bool enrollment = isEnrollmentMode();
  if (!frameAreaPrepared) {
    display.fillScreen(TFT_BLACK);
    frameAreaPrepared = true;
    if (!enrollment) drawStatusPanel();
  }

  const uint32_t drawStarted = millis();
  bool drawn = enrollment
      ? display.drawJpg(slot.jpeg, slot.length, 0, 0, 480, 320)
      : display.drawJpg(slot.jpeg, slot.length,
                        kAttendanceImageX, kAttendanceImageY,
                        kAttendanceImageWidth, kAttendanceImageHeight);
  if (drawn && !enrollment) {
    String key = kiosk.mode + kiosk.state + kiosk.title + kiosk.name
        + kiosk.employeeId + String(kiosk.sampleCount);
    if (key != previousStatusKey) {
      previousStatusKey = key;
      drawStatusPanel();
    }
  }
  const uint32_t drawMs = millis() - drawStarted;

  if (drawn) {
    const uint32_t now = millis();
    ++renderedFrameCount;
    totalHeaderMs += slot.headerMs;
    totalReadMs += slot.readMs;
    totalDrawMs += drawMs;
    maxHeaderMs = max(maxHeaderMs, slot.headerMs);
    maxReadMs = max(maxReadMs, slot.readMs);
    maxDrawMs = max(maxDrawMs, drawMs);
    if (renderedWindowStarted == 0) renderedWindowStarted = now;
    const uint32_t elapsed = now - renderedWindowStarted;
    if (elapsed >= 2000) {
      const float renderedFps = renderedFrameCount * 1000.0f / elapsed;
      renderedFpsValue = renderedFps;
      const uint32_t dropped = droppedFrameCount - reportedDroppedFrames;
      const uint32_t failures = fetchFailureCount - reportedFetchFailures;
      const uint32_t retries = fetchRetryCount - reportedFetchRetries;
      Serial.printf("[DISPLAY] FPS=%.1f | HTTP=%lu/%lu ms | READ=%lu/%lu ms | "
                    "DRAW=%lu/%lu ms | DROP=%lu | RETRY=%lu | FAIL=%lu | "
                    "RSSI=%d | JPEG=%u B\n",
                    renderedFps,
                    totalHeaderMs / renderedFrameCount,
                    maxHeaderMs,
                    totalReadMs / renderedFrameCount,
                    maxReadMs,
                    totalDrawMs / renderedFrameCount,
                    maxDrawMs,
                    dropped, retries, failures, WiFi.RSSI(),
                    static_cast<unsigned int>(slot.length));
      reportedDroppedFrames = droppedFrameCount;
      reportedFetchFailures = fetchFailureCount;
      reportedFetchRetries = fetchRetryCount;
      renderedFrameCount = 0;
      totalHeaderMs = 0;
      totalReadMs = 0;
      totalDrawMs = 0;
      maxHeaderMs = 0;
      maxReadMs = 0;
      maxDrawMs = 0;
      renderedWindowStarted = now;
      if (!isEnrollmentMode()) drawMetricsPanel();
    }
  }
  return drawn;
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (frameHttpMutex != nullptr) {
    xSemaphoreTake(frameHttpMutex, portMAX_DELAY);
  }
  frameHttp.end();
  frameHttpReady = false;
  bool connected = connectConfiguredNetwork(
      WIFI_SSID, WIFI_PASSWORD, FACECHECK_SERVER);
  if (!connected) {
    connected = connectConfiguredNetwork(
        WIFI_SECONDARY_SSID, WIFI_SECONDARY_PASSWORD,
        FACECHECK_SECONDARY_SERVER);
  }
  if (frameHttpMutex != nullptr) {
    xSemaphoreGive(frameHttpMutex);
  }
  drawConnectionScreen("Dang ket noi Wi-Fi 2.4 GHz");
  if (connected && WiFi.status() == WL_CONNECTED) {
    WiFi.setSleep(false);
    Serial.printf("[WIFI] %s | %s | RSSI=%d dBm\n",
                  WiFi.SSID().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI());
    drawConnectionScreen("Wi-Fi OK, dang cho FaceCheck");
    lastSuccessfulRequest = millis();
  } else {
    Serial.printf("[WIFI] Khong ket noi duoc mang da cau hinh | status=%d\n", WiFi.status());
    drawConnectionScreen("Khong ket noi duoc Wi-Fi");
    int n = WiFi.scanNetworks();
    Serial.printf("[WIFI SCAN] Tim thay %d mang:\n", n);
    for (int i = 0; i < n; ++i) {
      Serial.printf("  %d: %s (%d dBm, ch %d)\n", i, WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i));
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  display.init();
  display.setRotation(1);
  display.setBrightness(220);
  drawConnectionScreen("Khoi dong bo man hinh");
  frameHttpMutex = xSemaphoreCreateMutex();
  freeFrameQueue = xQueueCreate(2, sizeof(uint8_t));
  readyFrameQueue = xQueueCreate(1, sizeof(uint8_t));
  if (frameHttpMutex == nullptr || freeFrameQueue == nullptr ||
      readyFrameQueue == nullptr) {
    Serial.println("[PIPELINE] Khong du RAM de tao queue/mutex.");
    while (true) delay(1000);
  }
  uint8_t firstSlot = 0;
  uint8_t secondSlot = 1;
  xQueueSend(freeFrameQueue, &firstSlot, 0);
  xQueueSend(freeFrameQueue, &secondSlot, 0);
  WiFi.mode(WIFI_STA);
  ensureWiFi();
  if (!isEnrollmentMode()) {
    drawStatusPanel();
  }
  const BaseType_t taskCreated = xTaskCreatePinnedToCore(
      // Wi-Fi/TCP cua ESP-IDF da chay tren Core 0. Dat HTTPClient len Core 0
      // co the khoa IDLE0 khi socket gap loi, nen pipeline ung dung o Core 1.
      frameFetchTask, "tft_fetch", 6144, nullptr, 1,
      &frameFetchTaskHandle, 1);
  if (taskCreated != pdPASS) {
    frameFetchTaskHandle = nullptr;
    Serial.println("[PIPELINE] Khong tao duoc task tai JPEG.");
    while (true) delay(1000);
  }
  Serial.println("[PIPELINE] Core 0 Wi-Fi/TCP | Core 1 fetch+draw | 2 buffer latest-frame");
}

void loop() {
  ensureWiFi();
  if (WiFi.status() != WL_CONNECTED) {
    delay(250);
    return;
  }

  uint8_t readySlot = 0;
  if (xQueueReceive(readyFrameQueue, &readySlot, pdMS_TO_TICKS(5)) == pdTRUE) {
    drawFrame(readySlot);
    xQueueSend(freeFrameQueue, &readySlot, portMAX_DELAY);
  }

  const uint32_t connectionCheckNow = millis();
  if (lastSuccessfulRequest != 0 &&
      connectionCheckNow - lastSuccessfulRequest > 6000) {
    xSemaphoreTake(frameHttpMutex, portMAX_DELAY);
    frameHttp.end();
    frameHttpReady = false;
    xSemaphoreGive(frameHttpMutex);
    drawConnectionScreen("Mat ket noi FaceCheck");
    lastSuccessfulRequest = 0;
    WiFi.disconnect(true);
  }
  delay(3);
}
