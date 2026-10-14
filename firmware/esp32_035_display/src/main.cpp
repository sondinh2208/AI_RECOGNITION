#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LovyanGFX.hpp>
#include <WiFi.h>

#include "secrets.h"

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
KioskState kiosk;
uint32_t lastStateFetch = 0;
uint32_t lastFrameFetch = 0;
uint32_t lastSuccessfulRequest = 0;
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

constexpr size_t kMaxJpegSize = 40960;
static uint8_t s_jpegBuffer[kMaxJpegSize];

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

void drawStatusPanel() {
  constexpr int kTextX = 65;
  constexpr int kMetricsX = 350;
  constexpr int kMetricsRight = 455;
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

  // Cot thong so co nen rieng de noi dung trang thai khong the de len FPS/mau quet.
  display.fillRect(kMetricsX, 250, 130, 70, kPanel);
  display.drawFastVLine(kMetricsX, 260, 50, 0x344054);

  display.setTextDatum(lgfx::textdatum_t::top_right);
  // Bao mau do khi toc do ve thuc te tut ro ret duoi muc tieu 4 FPS.
  const uint32_t fpsColor =
      renderedFpsValue > 0.0f && renderedFpsValue < 3.5f ? kRed : kMuted;
  display.setTextColor(fpsColor, kPanel);
  display.drawString(String(renderedFpsValue, 1) + " FPS", kMetricsRight, 264);
  if (kiosk.mode == "add_employee" && kiosk.sampleTarget > 0) {
    display.drawString(String(kiosk.sampleCount) + "/" + String(kiosk.sampleTarget), kMetricsRight, 292);
  } else if (kiosk.brightness > 0) {
    display.drawString(String(static_cast<int>(kiosk.brightness)) + "% SANG", kMetricsRight, 292);
  }
}

