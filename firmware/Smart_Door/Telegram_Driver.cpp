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

// ==========================================
// QUẢN LÝ NGƯỜI DÙNG ỦY QUYỀN (NVS)
// ==========================================

static void loadAuthorizedUsers() {
    s_authorizedUsers.clear();
    prefUsers.begin("door_users", true); // read-only
    int count = prefUsers.getInt("count", 0);
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
    if (!getLocalTime(&timeinfo)) return "--:--:--";
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

static UniversalTelegramBot *s_pBot = nullptr;
static String s_activeChatId;

static void telegramEnrollProgress(const char* msg) {
    if (s_pBot && s_activeChatId.length() > 0) {
        s_pBot->sendMessage(s_activeChatId, msg, "");
    }
}

// ==========================================
// XỬ LÝ LỆNH BOT
// ==========================================

static void handleBotCommand(UniversalTelegramBot &bot,
                              const String &chatId,
                              const String &text) {
    bool admin = isAdmin(chatId);

    // --- /start hoặc /help ---
    if (text == "/start" || text == "/help") {
        String msg  = "🚪 *SMART DOOR CONTROLLER*\n\n";
        msg        += "*Lệnh khả dụng:*\n";
        msg        += "/status - Xem trạng thái cửa\n";
        msg        += "/open   - Mở khóa từ xa\n";
        if (admin) {
            msg    += "\n*👑 Quản lý hệ thống (Admin):*\n";
            msg    += "/log - Xem lịch sử sự kiện gần nhất\n";
            msg    += "/count - Xem số lượng vân tay đã lưu\n";
            msg    += "/add <ID> - Đăng ký vân tay mới (VD: `/add 1`)\n";
            msg    += "/setname <ID> <Tên> - Đặt tên cho vân tay (VD: `/setname 1 Nguyễn Văn A`)\n";
            msg    += "/delete <ID> - Xóa 1 vân tay (VD: `/delete 1`)\n";
            msg    += "/clearall - Xóa TOÀN BỘ bộ nhớ vân tay\n";
            msg    += "/adduser <Chat_ID> - Ủy quyền cho người dùng mới\n";
            msg    += "/removeuser <Chat_ID> - Hủy ủy quyền người dùng\n";
            msg    += "/listuser - Xem danh sách người dùng được ủy quyền\n";
        }
        msg        += "\n/help - Hiển thị danh sách lệnh";
        bot.sendMessage(chatId, msg, "Markdown");
    }

    // --- /status ---
    else if (text == "/status") {
        String icon  = isLockOpen() ? "🚪 Cửa: *ĐANG MỞ*" : "🚪 Cửa: *ĐANG ĐÓNG*";
        bot.sendMessage(chatId, icon + " `[" + getTimeStr() + "]`", "Markdown");
    }

    // --- /open (Cả Admin và User được ủy quyền đều dùng được) ---
    else if (text == "/open") {
        if (isLockOpen()) {
            bot.sendMessage(chatId, "🚪 Cửa đang mở sẵn rồi!", "");
        } else {
            bot.sendMessage(chatId, "🔓 Đang mở cửa...", "");

            // Gửi lệnh hiển thị OLED: id=-1 = "Mo Cua Tu Xa"
            if (_displayQueue) {
                DisplayMsg dispMsg = { DISP_SUCCESS, -1 };
                xQueueSend(_displayQueue, &dispMsg, 0);
            }

            // Kích hoạt relay qua Event Group
            if (_lockEventGroup) {
                xEventGroupSetBits(_lockEventGroup, EVT_UNLOCK_TELEGRAM);
            }

            String opener = admin ? "Admin" : ("User " + chatId);
            addLog("Mở từ xa (" + opener + ") lúc " + getTimeStr());
        }
    }

    // --- /log (Admin only) ---
    else if (text == "/log") {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xem nhật ký sự kiện!", "");
            return;
        }
        bot.sendMessage(chatId, getLogString(), "Markdown");
    }

    // --- /count (Admin only) ---
    else if (text == "/count") {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xem số lượng vân tay!", "");
            return;
        }
        uint16_t count = getFingerprintCount();
        bot.sendMessage(chatId, "📊 Số lượng vân tay hiện có trong bộ nhớ: *" + String(count) + "/127*", "Markdown");
    }

    // --- /add <ID> (Admin only) ---
    else if (text.startsWith("/add ")) {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền đăng ký vân tay!", "");
            return;
        }
        int spaceIdx = text.indexOf(' ');
        if (spaceIdx == -1) {
            bot.sendMessage(chatId, "⚠️ Cú pháp: `/add <ID (1-127)>`\nVí dụ: `/add 1`", "Markdown");
            return;
        }
        int id = text.substring(spaceIdx + 1).toInt();
        if (id < 1 || id > 127) {
            bot.sendMessage(chatId, "❌ ID không hợp lệ! Chỉ chấp nhận ID từ 1 đến 127.", "");
            return;
        }

        s_pBot = &bot;
        s_activeChatId = chatId;
        enrollFingerprint((uint8_t)id, telegramEnrollProgress);
    }

    // --- /setname <ID> <Tên> (Admin only) ---
    else if (text.startsWith("/setname")) {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền đặt tên cho vân tay!", "");
            return;
        }
        int firstSpace = text.indexOf(' ');
        if (firstSpace == -1) {
            bot.sendMessage(chatId, "⚠️ Cú pháp: `/setname <ID> <Tên>`\nVí dụ: `/setname 1 Nguyễn Văn Lộc`", "Markdown");
            return;
        }
        int secondSpace = text.indexOf(' ', firstSpace + 1);
        if (secondSpace == -1) {
            bot.sendMessage(chatId, "⚠️ Vui lòng nhập cả ID và Tên!\nVí dụ: `/setname 1 Nguyễn Văn Lộc`", "Markdown");
            return;
        }
        int id = text.substring(firstSpace + 1, secondSpace).toInt();
        String name = text.substring(secondSpace + 1);
        name.trim();

        if (id < 1 || id > 127 || name.length() == 0) {
            bot.sendMessage(chatId, "❌ Dữ liệu không hợp lệ! ID phải từ 1 đến 127 và tên không được để trống.", "");
            return;
        }

        setFingerprintName(id, name);
        bot.sendMessage(chatId, "✅ Đã gán tên cho vân tay ID #" + String(id) + ": *" + name + "*", "Markdown");
        addLog("Đặt tên ID #" + String(id) + " -> " + name);
    }

    // --- /delete <ID> (Admin only) ---
    else if (text.startsWith("/delete")) {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xóa vân tay!", "");
            return;
        }
        int spaceIdx = text.indexOf(' ');
        if (spaceIdx == -1) {
            bot.sendMessage(chatId, "⚠️ Cú pháp: `/delete <ID (1-127)>`\nVí dụ: `/delete 1`", "Markdown");
            return;
        }
        int id = text.substring(spaceIdx + 1).toInt();
        if (id < 1 || id > 127) {
            bot.sendMessage(chatId, "❌ ID không hợp lệ!", "");
            return;
        }
        if (deleteFingerprint((uint8_t)id)) {
            removeFingerprintName(id);
            bot.sendMessage(chatId, "✅ Đã xóa thành công vân tay ID #" + String(id) + "!", "");
            addLog("Xóa vân tay #" + String(id));
        } else {
            bot.sendMessage(chatId, "❌ Không thể xóa vân tay ID #" + String(id) + " (có thể ID chưa tồn tại)!", "");
        }
    }

    // --- /clearall (Admin only) ---
    else if (text == "/clearall") {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xóa toàn bộ vân tay!", "");
            return;
        }
        bot.sendMessage(chatId, "⚠️ *CẢNH BÁO!* Hành động này sẽ *XÓA TRẮNG* toàn bộ vân tay và tên đã lưu.\nGõ `/clearall confirm` để xác nhận.", "Markdown");
    }
    else if (text == "/clearall confirm") {
        if (!admin) return;
        if (emptyAllFingerprints()) {
            clearAllFingerprintNames();
            bot.sendMessage(chatId, "🗑️ Đã *XÓA TRẮNG* toàn bộ cơ sở dữ liệu vân tay!", "Markdown");
            addLog("Xóa toàn bộ vân tay");
        } else {
            bot.sendMessage(chatId, "❌ Xóa toàn bộ vân tay THẤT BẠI!", "");
        }
    }

    // --- /adduser <Chat_ID> (Admin only) ---
    else if (text.startsWith("/adduser")) {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền ủy quyền người dùng!", "");
            return;
        }
        int spaceIdx = text.indexOf(' ');
        if (spaceIdx == -1) {
            bot.sendMessage(chatId, "⚠️ Cú pháp: `/adduser <Chat_ID>`\nVí dụ: `/adduser 11223344`", "Markdown");
            return;
        }
        String newChatId = text.substring(spaceIdx + 1);
        newChatId.trim();

        if (newChatId.length() == 0 || newChatId == String(ADMIN_CHAT_ID)) {
            bot.sendMessage(chatId, "❌ Chat ID không hợp lệ hoặc đã là Admin!", "");
            return;
        }

        if (isUserAuthorized(newChatId)) {
            bot.sendMessage(chatId, "ℹ️ Chat ID `" + newChatId + "` đã có trong danh sách ủy quyền rồi.", "Markdown");
            return;
        }

        s_authorizedUsers.push_back(newChatId);
        saveAuthorizedUsers();
        bot.sendMessage(chatId, "✅ Đã thêm người dùng `" + newChatId + "` vào danh sách ủy quyền!", "Markdown");
        addLog("Thêm user ủy quyền: " + newChatId);
    }

    // --- /removeuser <Chat_ID> (Admin only) ---
    else if (text.startsWith("/removeuser")) {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xóa người dùng!", "");
            return;
        }
        int spaceIdx = text.indexOf(' ');
        if (spaceIdx == -1) {
            bot.sendMessage(chatId, "⚠️ Cú pháp: `/removeuser <Chat_ID>`\nVí dụ: `/removeuser 11223344`", "Markdown");
            return;
        }
        String targetChatId = text.substring(spaceIdx + 1);
        targetChatId.trim();

        bool found = false;
        for (auto it = s_authorizedUsers.begin(); it != s_authorizedUsers.end(); ++it) {
            if (*it == targetChatId) {
                s_authorizedUsers.erase(it);
                found = true;
                break;
            }
        }

        if (found) {
            saveAuthorizedUsers();
            bot.sendMessage(chatId, "✅ Đã xóa người dùng `" + targetChatId + "` khỏi danh sách ủy quyền.", "Markdown");
            addLog("Xóa user ủy quyền: " + targetChatId);
        } else {
            bot.sendMessage(chatId, "❌ Không tìm thấy Chat ID `" + targetChatId + "` trong danh sách!", "Markdown");
        }
    }

    // --- /listuser (Admin only) ---
    else if (text == "/listuser") {
        if (!admin) {
            bot.sendMessage(chatId, "⛔ Chỉ Admin mới có quyền xem danh sách người dùng!", "");
            return;
        }
        String msg = "👥 *DANH SÁCH NGƯỜI DÙNG ĐƯỢC ỦY QUYỀN:*\n\n";
        msg += "👑 *Admin:* `" + String(ADMIN_CHAT_ID) + "`\n";
        if (s_authorizedUsers.empty()) {
            msg += "\n_Chưa có thành viên phụ nào được ủy quyền._";
        } else {
            msg += "\n*Thành viên phụ (" + String(s_authorizedUsers.size()) + "):*\n";
            for (size_t i = 0; i < s_authorizedUsers.size(); i++) {
                msg += String(i + 1) + ". `" + s_authorizedUsers[i] + "`\n";
            }
        }
        bot.sendMessage(chatId, msg, "Markdown");
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

    // ---- 1. Kết nối WiFi ----
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print(F("[WiFi] Dang ket noi"));

    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries < 40) {
        vTaskDelay(pdMS_TO_TICKS(500));
        Serial.print(".");
        tries++;
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println(F("\n[WiFi] THAT BAI! Bot se khong hoat dong."));
        vTaskDelete(NULL);
        return;
    }

    Serial.print(F("\n[WiFi] Da ket noi! IP: "));
    Serial.println(WiFi.localIP());

    // ---- 2. Đồng bộ thời gian NTP (UTC+7 Việt Nam) ----
    configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");
    Serial.print(F("[NTP] Dang dong bo thoi gian"));
    struct tm timeinfo;
    int ntpTries = 0;
    while (!getLocalTime(&timeinfo) && ntpTries < 20) {
        vTaskDelay(pdMS_TO_TICKS(500));
        Serial.print(".");
        ntpTries++;
    }
    Serial.println(getLocalTime(&timeinfo)
                   ? F("\n[NTP] Dong bo OK!")
                   : F("\n[NTP] Khong dong bo duoc!"));

    // ---- 3. Khởi tạo Telegram Bot ----
    WiFiClientSecure client;
    client.setInsecure(); // Bỏ qua SSL cert (đơn giản cho IoT)
    UniversalTelegramBot bot(BOT_TOKEN, client);

    // Thông báo khởi động cho Admin
    bot.sendMessage(ADMIN_CHAT_ID,
        "🔔 *Smart Door ĐÃ SẴN SÀNG (ONLINE)!*\n⏰ Hệ thống khởi động lúc: `" + getTimeStr() +
        "`\n👉 Gõ /help để xem danh sách lệnh.", "Markdown");

    TelegramNotif  notif;
    unsigned long  lastPoll = 0;

    // ---- Vòng lặp chính ----
    for (;;) {

        // A. Gửi push notification từ hardware tasks (non-blocking)
        while (xQueueReceive(telegramNotifQueue, &notif, 0) == pdTRUE) {
            // Gửi cho Admin
            bot.sendMessage(ADMIN_CHAT_ID, String(notif.message), "");
            // Gửi cho tất cả User được ủy quyền
            for (const String &u : s_authorizedUsers) {
                bot.sendMessage(u, String(notif.message), "");
            }
        }

        // B. Poll lệnh từ Telegram mỗi 1.5 giây
        if (millis() - lastPoll >= 1500) {

            // Tự động reconnect nếu mất WiFi
            if (WiFi.status() != WL_CONNECTED) {
                Serial.println(F("[WiFi] Mat ket noi, dang ket noi lai..."));
                WiFi.reconnect();
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

