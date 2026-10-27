#pragma once

#include <Arduino.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

namespace FaceCheckWifi {

struct Config {
  String ssid;
  String password;
  String server;
  bool saved = false;
};

inline String htmlEscape(const String& value) {
  String escaped;
  escaped.reserve(value.length() + 8);
  for (size_t index = 0; index < value.length(); ++index) {
    switch (value[index]) {
      case '&': escaped += F("&amp;"); break;
      case '<': escaped += F("&lt;"); break;
      case '>': escaped += F("&gt;"); break;
      case '"': escaped += F("&quot;"); break;
      case '\'': escaped += F("&#39;"); break;
      default: escaped += value[index]; break;
    }
  }
  return escaped;
}

inline Config load(const char* fallbackSsid, const char* fallbackPassword,
                   const char* fallbackServer = "") {
  Preferences preferences;
  Config config;
  preferences.begin("facecheck_wifi", true);
  config.saved = preferences.getBool("configured", false);
  config.ssid = preferences.getString("ssid", fallbackSsid ? fallbackSsid : "");
  config.password = preferences.getString(
      "password", fallbackPassword ? fallbackPassword : "");
  config.server = preferences.getString(
      "server", fallbackServer ? fallbackServer : "");
  preferences.end();
  return config;
}

inline void save(const String& ssid, const String& password,
                 const String& server) {
  Preferences preferences;
  preferences.begin("facecheck_wifi", false);
  preferences.putString("ssid", ssid);
  preferences.putString("password", password);
  preferences.putString("server", server);
  preferences.putBool("configured", true);
  preferences.end();
}

inline bool connect(const Config& config, uint32_t timeoutMs = 12000) {
  if (config.ssid.isEmpty()) return false;
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.disconnect(false);
  delay(100);
  if (config.password.isEmpty()) {
    WiFi.begin(config.ssid.c_str());
  } else {
    WiFi.begin(config.ssid.c_str(), config.password.c_str());
  }
  Serial.printf("[WIFI] Dang ket noi %s", config.ssid.c_str());
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < timeoutMs) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

inline String setupSsid(const char* deviceName) {
  const uint64_t chip = ESP.getEfuseMac();
  char suffix[5];
  snprintf(suffix, sizeof(suffix), "%04X",
           static_cast<unsigned int>(chip & 0xFFFF));
  String name = "FaceTouchPro-Setup-";
  name += deviceName && strlen(deviceName) ? deviceName : "ESP32";
  name += '-';
  name += suffix;
  if (name.length() > 31) name.remove(31);
  return name;
}

[[noreturn]] inline void startPortal(const char* deviceName,
                                     const String& currentServer = "") {
  WiFi.disconnect(true);
  delay(150);
  WiFi.mode(WIFI_AP_STA);
  const IPAddress address(192, 168, 4, 1);
  WiFi.softAPConfig(address, address, IPAddress(255, 255, 255, 0));
  const String accessPoint = setupSsid(deviceName);
  constexpr char kSetupPassword[] = "FaceCheck2026";
  if (!WiFi.softAP(accessPoint.c_str(), kSetupPassword)) {
    Serial.println(F("[SETUP] Khong tao duoc Wi-Fi cau hinh; khoi dong lai."));
    delay(2000);
    ESP.restart();
  }

  DNSServer dns;
  WebServer web(80);
  dns.start(53, "*", address);
  const bool autoServer = strcmp(deviceName, "Display") == 0;
  const bool showServer = !autoServer && (currentServer.startsWith("http://") ||
                          currentServer.startsWith("https://"));

  auto renderForm = [&web, &currentServer, &accessPoint, &showServer, &autoServer]() {
    int count = WiFi.scanNetworks(false, true);
    String options;
    String previous;
    for (int index = 0; index < count; ++index) {
      const String ssid = WiFi.SSID(index);
      if (ssid.isEmpty() || ssid == previous) continue;
      previous = ssid;
      options += F("<option value=\"");
      options += htmlEscape(ssid);
      options += F("\"></option>");
    }
    WiFi.scanDelete();
    String page = F(
        "<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>FaceTouch Pro - Cau hinh Wi-Fi</title><style>"
        "body{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}"
        "main{max-width:480px;margin:auto;background:#1e293b;padding:24px;border-radius:16px}"
        "h1{font-size:22px}label{display:block;margin-top:16px}"
        "input,select,button{box-sizing:border-box;width:100%;padding:12px;margin-top:6px;"
        "border-radius:9px;border:1px solid #475569;font-size:16px}"
        "button{background:#2563eb;color:white;border:0;font-weight:700;margin-top:22px}"
        "small{color:#94a3b8}</style></head><body><main>"
        "<h1>Cau hinh mang FaceTouch Pro</h1><p>Chon Wi-Fi 2.4 GHz cho thiet bi.</p>"
        "<form method=\"post\" action=\"/save\"><label>Ten Wi-Fi</label>"
        "<input name=\"ssid\" list=\"networks\" maxlength=\"32\" required>"
        "<datalist id=\"networks\">");
    page += options;
    page += F("</datalist><label>Mat khau</label><input name=\"password\" "
              "type=\"password\" maxlength=\"63\" autocomplete=\"new-password\">");
    if (showServer) {
      page += F("<label>May chu FaceTouch Pro</label>"
                "<input name=\"server\" maxlength=\"128\" value=\"");
      page += htmlEscape(currentServer);
      page += F("\" placeholder=\"http://facecheck.local:8767\">");
    }
    if (autoServer) {
      page += F("<p>Man hinh se tu tim laptop chay FaceTouch Pro tren cung Wi-Fi. "
                "Khong can nhap dia chi IP may chu.</p>");
    }
    page += F("<button type=\"submit\">Luu va ket noi</button></form>"
              "<p><small>Mang cai dat: ");
    page += htmlEscape(accessPoint);
    page += F("</small></p></main></body></html>");
    web.send(200, "text/html; charset=utf-8", page);
  };

  web.on("/", HTTP_GET, renderForm);
  web.on("/save", HTTP_POST, [&web, &currentServer, &showServer]() {
    String ssid = web.arg("ssid");
    String password = web.arg("password");
    String server = showServer ? web.arg("server") : currentServer;
    ssid.trim();
    server.trim();
    if (ssid.isEmpty() || ssid.length() > 32 || password.length() > 63 ||
        server.length() > 128) {
      web.send(400, "text/plain; charset=utf-8", "Thong tin khong hop le.");
      return;
    }
    if (server.isEmpty()) server = currentServer;
    save(ssid, password, server);
    web.send(200, "text/html; charset=utf-8",
             "<!doctype html><meta charset=utf-8><meta name=viewport "
             "content='width=device-width'><h2>Da luu cau hinh</h2>"
             "<p>Thiet bi dang khoi dong lai va ket noi Wi-Fi moi.</p>");
    delay(700);
    ESP.restart();
  });
  web.onNotFound([&web]() {
    web.sendHeader("Location", "http://192.168.4.1/", true);
    web.send(302, "text/plain", "");
  });
  web.begin();

  Serial.printf("[SETUP] Ket noi Wi-Fi '%s' (mat khau: %s)\n",
                accessPoint.c_str(), kSetupPassword);
  Serial.println(F("[SETUP] Mo http://192.168.4.1 de chon mang moi."));
  while (true) {
    if (esp_task_wdt_status(nullptr) == ESP_OK) esp_task_wdt_reset();
    dns.processNextRequest();
    web.handleClient();
    delay(2);
  }
}

// EN resets do not reliably preserve RTC RAM on every board. Use a short
// NVS marker instead, cleared before any camera/network/peripheral startup.
// Only the second reset inside this window requests provisioning.
inline void checkDoubleReset(const char* deviceName,
                             const char* fallbackServer = "") {
  Preferences preferences;
  if (!preferences.begin("fc_reset", false)) {
    Serial.println(F("[SETUP] Khong mo duoc NVS cho double reset."));
    return;
  }
  const bool secondReset = preferences.getBool("armed", false);
  if (secondReset) {
    preferences.putBool("armed", false);
    preferences.end();
    Serial.println(F("[SETUP] Double RESET: mo trang doi Wi-Fi."));
    const Config config = load("", "", fallbackServer);
    startPortal(deviceName, config.server);
  }
  preferences.putBool("armed", true);
  preferences.end();
  Serial.println(F("[SETUP] Nhan RESET lan nua trong 3 giay de doi Wi-Fi."));
  delay(3000);
  if (preferences.begin("fc_reset", false)) {
    preferences.putBool("armed", false);
    preferences.end();
  }
}

inline Config connectOrProvision(const char* deviceName,
                                 const char* fallbackSsid,
                                 const char* fallbackPassword,
                                 const char* fallbackServer = "",
                                 uint32_t timeoutMs = 12000) {
  Config config = load(fallbackSsid, fallbackPassword, fallbackServer);
  if (connect(config, timeoutMs)) return config;
  if (config.saved && fallbackSsid && strlen(fallbackSsid) &&
      config.ssid != fallbackSsid) {
    Config fallback;
    fallback.ssid = fallbackSsid;
    fallback.password = fallbackPassword ? fallbackPassword : "";
    fallback.server = fallbackServer ? fallbackServer : "";
    if (connect(fallback, timeoutMs)) return fallback;
  }
  startPortal(deviceName, config.server);
}

}  // namespace FaceCheckWifi
