/**
 * @file Smart_Door.ino
 * @brief Orchestrator chính cho Khóa Cửa Điện Tử (ESP32 + FreeRTOS)
 *
 * File này chỉ chịu trách nhiệm:
 *  1. Khởi tạo phần cứng và các FreeRTOS primitives
 *  2. Tạo và gắn các Task vào đúng CPU core
 *  3. Menu điều khiển vân tay qua Serial Monitor
 *
 * Toàn bộ logic nghiệp vụ nằm trong các Driver:
 *  - Display_Driver   : Giao tiếp OLED SSD1306
 *  - Fingerprint_Driver: Nhận diện vân tay AS608
 *  - Lock_Driver      : Điều khiển Relay + Còi báo
 *  - Telegram_Driver  : WiFi, Bot Telegram, Log, Push Notification
 */

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/event_groups.h>
#include <freertos/queue.h>
#include <time.h>

#include "config.h"
#include "Display_Driver.h"     // DisplayMsg, DisplayMsgType + API màn hình
#include "Fingerprint_Driver.h" // API cảm biến vân tay AS608
#include "Lock_Driver.h"        // API relay khóa cửa + còi báo
#include "Telegram_Driver.h"    // API bot + addLog() + telegramNotifQueue
#include "MQTT_Driver.h"        // Wi-Fi + HiveMQ Cloud + heartbeat

// ==========================================
// FREERTOS HANDLES (dùng chung giữa các Task)
// ==========================================

static QueueHandle_t      displayQueue   = NULL;
static QueueHandle_t      serverUnlockQueue = NULL;
static EventGroupHandle_t lockEventGroup = NULL;

#define EVT_UNLOCK_SERVER   (1 << 3)

// Bit sự kiện mở khóa
#define EVT_UNLOCK_FINGER   (1 << 0)  // Vân tay hợp lệ
#define EVT_UNLOCK_BUTTON   (1 << 1)  // Nút nhấn bên trong
// EVT_UNLOCK_TELEGRAM     (1 << 2)  // Định nghĩa trong Telegram_Driver.h

// ==========================================
// TASK 1: ĐIỀU KHIỂN RELAY + CÒI BÁO
// Core 1 — Priority 4 (Cao nhất, đảm bảo relay phản hồi tức thì)
// ==========================================
void Task_LockControl(void *pvParameters) {
    for (;;) {
        EventBits_t event = xEventGroupWaitBits(
            lockEventGroup,
            EVT_UNLOCK_FINGER | EVT_UNLOCK_BUTTON | EVT_UNLOCK_TELEGRAM | EVT_UNLOCK_SERVER,
            pdTRUE,        // Tự động xóa bit sau khi nhận
            pdFALSE,       // Nhận BẤT KỲ bit nào trong mask
            portMAX_DELAY  // Chờ vô hạn
        );

        if (event & (EVT_UNLOCK_FINGER | EVT_UNLOCK_BUTTON | EVT_UNLOCK_TELEGRAM)) {
            alertSuccess();
            openLock();
            vTaskDelay(pdMS_TO_TICKS(UNLOCK_DURATION));
            closeLock();

            DisplayMsg homeMsg = { DISP_HOME, 0 };
            xQueueSend(displayQueue, &homeMsg, 0);
        }

        if (event & EVT_UNLOCK_SERVER) {
            ServerUnlockRequest request;
            while (xQueueReceive(serverUnlockQueue, &request, 0) == pdTRUE) {
                const int64_t now = static_cast<int64_t>(time(nullptr));
                if (now <= 0 || now > request.expiresAt) {
                    Serial.printf("[LOCK] Tu choi OPEN %s: lenh da het han.\n", request.commandId);
                    queueMQTTAck(request.commandId, "OPEN", "failed", "expired_before_execution");
                    continue;
                }

                Serial.printf(
                    "[LOCK] OPEN %s trong %lu ms.\n",
                    request.commandId,
                    static_cast<unsigned long>(request.durationMs)
                );
                queueMQTTAck(request.commandId, "OPEN", "started");

                DisplayMsg serverMsg = { DISP_SUCCESS, -1 };
                xQueueSend(displayQueue, &serverMsg, 0);
                alertSuccess();
                openLock();
                vTaskDelay(pdMS_TO_TICKS(request.durationMs));
                closeLock();

                queueMQTTAck(request.commandId, "OPEN", "finished");
                DisplayMsg homeMsg = { DISP_HOME, 0 };
                xQueueSend(displayQueue, &homeMsg, 0);
            }
        }
    }
}

