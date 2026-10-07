#include <Arduino.h>
#include "USB.h"
#include "USBCDC.h"
#include "esp_camera.h"

// ESP32-S3 WROOM CAM + OV3660 theo so do chan cua bo FaceCheck.
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
USBCDC CameraUSB;

// 8 FPS la diem can bang cho OV3660: du muot de can chinh khuon mat, dong
// thoi giam tai DMA/JPEG va khong tao hang doi khi AI + TFT cham hon camera.
constexpr uint32_t kFrameIntervalMs = 125;
constexpr size_t kUsbChunkSize = 2048;
constexpr unsigned long kWriteTimeoutMs = 1500;
uint32_t frame_id = 0;

// CRC-32/IEEE giong zlib.crc32() o bridge Python. Checksum cho phep
// laptop bo qua frame bi hong thay vi dua anh soc mau vao AI/giao dien.
uint32_t crc32_ieee(const uint8_t *data, size_t length) {
  uint32_t crc = 0xffffffffu;
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
  }
  return crc ^ 0xffffffffu;
}

void put_u32_le(uint8_t *target, uint32_t value) {
  target[0] = value & 0xff;
  target[1] = (value >> 8) & 0xff;
  target[2] = (value >> 16) & 0xff;
  target[3] = (value >> 24) & 0xff;
}

bool usb_write_all(const uint8_t *data, size_t length) {
  size_t offset = 0;
  const unsigned long started = millis();
  while (offset < length) {
    const size_t chunk = min(kUsbChunkSize, length - offset);
    const size_t written = CameraUSB.write(data + offset, chunk);
    if (written > 0) {
      offset += written;
      continue;
    }
    if (millis() - started >= kWriteTimeoutMs) return false;
    delay(1);
  }
  return true;
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
  // Giu XCLK/JPEG da cho chat luong tot, nhung chi dung mot framebuffer de
  // camera khong ghi frame moi vao PSRAM trong luc frame hien tai dang gui USB.
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;   // 640x480 da xac nhan on dinh qua USB.
  config.jpeg_quality = 10;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("[CAMERA] Khoi tao that bai: 0x%x\n", error);
    CameraUSB.printf("[CAMERA] Khoi tao that bai: 0x%x\n", error);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor && sensor->id.PID == OV3660_PID) {
    sensor->set_vflip(sensor, 1);
    sensor->set_brightness(sensor, 1);
    sensor->set_contrast(sensor, 1);
    sensor->set_saturation(sensor, 0);
    sensor->set_sharpness(sensor, 2);
  }
  return true;
}
}  // namespace

void setup() {
  Serial.begin(115200);  // Log qua cong USB-UART/COM11 khi phat trien.
  // Khoi dong USB truoc camera de van doc duoc loi khoi tao tren cong OTG.
  CameraUSB.begin(115200);
  USB.begin();
  delay(500);
  Serial.println("\n[BOOT] FaceCheck USB Camera");
  CameraUSB.println("\n[BOOT] FaceCheck USB Camera");

  if (!psramFound()) {
    Serial.println("[PSRAM] Khong tim thay PSRAM.");
    CameraUSB.println("[PSRAM] Khong tim thay PSRAM.");
    while (true) delay(1000);
  }
  if (!start_camera()) {
    while (true) delay(1000);
  }

  Serial.println("[USB] Cam cong USB-OTG vao laptop; du lieu camera se o COM rieng.");
  CameraUSB.println("[USB] Camera san sang; bat dau phat frame FCM2.");
}

void loop() {
  const unsigned long frame_started = millis();
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    delay(10);
    return;
  }

  if (frame->format == PIXFORMAT_JPEG && frame->len > 0 && frame->len < 500000) {
    // Protocol FCM2: magic + frame_id + jpeg_length + jpeg_crc32.
    uint8_t header[16] = {'F', 'C', 'M', '2'};
    put_u32_le(header + 4, ++frame_id);
    put_u32_le(header + 8, static_cast<uint32_t>(frame->len));
    put_u32_le(header + 12, crc32_ieee(frame->buf, frame->len));
    if (!usb_write_all(header, sizeof(header)) ||
        !usb_write_all(frame->buf, frame->len)) {
      delay(10);  // Chua co host hoac host vua ngat ket noi.
    }
  }
  esp_camera_fb_return(frame);

  const unsigned long elapsed = millis() - frame_started;
  if (elapsed < kFrameIntervalMs) delay(kFrameIntervalMs - elapsed);
}
