#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebSocketsClient.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_task_wdt.h"
#include "img_converters.h"
#include "lwip/sockets.h"
#include "send_pacing.h"
#include "secrets.h"

#ifndef WIFI_SECONDARY_SSID
#define WIFI_SECONDARY_SSID ""
#endif

#ifndef WIFI_SECONDARY_PASSWORD
#define WIFI_SECONDARY_PASSWORD ""
#endif

#ifndef FACECHECK_SECONDARY_SERVER_HOST
#define FACECHECK_SECONDARY_SERVER_HOST FACECHECK_SERVER_HOST
#endif

#ifndef FACECHECK_WIFI_ONLY_DIAGNOSTIC
#define FACECHECK_WIFI_ONLY_DIAGNOSTIC 0
#endif

// ESP32-S3 WROOM CAM + OV3660 theo sơ đồ chân của bo đang sử dụng.
#define CAM_PIN_PWDN  -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK  15
#define CAM_PIN_SIOD  4
#define CAM_PIN_SIOC  5
#define CAM_PIN_D9    16
#define CAM_PIN_D8    17
#define CAM_PIN_D7    18
#define CAM_PIN_D6    12
#define CAM_PIN_D5    10
#define CAM_PIN_D4    8
#define CAM_PIN_D3    9
#define CAM_PIN_D2    11
#define CAM_PIN_VSYNC 6
#define CAM_PIN_HREF  7
#define CAM_PIN_PCLK  13