// ==========================================
// TASK 2: QUÉT VÂN TAY AS608
// Core 1 — Priority 3
// ==========================================
void Task_Fingerprint(void *pvParameters) {
    static unsigned long lastWarningTime = 0;

    for (;;) {
        if (!isLockOpen() && isFingerprintConnected()) {
            int result = checkFingerprint();

            if (result > 0) {
                // ==========================================
                // 1. VÂN TAY HỢP LỆ -> MỞ CỬA
                // ==========================================
                Serial.print(F("[AUTH] Chap nhan: ID #"));
                Serial.println(result);

                DisplayMsg msg = { DISP_SUCCESS, result };
                xQueueSend(displayQueue, &msg, 0);
                xEventGroupSetBits(lockEventGroup, EVT_UNLOCK_FINGER);

                String fpName = getFingerprintName(result);
                char msgBuf[TELEGRAM_NOTIF_MAX_LEN];
                if (fpName.length() > 0) {
                    snprintf(msgBuf, sizeof(msgBuf), "🚪 Mở cửa - %s (ID #%d) lúc %s", fpName.c_str(), result, getTimeStr().c_str());
                    addLog("Mở cửa: " + fpName + " (ID #" + String(result) + ")");
                } else {
                    snprintf(msgBuf, sizeof(msgBuf), "🚪 Mở cửa - Vân tay #%d lúc %s", result, getTimeStr().c_str());
                    addLog("Mở cửa: Vân tay #" + String(result));
                }

                if (telegramNotifQueue) {
                    TelegramNotif notif;
                    strncpy(notif.message, msgBuf, sizeof(notif.message) - 1);
                    notif.message[sizeof(notif.message) - 1] = '\0';
                    xQueueSend(telegramNotifQueue, &notif, 0);
                }

                vTaskDelay(pdMS_TO_TICKS(UNLOCK_DURATION + 300));
            }
            else if (result == FP_NOT_FOUND) {
                // ==========================================
                // 2. VÂN TAY KHÔNG HỢP LỆ (LẠ) -> BÁO ĐỘNG
                // ==========================================
                Serial.println(F("[AUTH] Tu choi: Van tay khong hop le!"));
                alertError();

                DisplayMsg errMsg = { DISP_ERROR, 0 };
                xQueueSend(displayQueue, &errMsg, 0);

                // Giới hạn gửi cảnh báo tối thiểu cách nhau 5 giây để tránh spam
                if (millis() - lastWarningTime > 5000) {
                    if (telegramNotifQueue) {
                        TelegramNotif notif;
                        snprintf(notif.message, TELEGRAM_NOTIF_MAX_LEN,
                                 "⚠️ CẢNH BÁO! Vân tay lạ bị từ chối lúc %s.", getTimeStr().c_str());
                        xQueueSend(telegramNotifQueue, &notif, 0);
                    }
                    addLog("⚠️ Cảnh báo: Vân tay lạ");
                    lastWarningTime = millis();
                }

                vTaskDelay(pdMS_TO_TICKS(ERROR_DURATION));
                clearErrorAlert();

                DisplayMsg homeMsg = { DISP_HOME, 0 };
                xQueueSend(displayQueue, &homeMsg, 0);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50)); // Chu kỳ 50ms cho độ nhạy mượt mà
    }
}



