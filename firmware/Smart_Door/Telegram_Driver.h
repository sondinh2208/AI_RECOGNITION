#ifndef TELEGRAM_DRIVER_H
#define TELEGRAM_DRIVER_H

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/event_groups.h>

// ==========================================
// EVENT BIT: MỞ KHÓA TỪ TELEGRAM
// Bit 2 — các bit 0,1 dành cho vân tay & nút nhấn (khai báo ở Smart_Door.ino)
// ==========================================
#define EVT_UNLOCK_TELEGRAM (1 << 2)

// ==========================================
// STRUCT THÔNG BÁO ĐẨY (PUSH NOTIFICATION)
// Hardware tasks đẩy vào queue -> Task_Telegram gửi lên Telegram
// ==========================================
#define TELEGRAM_NOTIF_MAX_LEN  256

struct TelegramNotif {
    char message[TELEGRAM_NOTIF_MAX_LEN];
};

// Queue toàn cục: hardware tasks -> Task_Telegram
// Khai báo extern để Smart_Door.ino và các task truy cập được
extern QueueHandle_t telegramNotifQueue;

// ==========================================
// API CÔNG KHAI
// ==========================================

/**
 * @brief Liên kết Telegram Driver với các handle FreeRTOS của hệ thống.
 *        Phải gọi trước khi tạo Task_Telegram.
 * @param displayQ      Queue gửi lệnh hiển thị OLED
 * @param lockEvtGroup  Event Group đồng bộ lệnh mở khóa
 */
void initTelegram(QueueHandle_t displayQ, EventGroupHandle_t lockEvtGroup);

/**
 * @brief FreeRTOS Task cho Telegram Bot.
 *        Chạy trên Core 0, stack 12KB (SSL/TLS cần nhiều RAM).
 *        Kết nối WiFi, đồng bộ NTP, poll lệnh, gửi push notification.
 */
void Task_Telegram(void *pvParameters);

/**
 * @brief Thêm một entry vào log ring buffer (10 sự kiện gần nhất).
 *        Thread-safe (dùng Mutex nội bộ).
 * @param entry Chuỗi mô tả sự kiện
 */
void addLog(const String &entry);

/**
 * @brief Lấy toàn bộ log dưới dạng Markdown String để gửi qua Telegram.
 * @return String chứa danh sách 10 sự kiện gần nhất
 */
String getLogString();

/**
 * @brief Lấy chuỗi thời gian hiện tại (HH:MM:SS) từ NTP
 */
String getTimeStr();

/**
 * @brief Lấy tên người dùng gán cho ID vân tay từ bộ nhớ Flash (NVS)
 * @param id ID vân tay (1 - 127)
 * @return Tên người dùng hoặc chuỗi rỗng nếu chưa đặt
 */
String getFingerprintName(int id);

/**
 * @brief Đặt hoặc cập nhật tên cho ID vân tay vào Flash (NVS)
 * @param id ID vân tay (1 - 127)
 * @param name Tên cần đặt
 */
void setFingerprintName(int id, const String &name);

/**
 * @brief Xóa tên của ID vân tay khỏi Flash (NVS)
 */
void removeFingerprintName(int id);

/**
 * @brief Xóa toàn bộ tên vân tay khỏi Flash (NVS)
 */
void clearAllFingerprintNames();

#endif // TELEGRAM_DRIVER_H

