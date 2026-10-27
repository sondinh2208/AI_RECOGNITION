#pragma once

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <mbedtls/md.h>

// UDP first, then a bounded, incremental TCP search when broadcast is filtered.
// A signed challenge is checked before sending the display token or accepting frames.
class ServerDiscovery {
 public:
  explicit ServerDiscovery(const char* token) : token_(token) {}

  void restart() {
    udp_.stop();
    udp_.begin(0);
    char nonce[33];
    snprintf(nonce, sizeof(nonce), "%08lx%08lx%08lx%08lx",
             (unsigned long)esp_random(), (unsigned long)esp_random(),
             (unsigned long)esp_random(), (unsigned long)esp_random());
    nonce_ = nonce;
    const uint32_t local = number(WiFi.localIP());
    const uint32_t mask = number(WiFi.subnetMask());
    // Never scan more than the local /24, even on a large enterprise subnet.
    const uint32_t boundedMask = mask | 0xffffff00U;
    first_ = (local & boundedMask) + 1;
    last_ = (local | ~boundedMask) - 1;
    cursor_ = first_;
    broadcast_ = address(local | ~mask);
    nextBroadcast_ = nextScan_ = nextCurrent_ = 0;
  }

  String poll(const String& current, bool needsServer) {
    if (!needsServer || WiFi.status() != WL_CONNECTED) return "";
    const uint32_t now = millis();
    int packet = udp_.parsePacket();
    if (packet > 0) {
      char payload[513];
      IPAddress source = udp_.remoteIP();
      const int length = udp_.read(payload, sizeof(payload) - 1);
      if (length > 0 && packet <= 512 && sameSubnet(source)) {
        payload[length] = 0;
        if (verified(payload)) return url(source);
      }
    }
    if (due(now, nextBroadcast_)) {
      nextBroadcast_ = now + 5000;
      udp_.beginPacket(broadcast_, 8768);
      udp_.print("FACECHECK_DISCOVER_V1 ");
      udp_.print(nonce_);
      udp_.endPacket();
    }
    if (due(now, nextCurrent_)) {
      nextCurrent_ = now + 10000;
      if (probe(current)) return current;
    }
    if (due(now, nextScan_) && first_ <= last_) {
      nextScan_ = now + 300;
      IPAddress candidate = address(cursor_);
      cursor_ = cursor_ >= last_ ? first_ : cursor_ + 1;
      if (candidate != WiFi.localIP() && probe(url(candidate))) return url(candidate);
    }
    return "";
  }

 private:
  const char* token_;
  WiFiUDP udp_;
  String nonce_;
  IPAddress broadcast_;
  uint32_t first_ = 1, last_ = 0, cursor_ = 1;
  uint32_t nextBroadcast_ = 0, nextScan_ = 0, nextCurrent_ = 0;

  static bool due(uint32_t now, uint32_t deadline) {
    return deadline == 0 || static_cast<int32_t>(now - deadline) >= 0;
  }
  static uint32_t number(IPAddress ip) {
    return (uint32_t(ip[0]) << 24) | (uint32_t(ip[1]) << 16) |
           (uint32_t(ip[2]) << 8) | uint32_t(ip[3]);
  }
  static IPAddress address(uint32_t value) {
    return IPAddress(value >> 24, value >> 16, value >> 8, value);
  }
  static String url(IPAddress ip) {
    return "http://" + ip.toString() + ":8767";
  }
  bool sameSubnet(IPAddress ip) {
    uint32_t mask = number(WiFi.subnetMask());
    return (number(ip) & mask) == (number(WiFi.localIP()) & mask);
  }
  bool verified(const char* payload) {
    JsonDocument doc;
    if (deserializeJson(doc, payload)) return false;
    if (String(doc["service"] | "") != "FaceCheck TFT display" ||
        String(doc["nonce"] | "") != nonce_ || (doc["port"] | 0) != 8767) return false;
    String received = doc["signature"] | "";
    if (!strlen(token_) || received.length() != 64) return false;
    String message = "facecheck-display-v1|" + nonce_ + "|8767";
    unsigned char digest[32];
    if (mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),
                        reinterpret_cast<const unsigned char*>(token_), strlen(token_),
                        reinterpret_cast<const unsigned char*>(message.c_str()),
                        message.length(), digest) != 0) return false;
    const char hex[] = "0123456789abcdef";
    unsigned char difference = 0;
    for (size_t i = 0; i < 32; ++i) {
      difference |= received[i * 2] ^ hex[digest[i] >> 4];
      difference |= received[i * 2 + 1] ^ hex[digest[i] & 15];
    }
    return difference == 0;
  }
  bool probe(const String& server) {
    if (!server.startsWith("http://")) return false;
    WiFiClient client;
    HTTPClient http;
    http.setConnectTimeout(120);
    http.setTimeout(250);
    http.setReuse(false);
    if (!http.begin(client, server + "/discover?nonce=" + nonce_)) return false;
    bool valid = false;
    const int status = http.GET();
    if (status == 200 && http.getSize() >= 0 && http.getSize() <= 512) {
      String payload = http.getString();
      valid = verified(payload.c_str());
    }
    if (status > 0) {
      Serial.printf("[DISCOVERY] %s HTTP=%d size=%d verified=%s\n",
                    server.c_str(), status, http.getSize(), valid ? "yes" : "no");
    }
    http.end();
    return valid;
  }
};