// ==========================================
// TASK 3: HIỂN THỊ OLED SSD1306
// Core 1 — Priority 2
// ==========================================
void Task_Display(void *pvParameters) {
    DisplayMsg msg;
    for (;;) {
        if (xQueueReceive(displayQueue, &msg, portMAX_DELAY) == pdTRUE) {
            switch (msg.type) {
                case DISP_HOME:         showHomeScreen();           break;
                case DISP_SUCCESS:      showSuccessScreen(msg.id);  break;
                case DISP_ERROR:        showErrorScreen();          break;
                case DISP_SENSOR_ERROR: showSensorErrorScreen();    break;
            }
        }
    }
}

// ==========================================
// TASK 4: NÚT NHẤN MỞ CỬA TỪ BÊN TRONG (GPIO15)
// Core 1 — Priority 3
// ==========================================
void Task_Button(void *pvParameters) {
    pinMode(BUTTON_INSIDE_PIN, INPUT_PULLUP);
    bool lastState = HIGH;

    for (;;) {
        bool currentState = digitalRead(BUTTON_INSIDE_PIN);

        if (lastState == HIGH && currentState == LOW) {
            vTaskDelay(pdMS_TO_TICKS(50)); // Debounce
            if (digitalRead(BUTTON_INSIDE_PIN) == LOW && !isLockOpen()) {
                Serial.println(F("[BUTTON] Nhan nut mo cua tu ben trong!"));

                DisplayMsg msg = { DISP_SUCCESS, 0 }; // id=0: nút nhấn
                xQueueSend(displayQueue, &msg, 0);
                xEventGroupSetBits(lockEventGroup, EVT_UNLOCK_BUTTON);

                if (telegramNotifQueue) {
                    TelegramNotif notif;
                    snprintf(notif.message, TELEGRAM_NOTIF_MAX_LEN,
                             "🚪 Mở cửa từ nút nhấn bên trong lúc %s.", getTimeStr().c_str());
                    xQueueSend(telegramNotifQueue, &notif, 0);
                }
                addLog("Nút nhấn bên trong lúc " + getTimeStr());

                vTaskDelay(pdMS_TO_TICKS(UNLOCK_DURATION + 300));
            }
        }
        lastState = currentState;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}


// ==========================================
// MENU QUẢN LÝ VÂN TAY (SERIAL MONITOR)
// ==========================================
void printMenu() {
    Serial.println(F("\n=========================================="));
    Serial.println(F("       MENU QUAN LY VAN TAY (SERIAL)     "));
    Serial.println(F("=========================================="));
    Serial.println(F(" [e] Dang ky (Enroll) van tay moi"));
    Serial.println(F(" [d] Xoa (Delete) mot ID van tay"));
    Serial.println(F(" [c] Xem so luong (Count) van tay da luu"));
    Serial.println(F(" [x] Xoa trang toan bo co so du lieu"));
    Serial.println(F(" [h] In lai menu nay"));
    Serial.println(F("=========================================="));
}

// ==========================================
// SETUP
// ==========================================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n=========================================="));
    Serial.println(F("   KHOA CUA DIEN TU THONG MINH (ESP32)   "));
    Serial.println(F("=========================================="));
    Serial.printf(
        "[SYSTEM] Flash: %u MB | PSRAM: %u MB\n",
        static_cast<unsigned int>(ESP.getFlashChipSize() / (1024U * 1024U)),
        static_cast<unsigned int>(ESP.getPsramSize() / (1024U * 1024U))
    );
    if (ESP.getFlashChipSize() != 16U * 1024U * 1024U || ESP.getPsramSize() == 0) {
        Serial.println(F("[SYSTEM] CANH BAO: Cau hinh bo nho khong khop ESP32-S3 N16R8!"));
    }

    // 1. Khởi tạo phần cứng
    initLock();
    initDisplay();
    bool fpOk = initFingerprint();

    // 2. Khởi tạo FreeRTOS primitives
    lockEventGroup = xEventGroupCreate();
    displayQueue   = xQueueCreate(5, sizeof(DisplayMsg));
    serverUnlockQueue = xQueueCreate(SERVER_UNLOCK_QUEUE_LENGTH, sizeof(ServerUnlockRequest));

    if (!lockEventGroup || !displayQueue || !serverUnlockQueue) {
        Serial.println(F("[SYSTEM] LOI: Khong tao duoc FreeRTOS primitive."));
        for (;;) delay(1000);
    }

    if (!fpOk) {
        DisplayMsg errMsg = { DISP_SENSOR_ERROR, 0 };
        xQueueSend(displayQueue, &errMsg, 0);
    }

    // 3. Khởi tạo Telegram Driver (tạo queue + mutex nội bộ)
    initTelegram(displayQueue, lockEventGroup);
    initMQTT(serverUnlockQueue, lockEventGroup, EVT_UNLOCK_SERVER);

    // 4. Tạo Task phần cứng — Core 1
    xTaskCreatePinnedToCore(Task_LockControl, "Lock",    2048, NULL, 4, NULL, 1);
    xTaskCreatePinnedToCore(Task_Fingerprint, "FP",      4096, NULL, 3, NULL, 1);
    xTaskCreatePinnedToCore(Task_Button,      "Btn",     2048, NULL, 3, NULL, 1);
    xTaskCreatePinnedToCore(Task_Display,     "Display", 4096, NULL, 2, NULL, 1);

    // 5. MQTT Cloud — Core 0. Giai đoạn này chỉ gửi trạng thái/heartbeat.
    xTaskCreatePinnedToCore(Task_MQTT,        "MQTT",     8192, NULL, 1, NULL, 0);

    // Telegram cũ tạm tắt trong quá trình chuyển quyền quản lý lên server.
#if ENABLE_LEGACY_TELEGRAM
    xTaskCreatePinnedToCore(Task_Telegram,    "Telegram", 12288, NULL, 1, NULL, 0);
#endif

    Serial.println(F("[SYSTEM] Tat ca Task da khoi chay!"));
    printMenu();
}