namespace {
constexpr char kStreamContentType[] =
    "multipart/x-mixed-replace;boundary=facecheck-boundary";
constexpr char kStreamPart[] =
    "\r\n--facecheck-boundary\r\nContent-Type: image/jpeg\r\n"
    "Content-Length: %u\r\n\r\n";
// HVGA/q20: khop khung 480x320 va nham JPEG khoang 8 KB/frame.
// Ban thu dong bo toan tuyen: camera, backend va TFT cung chay 5 FPS.
constexpr uint32_t kStreamTargetFps = 5;
constexpr uint32_t kStreamFrameIntervalMs = 1000 / kStreamTargetFps;
// Pulling MJPEG from the server is more robust on ESP32 than pushing large
// synchronous WebSocket frames. The WebSocket implementation remains
// available for diagnostics but is disabled in the deployed transport.
constexpr bool kWebSocketPushEnabled = false;
constexpr uint32_t kWsReconnectMinMs = 2000;
constexpr uint32_t kWsReconnectMaxMs = 8000;
constexpr uint32_t kWsSlowSendMs = 1500;
constexpr uint8_t kWsFailuresBeforeReconnect = 3;

httpd_handle_t web_server = nullptr;
httpd_handle_t stream_server = nullptr;
bool web_server_ready = false;
bool stream_server_ready = false;
volatile bool stream_client_active = false;
volatile uint32_t stream_client_count = 0;
volatile uint32_t stream_frame_count = 0;
volatile float stream_fps = 0.0f;
WebSocketsClient camera_socket;
volatile bool ws_connected = false;
volatile uint32_t ws_frame_count = 0;
volatile float ws_fps = 0.0f;
volatile uint32_t ws_last_send_ms = 0;
volatile uint8_t ws_send_pressure = 0;
TaskHandle_t websocket_task_handle = nullptr;
AdaptiveSendPacer ws_pacer;
uint32_t ws_reconnect_delay_ms = kWsReconnectMinMs;
bool ws_disconnect_requested = false;

const char* facecheck_server_host() {
  if (strlen(WIFI_SECONDARY_SSID) > 0 &&
      WiFi.SSID() == WIFI_SECONDARY_SSID) {
    return FACECHECK_SECONDARY_SERVER_HOST;
  }
  return FACECHECK_SERVER_HOST;
}

void websocket_event(WStype_t type, uint8_t *payload, size_t length) {
  (void)payload;
  (void)length;
  if (type == WStype_CONNECTED) {
    ws_connected = true;
    ws_send_pressure = 0;
    ws_reconnect_delay_ms = kWsReconnectMinMs;
    ws_disconnect_requested = false;
    camera_socket.setReconnectInterval(ws_reconnect_delay_ms);
    ws_pacer.beginConnection();
    Serial.printf("[WS] Da ket noi ws://%s:%u/camera\n",
                  facecheck_server_host(), FACECHECK_WS_PORT);
  } else if (type == WStype_DISCONNECTED) {
    ws_connected = false;
    ws_fps = 0.0f;
    ws_send_pressure = 0;
    if (!ws_disconnect_requested) {
      ws_reconnect_delay_ms = min(kWsReconnectMaxMs,
                                  ws_reconnect_delay_ms * 2);
    }
    ws_disconnect_requested = false;
    camera_socket.setReconnectInterval(ws_reconnect_delay_ms);
    Serial.printf("[WS] Mat ket noi; thu lai sau %u ms.\n",
                  ws_reconnect_delay_ms);
  }
}

void start_camera_socket() {
  const String path = String("/camera?token=") + FACECHECK_CAMERA_TOKEN;
  camera_socket.begin(facecheck_server_host(), FACECHECK_WS_PORT, path.c_str());
  camera_socket.onEvent(websocket_event);
  camera_socket.setReconnectInterval(kWsReconnectMinMs);
  // Phat hien socket nua-mo nhanh de video khong dung hang chuc giay.
  // Do not turn one short Wi-Fi/TCP stall into a reconnect cycle.
  camera_socket.enableHeartbeat(10000, 5000, 3);
}

void push_websocket_frame() {
  static unsigned long next_frame_at = 0;
  static uint32_t last_wait_ms = 0;
  static unsigned long window_started = millis();
  static uint32_t window_frames = 0;
  static uint32_t window_bytes = 0;
  static uint32_t window_capture_ms = 0;
  static uint32_t window_send_ms = 0;
  if (!ws_connected || stream_client_count > 0) return;

  const unsigned long now = millis();
  if (static_cast<int32_t>(now - next_frame_at) < 0) return;

  const unsigned long capture_started = millis();
  camera_fb_t *frame = esp_camera_fb_get();
  const uint32_t capture_ms = millis() - capture_started;
  if (!frame) return;

  bool sent = false;
  uint32_t send_ms = 0;
  if (frame->format == PIXFORMAT_JPEG && frame->len > 0) {
    const unsigned long send_started = millis();
    sent = camera_socket.sendBIN(frame->buf, frame->len);
    send_ms = millis() - send_started;
  }
  const size_t frame_length = frame->len;
  esp_camera_fb_return(frame);
  ws_last_send_ms = send_ms;
  if (!sent) {
    ws_pacer.recordFailure();
    ++ws_send_pressure;
    // Never retry immediately after a blocked/failed write. Immediate retries
    // used to keep loopTask inside the TCP writer until the watchdog rebooted.
    next_frame_at = millis() + ws_pacer.intervalMs();
    Serial.printf("[WS] Gui that bai (%u/%u) | SEND=%u ms | retry=%u ms\n",
                  ws_send_pressure, kWsFailuresBeforeReconnect, send_ms,
                  ws_pacer.intervalMs());
    if (ws_send_pressure >= kWsFailuresBeforeReconnect) {
      ws_reconnect_delay_ms = min(kWsReconnectMaxMs,
                                  ws_reconnect_delay_ms * 2);
      camera_socket.setReconnectInterval(ws_reconnect_delay_ms);
      ws_disconnect_requested = true;
      ws_send_pressure = 0;
      camera_socket.disconnect();
    }
    return;
  }

  ws_pacer.recordSuccess(send_ms);
  if (send_ms >= kWsSlowSendMs) {
    ++ws_send_pressure;
  } else if (send_ms <= ws_pacer.intervalMs()) {
    ws_send_pressure = 0;
  }
  if (ws_send_pressure >= kWsFailuresBeforeReconnect) {
    ws_reconnect_delay_ms = min(kWsReconnectMaxMs,
                                ws_reconnect_delay_ms * 2);
    camera_socket.setReconnectInterval(ws_reconnect_delay_ms);
    ws_disconnect_requested = true;
    ws_send_pressure = 0;
    Serial.printf("[WS] TCP cham lien tiep; tao lai socket sau %u ms.\n",
                  ws_reconnect_delay_ms);
    camera_socket.disconnect();
    return;
  }
  const uint32_t target_interval_ms = ws_pacer.intervalMs();
  const uint32_t frame_work_ms = millis() - capture_started;
  // Healthy writes use an absolute frame period: CAP + SEND + WAIT ~= target.
  // A slow write gets a full cooldown after completion and can never trigger
  // an immediate catch-up frame.
  last_wait_ms = target_interval_ms;
  if (send_ms <= 100) {
    last_wait_ms = frame_work_ms < target_interval_ms
        ? target_interval_ms - frame_work_ms
        : 1;
  }
  next_frame_at = millis() + last_wait_ms;

  ++ws_frame_count;
  ++window_frames;
  window_bytes += frame_length;
  window_capture_ms += capture_ms;
  window_send_ms += send_ms;
  const uint32_t elapsed = millis() - window_started;
  if (elapsed >= 2000) {
    ws_fps = window_frames * 1000.0f / elapsed;
    Serial.printf(
        "[WS] FPS=%.1f | CAP=%u ms | SEND=%u ms | JPEG=%u B | "
        "RSSI=%d dBm | heap=%u | TARGET=%u FPS | PACE=%u ms | WAIT=%u ms\n",
        ws_fps,
        window_frames ? window_capture_ms / window_frames : 0,
        window_frames ? window_send_ms / window_frames : 0,
        window_frames ? window_bytes / window_frames : 0,
        WiFi.RSSI(), ESP.getFreeHeap(), ws_pacer.targetFps(),
        ws_pacer.intervalMs(), last_wait_ms);
    window_frames = 0;
    window_bytes = 0;
    window_capture_ms = 0;
    window_send_ms = 0;
    window_started = millis();
  }
}

void websocket_task(void *parameter) {
  (void)parameter;
  // This task is deliberately not registered with the task watchdog. A slow
  // TCP write may still time out, but it can no longer starve loopTask, Wi-Fi
  // recovery, status reporting, or reboot the complete camera.
  for (;;) {
    if (WiFi.status() == WL_CONNECTED) {
      camera_socket.loop();
      push_websocket_frame();
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

void start_websocket_task() {
  if (websocket_task_handle != nullptr) return;
  const BaseType_t created = xTaskCreatePinnedToCore(
      websocket_task, "camera_ws", 8192, nullptr, 1,
      &websocket_task_handle, 0);
  if (created != pdPASS) {
    websocket_task_handle = nullptr;
    Serial.println("[WS] Khong tao duoc task WebSocket.");
  }
}

bool facecheck_server_reachable(const char* host) {
  WiFiClient probe;
  probe.setTimeout(1200);
  const bool connected = probe.connect(host, 8767, 1200);
  probe.stop();
  return connected;
}

bool connect_station(const char* ssid, const char* password,
                     const char* server_host) {
  if (strlen(ssid) == 0) return false;
  WiFi.disconnect(false);
  delay(100);
  if (strlen(password) == 0) {
    WiFi.begin(ssid);
  } else {
    WiFi.begin(ssid, password);
  }
  Serial.printf("[WIFI] Thu %s", ssid);
  const unsigned long started_at = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started_at < 7000) {
    delay(250);
    Serial.print('.');
    if (web_server_ready || stream_server_ready) esp_task_wdt_reset();
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) return false;
  if (!facecheck_server_reachable(server_host)) {
    Serial.printf("[WIFI] %s co song nhung khong thay FaceCheck tai %s:8767\n",
                  ssid, server_host);
    return false;
  }
  return true;
}

void begin_station_connection() {
  if (connect_station(WIFI_SSID, WIFI_PASSWORD, FACECHECK_SERVER_HOST)) return;
  if (connect_station(WIFI_SECONDARY_SSID, WIFI_SECONDARY_PASSWORD,
                      FACECHECK_SECONDARY_SERVER_HOST)) return;
  Serial.println("[WIFI] Chua tim thay mang co may chu FaceCheck.");
}

void start_mdns() {
  MDNS.end();
  if (MDNS.begin("facecheck-cam")) {
    MDNS.addService("http", "tcp", 80);
    Serial.println("[MDNS] http://facecheck-cam.local/ san sang");
  } else {
    Serial.println("[MDNS] Khoi tao that bai; van co the dung dia chi IP.");
  }
}

esp_err_t root_handler(httpd_req_t *request) {
  static const char page[] PROGMEM = R"HTML(
<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FaceCheck Camera</title><style>
html,body{margin:0;background:#0f172a;color:#fff;font-family:Arial,sans-serif}
main{min-height:100vh;display:grid;place-items:center;padding:12px;box-sizing:border-box}
section{width:min(100%,760px);text-align:center}h1{font-size:20px;margin:0 0 10px}
img{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#000;border-radius:12px}
p{font-size:13px;color:#bfdbfe}</style></head><body><main><section>
<h1>FaceCheck · ESP32-S3 OV3660</h1><img id="cam" alt="Camera đang kết nối">
<p>Luồng MJPEG 640×480 · xử lý AI thực hiện trên laptop</p>
<script>
const cam=document.getElementById('cam');
const connectCamera=()=>{cam.src='http://'+location.hostname+':81/stream?t='+Date.now()};
cam.onerror=()=>{cam.removeAttribute('src');setTimeout(connectCamera,1500)};
connectCamera();
</script>
</section></main></body></html>)HTML";
  httpd_resp_set_type(request, "text/html; charset=utf-8");
  return httpd_resp_send(request, page, HTTPD_RESP_USE_STRLEN);
}

esp_err_t health_handler(httpd_req_t *request) {
  char response[384];
  snprintf(response, sizeof(response),
           "{\"ok\":true,\"camera\":\"OV3660\",\"rssi\":%d,"
           "\"free_heap\":%u,\"stream_active\":%s,\"stream_clients\":%u,"
           "\"stream_frames\":%u,"
           "\"stream_fps\":%.1f,\"target_fps\":%u,"
           "\"ws_connected\":%s,\"ws_frames\":%u,\"ws_fps\":%.1f,"
           "\"ws_send_ms\":%u,\"ws_pressure\":%u,"
           "\"ip\":\"%s\"}",
           WiFi.RSSI(), ESP.getFreeHeap(),
           stream_client_active ? "true" : "false", stream_client_count,
           stream_frame_count, stream_fps,
           kWebSocketPushEnabled ? ws_pacer.targetFps() : kStreamTargetFps,
           ws_connected ? "true" : "false", ws_frame_count, ws_fps,
           ws_last_send_ms, ws_send_pressure,
           WiFi.localIP().toString().c_str());
  httpd_resp_set_type(request, "application/json");
  httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(request, response, HTTPD_RESP_USE_STRLEN);
}

esp_err_t capture_handler(httpd_req_t *request) {
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR,
                        "Khong lay duoc anh camera");
    return ESP_FAIL;
  }

  uint8_t *jpeg = frame->buf;
  size_t jpeg_length = frame->len;
  bool converted = false;
  if (frame->format != PIXFORMAT_JPEG) {
    converted = frame2jpg(frame, 80, &jpeg, &jpeg_length);
  }

  esp_err_t result = ESP_FAIL;
  if (frame->format == PIXFORMAT_JPEG || converted) {
    httpd_resp_set_type(request, "image/jpeg");
    httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
    result = httpd_resp_send(request,
                             reinterpret_cast<const char *>(jpeg),
                             jpeg_length);
  }

  if (converted) free(jpeg);
  esp_camera_fb_return(frame);
  return result;
}

esp_err_t stream_handler(httpd_req_t *request) {
  httpd_resp_set_type(request, kStreamContentType);
  httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store, no-cache, must-revalidate");
  httpd_resp_set_hdr(request, "Pragma", "no-cache");
  httpd_resp_set_hdr(request, "X-Framerate", "18");

  // MJPEG gui mot header nho roi moi den JPEG. Tat Nagle de tranh cac goi
  // nho bi doi ACK, gay SEND nhay len hang tram mili giay du Wi-Fi tot.
  const int socket_fd = httpd_req_to_sockfd(request);
  int no_delay = 1;
  const int no_delay_result = setsockopt(
      socket_fd, IPPROTO_TCP, TCP_NODELAY, &no_delay, sizeof(no_delay));
  Serial.printf("[STREAM] TCP_NODELAY=%s\n",
                no_delay_result == 0 ? "OK" : "FAIL");

  char header[96];
  uint32_t client_frames = 0;
  uint32_t fps_window_frames = 0;
  uint32_t fps_window_bytes = 0;
  uint32_t fps_window_capture_ms = 0;
  uint32_t fps_window_send_ms = 0;
  unsigned long fps_window_started = millis();
  ++stream_client_count;
  stream_client_active = true;
  Serial.printf("[STREAM] Client da ket noi | clients=%u\n",
                stream_client_count);
  while (true) {
    const unsigned long frame_started = millis();
    const unsigned long capture_started = millis();
    camera_fb_t *frame = esp_camera_fb_get();
    const unsigned long capture_ms = millis() - capture_started;
    if (!frame) break;

    uint8_t *jpeg = frame->buf;
    size_t jpeg_length = frame->len;
    bool converted = false;
    if (frame->format != PIXFORMAT_JPEG) {
      converted = frame2jpg(frame, 80, &jpeg, &jpeg_length);
    }

    esp_err_t result = ESP_OK;
    if (frame->format != PIXFORMAT_JPEG && !converted) {
      result = ESP_FAIL;
    } else {
      const unsigned long send_started = millis();
      const size_t header_length = snprintf(
          header, sizeof(header), kStreamPart,
          static_cast<unsigned int>(jpeg_length));
      // Gop boundary va header thanh mot lan gui de giam so lan socket block.
      result = httpd_resp_send_chunk(request, header, header_length);
      if (result == ESP_OK) {
        result = httpd_resp_send_chunk(
            request, reinterpret_cast<const char *>(jpeg), jpeg_length);
      }
      if (result == ESP_OK) {
        const unsigned long send_ms = millis() - send_started;
        ++client_frames;
        ++stream_frame_count;
        ++fps_window_frames;
        fps_window_bytes += jpeg_length;
        fps_window_capture_ms += capture_ms;
        fps_window_send_ms += send_ms;
        const unsigned long now = millis();
        const unsigned long fps_elapsed = now - fps_window_started;
        if (fps_elapsed >= 2000) {
          stream_fps = fps_window_frames * 1000.0f / fps_elapsed;
          const uint32_t average_jpeg = fps_window_frames
              ? fps_window_bytes / fps_window_frames : 0;
          const uint32_t average_capture_ms = fps_window_frames
              ? fps_window_capture_ms / fps_window_frames : 0;
          const uint32_t average_send_ms = fps_window_frames
              ? fps_window_send_ms / fps_window_frames : 0;
          Serial.printf(
              "[STREAM] FPS=%.1f | CAP=%u ms | SEND=%u ms | JPEG=%u B | "
              "clients=%u | RSSI=%d dBm | heap=%u | temp=%.1f C\n",
              stream_fps, average_capture_ms, average_send_ms, average_jpeg,
              stream_client_count, WiFi.RSSI(), ESP.getFreeHeap(),
              temperatureRead());
          fps_window_frames = 0;
          fps_window_bytes = 0;
          fps_window_capture_ms = 0;
          fps_window_send_ms = 0;
          fps_window_started = now;
        }
      }
    }

    if (converted) free(jpeg);
    esp_camera_fb_return(frame);
    if (result != ESP_OK) break;
    const unsigned long frame_elapsed = millis() - frame_started;
    if (frame_elapsed < kStreamFrameIntervalMs) {
      delay(kStreamFrameIntervalMs - frame_elapsed);
    } else {
      delay(1);
    }
  }
  if (stream_client_count > 0) --stream_client_count;
  stream_client_active = stream_client_count > 0;
  stream_fps = 0.0f;
  Serial.printf("[STREAM] Client ngat ket noi sau %u frames | clients=%u\n",
                client_frames, stream_client_count);
  return ESP_OK;
}

void start_web_servers() {
  httpd_config_t web_config = HTTPD_DEFAULT_CONFIG();
  web_config.server_port = 80;
  web_config.ctrl_port = 32768;
  web_config.max_uri_handlers = 8;

  httpd_uri_t root_uri = {"/", HTTP_GET, root_handler, nullptr};
  httpd_uri_t health_uri = {"/health", HTTP_GET, health_handler, nullptr};
  httpd_uri_t capture_uri = {"/capture", HTTP_GET, capture_handler, nullptr};
  const esp_err_t web_error = httpd_start(&web_server, &web_config);
  if (web_error == ESP_OK) {
    httpd_register_uri_handler(web_server, &root_uri);
    httpd_register_uri_handler(web_server, &health_uri);
    httpd_register_uri_handler(web_server, &capture_uri);
    web_server_ready = true;
    Serial.println("[SERVER] Web server port 80: OK");
  } else {
    Serial.printf("[SERVER] Web server port 80 that bai: 0x%x\n", web_error);
  }

  httpd_config_t stream_config = HTTPD_DEFAULT_CONFIG();
  stream_config.server_port = 81;
  stream_config.ctrl_port = 32769;
  stream_config.max_uri_handlers = 4;
  httpd_uri_t stream_uri = {"/stream", HTTP_GET, stream_handler, nullptr};
  const esp_err_t stream_error = httpd_start(&stream_server, &stream_config);
  if (stream_error == ESP_OK) {
    httpd_register_uri_handler(stream_server, &stream_uri);
    stream_server_ready = true;
    Serial.println("[SERVER] MJPEG server port 81: OK");
  } else {
    Serial.printf("[SERVER] MJPEG server port 81 that bai: 0x%x\n", stream_error);
  }
}

bool start_camera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D2;
  config.pin_d1 = CAM_PIN_D3;
  config.pin_d2 = CAM_PIN_D4;
  config.pin_d3 = CAM_PIN_D5;
  config.pin_d4 = CAM_PIN_D6;
  config.pin_d5 = CAM_PIN_D7;
  config.pin_d6 = CAM_PIN_D8;
  config.pin_d7 = CAM_PIN_D9;
  config.pin_xclk = CAM_PIN_XCLK;
  config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC;
  config.pin_href = CAM_PIN_HREF;
  config.pin_sccb_sda = CAM_PIN_SIOD;
  config.pin_sccb_scl = CAM_PIN_SIOC;
  config.pin_pwdn = CAM_PIN_PWDN;
  config.pin_reset = CAM_PIN_RESET;
  // Reduce continuous camera DMA pressure so Wi-Fi can transmit reliably.
  config.xclk_freq_hz = 8000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_HVGA;
  // Gia tri lon hon nen manh hon; q20 nham muc tieu khoang 8 KB/frame.
  config.jpeg_quality = 20;
  // One on-demand framebuffer avoids continuous PSRAM/DMA contention.
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("[CAMERA] Khoi tao that bai: 0x%x\n", error);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor) {
    // Bo camera vat ly dang lap nguoc nen giu lat doc. Khong lat ngang de
    // chuyen dong trai/phai hien dung, tranh hinh anh nhu trong guong.
    sensor->set_vflip(sensor, 1);
    sensor->set_hmirror(sensor, 0);
    sensor->set_sharpness(sensor, 1);
    sensor->set_contrast(sensor, 0);
    if (sensor->id.PID == OV3660_PID) {
      sensor->set_brightness(sensor, 1);
      sensor->set_saturation(sensor, -1);
    }
  }
  return true;
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(800);
  Serial.println("\n[BOOT] FaceCheck ESP32-S3 OV3660");

  if (!psramFound()) {
    Serial.println("[PSRAM] Khong tim thay PSRAM. Dung khoi dong camera.");
    while (true) delay(1000);
  }
  Serial.printf("[PSRAM] San sang: %u bytes\n", ESP.getPsramSize());

#if !FACECHECK_AP_ONLY_TEST && !FACECHECK_WIFI_ONLY_DIAGNOSTIC
  if (!start_camera()) {
    while (true) delay(1000);
  }
  Serial.println("[CAMERA] San sang (HVGA 480x320/q20, 1 buffer, XCLK 8 MHz)");
#else
  Serial.println("[TEST] Bo qua camera va web server, chi kiem tra SoftAP.");
#endif

#if CAMERA_USE_SOFT_AP
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  const IPAddress ap_ip(192, 168, 4, 1);
  const IPAddress gateway(192, 168, 4, 1);
  const IPAddress subnet(255, 255, 255, 0);
  constexpr uint8_t ap_channel = 10;
  if (!WiFi.softAPConfig(ap_ip, gateway, subnet) ||
      !WiFi.softAP(CAMERA_AP_SSID, CAMERA_AP_PASSWORD, ap_channel, false, 1)) {
    Serial.println("[WIFI] Khong tao duoc mang SoftAP.");
    while (true) delay(1000);
  }
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  Serial.printf("[WIFI] SoftAP san sang: %s | channel=%u | max_clients=1\n",
                CAMERA_AP_SSID, ap_channel);
#elif FACECHECK_WIFI_ONLY_DIAGNOSTIC
  Serial.println("[TEST] Wi-Fi only; camera/DMA disabled.");
#else
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  begin_station_connection();
  Serial.printf("[WIFI] Dang tim %s hoac %s\n", WIFI_SSID,
                WIFI_SECONDARY_SSID);
  const unsigned long started_at = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started_at < 30000) {
    delay(400);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    // Match the stable display firmware: let the driver negotiate protocol,
    // bandwidth and TX power with the access point, then disable sleep.
    WiFi.setSleep(false);
  } else {
    Serial.println(
        "[WIFI] Chua ket noi duoc; firmware se tu thu lai moi 5 giay.");
  }
#endif

#if CAMERA_USE_SOFT_AP
  start_mdns();
#else
  if (WiFi.status() == WL_CONNECTED) start_mdns();
#endif
#if !FACECHECK_AP_ONLY_TEST && !FACECHECK_WIFI_ONLY_DIAGNOSTIC
  // Starting esp_http_server before STA owns an IP can leave listening
  // sockets that accept TCP but never dispatch a request after DHCP recovers.
#if CAMERA_USE_SOFT_AP
  start_web_servers();
#else
  if (WiFi.status() == WL_CONNECTED) start_web_servers();
#endif
  if (kWebSocketPushEnabled) start_camera_socket();
#endif

#if CAMERA_USE_SOFT_AP
  const String ip = WiFi.softAPIP().toString();
  Serial.printf("[WIFI] IP truc tiep: %s\n", ip.c_str());
#else
  const String ip = WiFi.localIP().toString();
    Serial.printf("[WIFI] SSID: %s | IP: %s | RSSI: %d dBm\n",
                  WiFi.SSID().c_str(), ip.c_str(), WiFi.RSSI());
#endif
  if (web_server_ready) {
    Serial.printf("[WEB] Trang kiem tra: http://%s/\n", ip.c_str());
    Serial.printf("[WEB] Anh chup:      http://%s/capture\n", ip.c_str());
    Serial.println("[WEB] Hostname thu nghiem: http://facecheck-cam.local/");
  }
  if (stream_server_ready) {
    Serial.printf("[WEB] MJPEG stream:  http://%s:81/stream\n", ip.c_str());
  }