bool fetchAndDrawFrame() {
  const uint32_t requestStarted = millis();
  if (!frameHttpReady) {
    frameHttp.setConnectTimeout(800);
    frameHttp.setTimeout(1200);
    frameHttp.setReuse(true);
    frameClient.setNoDelay(true);
    frameClient.setTimeout(1200);
    static const char* headerKeys[] = {
        "X-FaceCheck-Mode", "X-FaceCheck-State", "X-FaceCheck-Title",
        "X-FaceCheck-Name", "X-FaceCheck-Employee", "X-FaceCheck-Fps",
        "X-FaceCheck-Brightness", "X-FaceCheck-Sample-Count",
        "X-FaceCheck-Sample-Target"};
    frameHttp.collectHeaders(headerKeys, sizeof(headerKeys) / sizeof(headerKeys[0]));
    if (!frameHttp.begin(frameClient, String(FACECHECK_SERVER) + "/frame.jpg")) {
      return false;
    }
    frameHttpReady = true;
  }

  frameHttp.addHeader("X-FaceCheck-Token", FACECHECK_DISPLAY_TOKEN);
  const int code = frameHttp.GET();
  if (code != HTTP_CODE_OK) {
    frameHttp.end();
    frameHttpReady = false;
    return false;
  }

  const String modeBeforeFrame = kiosk.mode;
  kiosk.mode = frameHttp.header("X-FaceCheck-Mode");
  kiosk.state = frameHttp.header("X-FaceCheck-State");
  kiosk.title = frameHttp.header("X-FaceCheck-Title");
  kiosk.name = frameHttp.header("X-FaceCheck-Name");
  kiosk.employeeId = frameHttp.header("X-FaceCheck-Employee");
  kiosk.fps = frameHttp.header("X-FaceCheck-Fps").toFloat();
  kiosk.brightness = frameHttp.header("X-FaceCheck-Brightness").toFloat();
  kiosk.sampleCount = frameHttp.header("X-FaceCheck-Sample-Count").toInt();
  kiosk.sampleTarget = frameHttp.header("X-FaceCheck-Sample-Target").toInt();
  const int length = frameHttp.getSize();
  WiFiClient* stream = frameHttp.getStreamPtr();
  const uint32_t headersReceived = millis();
  if (stream == nullptr || length <= 0 || static_cast<size_t>(length) > sizeof(s_jpegBuffer)) {
    frameHttp.end();
    frameHttpReady = false;
    return false;
  }

  const size_t received = stream->readBytes(s_jpegBuffer, length);
  const uint32_t jpegReceived = millis();
  bool drawn = false;
  if (received == static_cast<size_t>(length)) {
    const bool enrollment = isEnrollmentMode();
    if (modeBeforeFrame != kiosk.mode) {
      display.fillScreen(TFT_BLACK);
      frameAreaPrepared = false;
      previousStatusKey = "";
      previousMode = kiosk.mode;
    }
    if (!frameAreaPrepared) {
      display.fillScreen(TFT_BLACK);
      frameAreaPrepared = true;
      if (!enrollment) {
        drawStatusPanel();
      }
    }
    if (enrollment) {
      drawn = display.drawJpg(s_jpegBuffer, length, 0, 0, 480, 320);
    } else {
      drawn = display.drawJpg(s_jpegBuffer, length, 0, 0, 480, 250);
    }
    if (drawn && !enrollment) {
      String key = kiosk.mode + kiosk.state + kiosk.title + kiosk.name
          + kiosk.employeeId + String(kiosk.sampleCount);
      if (key != previousStatusKey) {
        previousStatusKey = key;
        drawStatusPanel();
      }
    }
  } else {
    // Neu doc byte thieu thi frame TCP bi lech, phai reset socket
    frameHttp.end();
    frameHttpReady = false;
  }
  const uint32_t frameDrawn = millis();
  // Giu TCP Keep-Alive, nhung moi GET luon lay JPEG moi nhat de khong tich frame cu.

  if (drawn) {
    const uint32_t now = millis();
    lastSuccessfulRequest = now;
    ++renderedFrameCount;
    totalHeaderMs += headersReceived - requestStarted;
    totalReadMs += jpegReceived - headersReceived;
    totalDrawMs += frameDrawn - jpegReceived;
    if (renderedWindowStarted == 0) renderedWindowStarted = now;
    const uint32_t elapsed = now - renderedWindowStarted;
    if (elapsed >= 2000) {
      const float renderedFps = renderedFrameCount * 1000.0f / elapsed;
      renderedFpsValue = renderedFps;
      Serial.printf("[DISPLAY] FPS=%.1f | HTTP=%lu ms | READ=%lu ms | DRAW=%lu ms | RSSI=%d | JPEG=%d B\n",
                    renderedFps,
                    totalHeaderMs / renderedFrameCount,
                    totalReadMs / renderedFrameCount,
                    totalDrawMs / renderedFrameCount,
                    WiFi.RSSI(), length);
      renderedFrameCount = 0;
      totalHeaderMs = 0;
      totalReadMs = 0;
      totalDrawMs = 0;
      renderedWindowStarted = now;
      // Cap nhat chi so FPS tren TFT moi cua so do, khong cho doi thay doi
      // trang thai nhan dien moi ve lai bang thong so.
      if (!isEnrollmentMode()) drawStatusPanel();
    }
  }
  return drawn;
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  frameHttp.end();
  frameHttpReady = false;
  WiFi.disconnect(true);
  delay(100);
  WiFi.mode(WIFI_STA);
  if (strlen(WIFI_PASSWORD) == 0) {
    WiFi.begin(WIFI_SSID);
  } else {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
  drawConnectionScreen("Dang ket noi Wi-Fi 2.4 GHz");
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(250);
  }
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.setSleep(false);
    Serial.printf("[WIFI] %s | RSSI=%d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    drawConnectionScreen("Wi-Fi OK, dang cho FaceCheck");
    lastSuccessfulRequest = millis();
  } else {
    Serial.printf("[WIFI] Khong ket noi duoc SSID: %s | status=%d\n", WIFI_SSID, WiFi.status());
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
  WiFi.mode(WIFI_STA);
  ensureWiFi();
  if (!isEnrollmentMode()) {
    drawStatusPanel();
  }
}

void loop() {
  ensureWiFi();
  const uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    delay(250);
    return;
  }

  // Dong bo muc tieu 4 FPS voi camera va khong tao request thua.
  if (now - lastFrameFetch >= 250) {
    lastFrameFetch = now;
    fetchAndDrawFrame();
  }

  const uint32_t connectionCheckNow = millis();
  if (lastSuccessfulRequest != 0 &&
      connectionCheckNow - lastSuccessfulRequest > 6000) {
    frameHttp.end();
    frameHttpReady = false;
    drawConnectionScreen("Mat ket noi FaceCheck");
    lastSuccessfulRequest = 0;
  }
  delay(3);
}
