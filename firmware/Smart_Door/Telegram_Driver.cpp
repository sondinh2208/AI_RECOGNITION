/**
 * @file Telegram_Driver.cpp
 * @brief Telegram Bot Driver cho Khóa Cửa Điện Tử (ESP32)
 *
 * Chức năng:
 *  - Kết nối WiFi và tự động reconnect khi mất mạng
 *  - Đồng bộ thời gian thực qua NTP (UTC+7 Việt Nam)
 *  - Poll lệnh từ Telegram Bot mỗi 1.5 giây
 *  - Phân quyền: Admin (toàn quyền) / User thường (/open, /status)
 *  - Gửi push notification cho mọi sự kiện phần cứng
 *  - Log ring buffer 10 sự kiện, bảo vệ bằng Mutex
 *
 * Thư viện cần cài (Arduino Library Manager):
 *  - UniversalTelegramBot by Brian Lough
 *  - ArduinoJson by Benoit Blanchon (v6.x)
 */

#include "Telegram_Driver.h"
#include "Display_Driver.h"   // DisplayMsg, DisplayMsgType
#include "Lock_Driver.h"       // isLockOpen(), closeLock()
#include "MQTT_Driver.h"
#include "config.h"            // BOT_TOKEN, ADMIN_CHAT_ID, WIFI_SSID/PASSWORD

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <UniversalTelegramBot.h>
#include <ArduinoJson.h>
#include <time.h>
#include <freertos/semphr.h>

#include <Preferences.h>
#include <vector>

// ==========================================
// BIẾN NỘI BỘ MODULE
// ==========================================

// Handle nhận từ initTelegram()
static QueueHandle_t      _displayQueue   = NULL;
static EventGroupHandle_t _lockEventGroup = NULL;

// Queue push notification (public - khai báo extern trong .h)
QueueHandle_t telegramNotifQueue = NULL;

// Log ring buffer
#define LOG_BUFFER_SIZE     10
static String            s_logBuffer[LOG_BUFFER_SIZE];
static int               s_logHead  = 0;
static int               s_logCount = 0;
static SemaphoreHandle_t s_logMutex = NULL;

// NVS Preferences cho tên vân tay và người dùng
static Preferences prefNames;
static Preferences prefUsers;
static std::vector<String> s_authorizedUsers;
static String s_pendingOpenChat;
static uint32_t s_pendingOpenAt = 0;
static uint32_t s_lastRemoteOpenAt = 0;
static bool s_remoteOpened = false;

// ==========================================
// QUẢN LÝ NGƯỜI DÙNG ỦY QUYỀN (NVS)
// ==========================================

static void loadAuthorizedUsers() {
    s_authorizedUsers.clear();
    prefUsers.begin("door_users", true); // read-only
    int count = min(32, prefUsers.getInt("count", 0));
    for (int i = 0; i < count; i++) {
        String key = "u_" + String(i);
        String u = prefUsers.getString(key.c_str(), "");
        if (u.length() > 0) {
            s_authorizedUsers.push_back(u);
        }
    }
    prefUsers.end();
}

static void saveAuthorizedUsers() {
    prefUsers.begin("door_users", false); // read-write
    prefUsers.clear();
    prefUsers.putInt("count", s_authorizedUsers.size());
    for (size_t i = 0; i < s_authorizedUsers.size(); i++) {
        String key = "u_" + String(i);
        prefUsers.putString(key.c_str(), s_authorizedUsers[i]);
    }
    prefUsers.end();
}

static bool isUserAuthorized(const String &chatId) {
    for (const String &u : s_authorizedUsers) {
        if (u == chatId) return true;
    }
    return false;
}

static bool isAdmin(const String &chatId) {
    return chatId == String(ADMIN_CHAT_ID);
}

static bool isAuthorized(const String &chatId) {
    if (isAdmin(chatId)) return true;
    return isUserAuthorized(chatId);
}

// ==========================================
// QUẢN LÝ TÊN VÂN TAY (NVS)
// ==========================================

String getFingerprintName(int id) {
    prefNames.begin("fp_names", true); // read-only
    String key = "id_" + String(id);
    String name = prefNames.getString(key.c_str(), "");
    prefNames.end();
    return name;
}

void setFingerprintName(int id, const String &name) {
    prefNames.begin("fp_names", false); // read-write
    String key = "id_" + String(id);
    if (name.length() > 0) {
        prefNames.putString(key.c_str(), name);
    } else {
        prefNames.remove(key.c_str());
    }
    prefNames.end();
}

void removeFingerprintName(int id) {
    prefNames.begin("fp_names", false);
    String key = "id_" + String(id);
    prefNames.remove(key.c_str());
    prefNames.end();
}

void clearAllFingerprintNames() {
    prefNames.begin("fp_names", false);
    prefNames.clear();
    prefNames.end();
}