// ==========================================
// LOOP — Menu Serial tương tác
// ==========================================
void loop() {
    if (Serial.available() > 0) {
        char cmd = Serial.read();
        if (cmd == '\r' || cmd == '\n' || cmd == ' ') return;

        if (cmd == 'e' || cmd == 'E') {
            Serial.println(F("\n--- DANG KY VAN TAY MOI ---"));
            Serial.println(F("Nhap ID (1-127) va nhan Enter:"));
            while (Serial.available()) Serial.read();
            while (!Serial.available()) vTaskDelay(pdMS_TO_TICKS(50));
            int id = Serial.parseInt();
            while (Serial.available()) Serial.read();
            if (id >= 1 && id <= 127) enrollFingerprint((uint8_t)id);
            else Serial.println(F("ID khong hop le!"));
            printMenu();
        }
        else if (cmd == 'd' || cmd == 'D') {
            Serial.println(F("\n--- XOA MOT ID VAN TAY ---"));
            Serial.println(F("Nhap ID (1-127) va nhan Enter:"));
            while (Serial.available()) Serial.read();
            while (!Serial.available()) vTaskDelay(pdMS_TO_TICKS(50));
            int id = Serial.parseInt();
            while (Serial.available()) Serial.read();
            if (id >= 1 && id <= 127) deleteFingerprint((uint8_t)id);
            else Serial.println(F("ID khong hop le!"));
            printMenu();
        }
        else if (cmd == 'c' || cmd == 'C') {
            Serial.print(F("[AS608] So van tay da luu: "));
            Serial.println(getFingerprintCount());
            printMenu();
        }
        else if (cmd == 'x' || cmd == 'X') {
            Serial.println(F("Ban co chac? (Y/N):"));
            while (Serial.available()) Serial.read();
            while (!Serial.available()) vTaskDelay(pdMS_TO_TICKS(50));
            char confirm = Serial.read();
            while (Serial.available()) Serial.read();
            if (confirm == 'y' || confirm == 'Y') emptyAllFingerprints();
            else Serial.println(F("Da huy!"));
            printMenu();
        }
        else if (cmd == 'h' || cmd == 'H') {
            printMenu();
        }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
}