  // Tu khoi dong lai neu camera/TCP driver giu loop qua lau. Bat watchdog sau
  // giai doan ket noi Wi-Fi de setup duoc phep cho DHCP toi 30 giay.
  esp_task_wdt_init(8, true);
  esp_task_wdt_add(nullptr);
#if !FACECHECK_AP_ONLY_TEST && !FACECHECK_WIFI_ONLY_DIAGNOSTIC
  if (kWebSocketPushEnabled) start_websocket_task();
#endif
}

void loop() {
  esp_task_wdt_reset();
  static unsigned long last_report = 0;
#if !CAMERA_USE_SOFT_AP
  static unsigned long last_reconnect_attempt = 0;
  static unsigned long last_server_probe = 0;
  static bool was_connected = WiFi.status() == WL_CONNECTED;
  bool is_connected = WiFi.status() == WL_CONNECTED;
  if (is_connected && millis() - last_server_probe >= 6000) {
    last_server_probe = millis();
    if (!facecheck_server_reachable(facecheck_server_host())) {
      Serial.println("[WIFI] Laptop da doi mang; dang tim FaceCheck tren mang con lai.");
      WiFi.disconnect(false);
      is_connected = false;
    }
  }
  if (is_connected && !was_connected) {
    WiFi.setSleep(false);
    const String ip = WiFi.localIP().toString();
    Serial.printf("[WIFI] Da ket noi lai: %s | RSSI=%d dBm\n",
                  ip.c_str(), WiFi.RSSI());
    start_mdns();
#if !FACECHECK_AP_ONLY_TEST
    if (!web_server_ready && !stream_server_ready) {
      start_web_servers();
    }
#endif
  } else if (!is_connected && was_connected) {
    Serial.println("[WIFI] Mat ket noi.");
  }
  was_connected = is_connected;
  if (!is_connected &&
      millis() - last_reconnect_attempt >= 5000) {
    last_reconnect_attempt = millis();
    Serial.println("[WIFI] Mat ket noi; dang thu ket noi lai...");
    WiFi.disconnect(false);
    begin_station_connection();
  }
#endif
  if (millis() - last_report >= 10000) {
    last_report = millis();
#if CAMERA_USE_SOFT_AP
    Serial.printf("[STATUS] WiFi=AP clients=%u heap=%u psram=%u temp=%.1f C\n",
                  WiFi.softAPgetStationNum(), ESP.getFreeHeap(),
                  ESP.getFreePsram(), temperatureRead());
#else
    Serial.printf("[STATUS] WiFi=%s RSSI=%d heap=%u psram=%u temp=%.1f C\n",
                  WiFi.status() == WL_CONNECTED ? "OK" : "LOST",
                  WiFi.RSSI(), ESP.getFreeHeap(), ESP.getFreePsram(),
                  temperatureRead());
#endif
  }
  delay(1);
}
