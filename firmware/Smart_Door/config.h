#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>
#include "secrets.h"

// ==========================================
// CAU HINH PHAN MEM / KET NOI
// ==========================================
#define DEVICE_ID                  "door-01"
#define FIRMWARE_VERSION            "0.4.0-fingerprint-admin"
#define ENABLE_LEGACY_TELEGRAM      1 // Restricted remote/status bot; management lives on server.
#define MQTT_HEARTBEAT_INTERVAL_MS  30000UL
#define MQTT_RECONNECT_INTERVAL_MS  5000UL
#define MQTT_COMMAND_MAX_FUTURE_SECONDS 15LL
#define SERVER_UNLOCK_MIN_DURATION_MS   500UL
#define SERVER_UNLOCK_MAX_DURATION_MS   10000UL
#define SERVER_UNLOCK_QUEUE_LENGTH      4

// ==========================================
// CẤU HÌNH CHÂN PHẦN CỨNG (PINOUT)
// ==========================================

// --- 1. Màn hình OLED SSD1306 (I2C) ---
#define OLED_SDA        8
#define OLED_SCL        9
#define SCREEN_ADDRESS  0x3C // Địa chỉ I2C mặc định của OLED 0.96 inch

// --- 2. Cảm biến vân tay AS608 (UART2) ---
// =========================================================================
// Chuẩn theo mặc định bo mạch ESP32-S3:
#define AS608_RX        18   // GPIO 18 là RX mặc định của ESP32-S3 ◄── Nối vào TX của AS608
#define AS608_TX        17   // GPIO 17 là TX mặc định của ESP32-S3 ──► Nối vào RX của AS608

#define BAUDRATE_AS608  57600

// --- 3. Khóa điện Relay, Còi báo & Đèn LED ---
#define LOCK_PIN        4   // Chân kích Relay mở khóa (Active LOW - Mức thấp kích mở)
#define BUZZER_PIN      5   // Chân điều khiển còi Buzzer (Active HIGH)
#define LED_GREEN_PIN   6   // LED báo trạng thái mở khóa thành công (Active HIGH)
#define LED_RED_PIN     7   // LED báo trạng thái lỗi / từ chối (Active HIGH)

// --- 4. Nút nhấn mở cửa từ bên trong ---
#define BUTTON_INSIDE_PIN   15  // Chân nút nhấn mở cửa (Active LOW - PULLUP)

// ==========================================
// THỜI GIAN CẤU HÌNH HỆ THỐNG (MILLISECONDS)
// ==========================================
#define UNLOCK_DURATION 3000 // Thời gian giữ mở khóa (3 giây)
#define ERROR_DURATION  1500 // Thời gian hiển thị cảnh báo lỗi (1.5 giây)

// ==========================================
// CẤU HÌNH WIFI
// ==========================================

// ==========================================
// CẤU HÌNH TELEGRAM BOT
// ==========================================

#endif // CONFIG_H