// ==========================================
// HELPER FUNCTIONS (nội bộ)
// ==========================================

/**
 * @brief Lấy chuỗi thời gian hiện tại dạng HH:MM:SS (sau khi NTP sync)
 */
String getTimeStr() {
    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 10)) return "--:--:--";
    time_t vietnamTime = time(nullptr) + 7 * 3600;
    gmtime_r(&vietnamTime, &timeinfo);
    char buf[10];
    strftime(buf, sizeof(buf), "%H:%M:%S", &timeinfo);
    return String(buf);
}

// ==========================================
// API CÔNG KHAI: LOG
// ==========================================

void addLog(const String &entry) {
    if (!s_logMutex) return;
    if (xSemaphoreTake(s_logMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        s_logBuffer[s_logHead] = entry;
        s_logHead = (s_logHead + 1) % LOG_BUFFER_SIZE;
        if (s_logCount < LOG_BUFFER_SIZE) s_logCount++;
        xSemaphoreGive(s_logMutex);
    }
}

String getLogString() {
    String result = "📋 *LỊCH SỬ 10 SỰ KIỆN GẦN NHẤT:*\n\n";
    if (!s_logMutex) return result + "_[Chưa khởi tạo]_";

    if (xSemaphoreTake(s_logMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        if (s_logCount == 0) {
            result += "_Chưa có sự kiện nào được ghi nhận._";
        } else {
            int start = (s_logHead - s_logCount + LOG_BUFFER_SIZE) % LOG_BUFFER_SIZE;
            for (int i = 0; i < s_logCount; i++) {
                int idx = (start + i) % LOG_BUFFER_SIZE;
                result += String(i + 1) + ". " + s_logBuffer[idx] + "\n";
            }
        }
        xSemaphoreGive(s_logMutex);
    }
    return result;
}

// ==========================================
// API CÔNG KHAI: INIT
// ==========================================

void initTelegram(QueueHandle_t displayQ, EventGroupHandle_t lockEvtGroup) {
    _displayQueue   = displayQ;
    _lockEventGroup = lockEvtGroup;

    // Tạo queue push notification và mutex log
    telegramNotifQueue = xQueueCreate(10, sizeof(TelegramNotif));
    s_logMutex         = xSemaphoreCreateMutex();

    // Nạp danh sách user ủy quyền từ Flash
    loadAuthorizedUsers();

    Serial.println(F("[TELEGRAM] Driver da khoi tao thanh cong."));
}

#include "Fingerprint_Driver.h" // API quản lý vân tay AS608


// ==========================================
// XỬ LÝ LỆNH BOT
// ==========================================

static void handleBotCommand(UniversalTelegramBot &bot,
                              const String &chatId,
                              const String &text) {
    bool admin = isAdmin(chatId);
    // Telegram is not a second employee/fingerprint database.
    if (text != "/start" && text != "/help" && text != "/status" && text != "/open" && text != "/open confirm" && text != "/cancel") {
        bot.sendMessage(chatId, "Quản lý hồ sơ, vân tay và phân quyền đã chuyển lên server. Gõ /help để xem lệnh đang dùng.", "");
        return;
    }

    // --- /start hoặc /help ---
    if (text == "/start" || text == "/help") {
        String msg  = "🚪 *FaceTouch Pro*\n\n";
        msg        += "*Lệnh khả dụng:*\n";
        msg        += "/status - Xem trạng thái cửa\n";
        msg        += "/open   - Mở khóa từ xa\n";
        msg        += "/cancel - Hủy yêu cầu đang chờ\n";
        msg        += "\n/help - Hiển thị danh sách lệnh";
        bot.sendMessage(chatId, msg, "Markdown");
    }

    // --- /status ---
    else if (text == "/status") {
        String icon  = isLockOpen() ? "🔓 Khóa: *ĐANG KÍCH MỞ*" : "🔒 Khóa: *ĐANG NGHỈ*";
        icon += isFingerprintConnected() ? "\nVân tay: sẵn sàng." : "\nVân tay: lỗi cảm biến.";
        icon += isMQTTConnected() ? "\nMQTT: đã kết nối." : "\nMQTT: mất kết nối.";
        bot.sendMessage(chatId, icon + " `[" + getTimeStr() + "]`", "Markdown");
    }

    // --- /open (Cả Admin và User được ủy quyền đều dùng được) ---
    else if (text == "/open") {
        s_pendingOpenChat = chatId;
        s_pendingOpenAt = millis();
        bot.sendMessage(chatId, "Xác nhận mở khóa trong 30 giây bằng lệnh /open confirm. Gõ /cancel để hủy.", "");
    }
    else if (text == "/cancel") {
        if (s_pendingOpenChat == chatId) s_pendingOpenChat = "";
        bot.sendMessage(chatId, "Đã hủy yêu cầu đang chờ xác nhận của bạn.", "");
    }
    else if (text == "/open confirm") {
        if (s_pendingOpenChat != chatId || millis() - s_pendingOpenAt > 30000UL) {
            bot.sendMessage(chatId, "Xác nhận đã hết hạn hoặc không hợp lệ. Gửi /open để bắt đầu lại.", "");
            return;
        }
        s_pendingOpenChat = "";
        if (!_lockEventGroup || (s_remoteOpened && millis() - s_lastRemoteOpenAt < 8000UL)) {
            bot.sendMessage(chatId, "Thiết bị đang bận hoặc vừa mở khóa. Vui lòng chờ.", "");
            return;
        }
        if (isLockOpen()) {
            bot.sendMessage(chatId, "🚪 Cửa đang mở sẵn rồi!", "");
        } else {
            bot.sendMessage(chatId, "Đã gửi yêu cầu mở khóa tới bộ điều khiển.", "");

            // OLED feedback is produced by the relay task when it executes.

            // Kích hoạt relay qua Event Group
            if (_lockEventGroup) {
                xEventGroupSetBits(_lockEventGroup, EVT_UNLOCK_TELEGRAM);
                s_lastRemoteOpenAt = millis();
                s_remoteOpened = true;
            }

            String opener = admin ? "Admin" : ("User " + chatId);
            addLog("Mở từ xa (" + opener + ") lúc " + getTimeStr());
        }
    }


    // --- Lệnh không xác định ---
    else {
        bot.sendMessage(chatId, "❓ Lệnh không hợp lệ. Gõ /help để xem danh sách lệnh.", "");
    }
}

// ==========================================
// FREERTOS TASK
// ==========================================

void Task_Telegram(void *pvParameters) {

    // MQTT owns Wi-Fi reconnect and UTC NTP. Do not reconfigure either here.
    if (strlen(BOT_TOKEN) < 20 || String(BOT_TOKEN).indexOf("YOUR_") >= 0 ||
        String(ADMIN_CHAT_ID).indexOf("YOUR_") >= 0) {
        Serial.println("[TELEGRAM] Chua cau hinh bot; khong khoi dong.");
        vTaskDelete(NULL); return;
    }
    while (WiFi.status() != WL_CONNECTED || time(nullptr) < 1700000000) {
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    WiFiClientSecure client;
    client.setCACert(TELEGRAM_CERTIFICATE_ROOT);
    client.setTimeout(3000);
    client.setHandshakeTimeout(4);
    UniversalTelegramBot bot(BOT_TOKEN, client);
    bot.longPoll = 0;

    TelegramNotif  notif;
    unsigned long  lastPoll = 0;

    // ---- Vòng lặp chính ----
    for (;;) {

        if (WiFi.status() != WL_CONNECTED) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue; // MQTT task owns Wi-Fi recovery.
        }

        // A. Gửi push notification từ hardware tasks (non-blocking)
        while (xQueueReceive(telegramNotifQueue, &notif, 0) == pdTRUE) {
            if (millis() - notif.queuedAt > 60000UL) continue;
            // Gửi cho Admin
            bot.sendMessage(ADMIN_CHAT_ID, String(notif.message), "");
            // Alerts go only to the configured administrator, not every remote user.
        }

        // B. Poll lệnh từ Telegram mỗi 1.5 giây
        if (millis() - lastPoll >= 1500) {

            // Tự động reconnect nếu mất WiFi
            if (WiFi.status() != WL_CONNECTED) {
                Serial.println(F("[WiFi] Mat ket noi, dang ket noi lai..."));
                // MQTT task reconnects Wi-Fi; Telegram must not race it.
                vTaskDelay(pdMS_TO_TICKS(5000));
                lastPoll = millis();
                continue;
            }

            int numMessages = bot.getUpdates(bot.last_message_received + 1);
            while (numMessages) {
                for (int i = 0; i < numMessages; i++) {
                    String chatId = bot.messages[i].chat_id;
                    String text   = bot.messages[i].text;
                    String from   = bot.messages[i].from_name;

                    Serial.println("[BOT] '" + text + "' tu: " + from
                                   + " (" + chatId + ")");

                    const time_t now = time(nullptr);
                    const time_t sent = static_cast<time_t>(bot.messages[i].date.toInt());
                    // No old queued /open after outages; private authorized chats only.
                    if (bot.messages[i].type != "message" || bot.messages[i].from_id != chatId ||
                        now < 1700000000 || sent > now + 5 || now - sent > 30) continue;
                    if (!isAuthorized(chatId)) {
                        bot.sendMessage(chatId,
                            "⛔ Bạn không có quyền truy cập hệ thống này!", "");
                        Serial.println("[BOT] Truy cap trai phep: " + chatId);
                        continue;
                    }

                    handleBotCommand(bot, chatId, text);
                }
                numMessages = bot.getUpdates(bot.last_message_received + 1);
            }

            lastPoll = millis();
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

