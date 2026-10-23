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
static QueueHandle_t      fingerprintCommandQueue = NULL;
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
            if (event & EVT_UNLOCK_TELEGRAM) postDisplay(DISP_SUCCESS, -2);
            alertSuccess();
            openLock();
            if (event & EVT_UNLOCK_TELEGRAM) queueMQTTEvent("telegram_open", "telegram");
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

                DisplayMsg serverMsg = { request.faceRecognition ? DISP_FACE_SUCCESS : DISP_SUCCESS, -1 };
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
    static uint32_t deniedTimes[3] = {};
    static unsigned deniedCount = 0;
    static bool warningSent = false;
    static unsigned long lastReconnectAttempt = 0;
    bool sensorWasConnected = isFingerprintConnected();

    for (;;) {
        FingerprintCommandRequest managementRequest;
        if (xQueueReceive(fingerprintCommandQueue, &managementRequest, 0) == pdTRUE) {
            const int64_t now = static_cast<int64_t>(time(nullptr));
            const bool enroll =
                strcmp(managementRequest.command, "FINGERPRINT_ENROLL") == 0;
            const bool inventory =
                strcmp(managementRequest.command, "FINGERPRINT_SYNC") == 0;
            if (now <= 0 || now > managementRequest.expiresAt) {
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "failed", "expired_before_execution"
                );
            } else if (!isFingerprintConnected()) {
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "failed", "sensor_offline"
                );
            } else if (inventory) {
                postDisplay(DISP_FP_SYNC);
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command, "started"
                );
                uint8_t slots[16] = {};
                uint16_t foundCount = 0;
                for (uint8_t id = 1; id <= 127; ++id) {
                    if (fingerprintExists(id)) {
                        slots[id / 8] |= (1U << (id % 8));
                        ++foundCount;
                    }
                    vTaskDelay(pdMS_TO_TICKS(2));
                }
                const uint16_t expectedCount = getFingerprintCount();
                if (foundCount != expectedCount) {
                    queueMQTTAck(
                        managementRequest.commandId, managementRequest.command,
                        "failed", "inventory_scan_incomplete"
                    );
                    continue;
                }
                static const char hex[] = "0123456789abcdef";
                char encoded[33];
                for (size_t i = 0; i < sizeof(slots); ++i) {
                    encoded[i * 2] = hex[(slots[i] >> 4) & 0x0F];
                    encoded[i * 2 + 1] = hex[slots[i] & 0x0F];
                }
                encoded[32] = '\0';
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "finished", nullptr, encoded
                );
                Serial.printf("[AS608] Da dong bo %u mau voi server.\n", foundCount);
                postDisplay(DISP_FP_SYNCED);
            } else if (enroll && fingerprintExists(managementRequest.fingerprintId)) {
                postDisplay(DISP_FP_FAILED, -2);
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "failed", "fingerprint_id_exists"
                );
            } else if (!enroll && !fingerprintExists(managementRequest.fingerprintId)) {
                postDisplay(DISP_FP_FAILED, managementRequest.fingerprintId);
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "failed", "fingerprint_id_not_found"
                );
            } else {
                if (!enroll) postDisplay(DISP_FP_DELETING, managementRequest.fingerprintId);
                if (enroll) {
                    DisplayMsg workingMsg = {
                        DISP_FP_ENROLL, managementRequest.fingerprintId
                    };
                    xQueueSend(displayQueue, &workingMsg, 0);
                }
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    "started"
                );
                const bool succeeded = enroll
                    ? enrollFingerprint(managementRequest.fingerprintId)
                    : deleteFingerprint(managementRequest.fingerprintId);
                const char* failureReason = "sensor_operation_failed";
                if (!succeeded && enroll) {
                    const char* enrollError = getLastFingerprintEnrollError();
                    if (enrollError != nullptr) failureReason = enrollError;
                }
                queueMQTTAck(
                    managementRequest.commandId, managementRequest.command,
                    succeeded ? "finished" : "failed",
                    succeeded ? nullptr : failureReason
                );
                if (succeeded) {
                    queueMQTTEvent(
                        enroll ? "fingerprint_enrolled" : "fingerprint_deleted",
                        "server_management",
                        managementRequest.fingerprintId
                    );
                }
                int displayId = managementRequest.fingerprintId;
                if (!succeeded && enroll && strcmp(failureReason, "fingerprint_already_registered") == 0) displayId = -2;
                if (!succeeded && enroll && strcmp(failureReason, "enrollment_timeout") == 0) displayId = -3;
                DisplayMsg resultMsg = {
                    succeeded
                        ? (enroll ? DISP_FP_SAVED : DISP_FP_DELETED)
                        : DISP_FP_FAILED,
                    displayId
                };
                xQueueSend(displayQueue, &resultMsg, 0);
                vTaskDelay(pdMS_TO_TICKS(2500));
                DisplayMsg homeMsg = { DISP_HOME, 0 };
                xQueueSend(displayQueue, &homeMsg, 0);
            }
        }

        bool sensorConnected = isFingerprintConnected();
        if (!sensorConnected && millis() - lastReconnectAttempt >= 10000UL) {
            lastReconnectAttempt = millis();
            sensorConnected = tryReconnectFingerprint();
        }
        if (sensorConnected != sensorWasConnected) {
            sensorWasConnected = sensorConnected;
            queueMQTTEvent(
                sensorConnected ? "sensor_online" : "sensor_offline",
                "fingerprint_sensor"
            );
            DisplayMsg sensorMsg = {
                sensorConnected ? DISP_HOME : DISP_SENSOR_ERROR,
                0
            };
            xQueueSend(displayQueue, &sensorMsg, 0);
        }

        if (!isLockOpen() && sensorConnected) {
            int result = checkFingerprint();

            if (result > 0) {
                deniedCount = 0;
                // ==========================================
                // 1. VÂN TAY HỢP LỆ -> MỞ CỬA
                // ==========================================
                Serial.print(F("[AUTH] Chap nhan: ID #"));
                Serial.println(result);

                DisplayMsg msg = { DISP_SUCCESS, result };
                xQueueSend(displayQueue, &msg, 0);
                xEventGroupSetBits(lockEventGroup, EVT_UNLOCK_FINGER);

                String fpName = getFingerprintName(result);
                queueMQTTEvent(
                    "fingerprint_granted",
                    "fingerprint",
                    static_cast<int16_t>(result)
                );
                char msgBuf[TELEGRAM_NOTIF_MAX_LEN];
                if (fpName.length() > 0) {
                    snprintf(msgBuf, sizeof(msgBuf), "🚪 Mở cửa - %s (ID #%d) lúc %s", fpName.c_str(), result, getTimeStr().c_str());
                    addLog("Mở cửa: " + fpName + " (ID #" + String(result) + ")");
                } else {
                    snprintf(msgBuf, sizeof(msgBuf), "🚪 Mở cửa - Vân tay #%d lúc %s", result, getTimeStr().c_str());
                    addLog("Mở cửa: Vân tay #" + String(result));
                }

                // Ordinary successful scans are logged on the server, not Telegram.

                vTaskDelay(pdMS_TO_TICKS(UNLOCK_DURATION + 300));
            }
            else if (result == FP_NOT_FOUND) {
                // ==========================================
                // 2. VÂN TAY KHÔNG HỢP LỆ (LẠ) -> BÁO ĐỘNG
                // ==========================================
                Serial.println(F("[AUTH] Tu choi: Van tay khong hop le!"));
                queueMQTTEvent("fingerprint_denied", "fingerprint");
                alertError();

                DisplayMsg errMsg = { DISP_ERROR, 0 };
                xQueueSend(displayQueue, &errMsg, 0);

                uint32_t nowWarning = millis();
                unsigned kept = 0;
                for (unsigned i = 0; i < deniedCount; ++i)
                    if (nowWarning - deniedTimes[i] <= 60000UL) deniedTimes[kept++] = deniedTimes[i];
                deniedCount = kept;
                if (deniedCount < 3) deniedTimes[deniedCount++] = nowWarning;
                if (deniedCount >= 3 && (!warningSent || nowWarning - lastWarningTime >= 60000UL)) {
                    if (telegramNotifQueue) {
                        TelegramNotif notif;
                        notif.queuedAt = millis();
                        snprintf(notif.message, TELEGRAM_NOTIF_MAX_LEN,
                                 "⚠️ Có ít nhất 3 lượt quét vân tay không hợp lệ trong 60 giây tại cửa chính, lúc %s.", getTimeStr().c_str());
                        xQueueSend(telegramNotifQueue, &notif, 0);
                    }
                    addLog("⚠️ Cảnh báo: Vân tay lạ");
                    lastWarningTime = millis();
                    warningSent = true;
                    deniedCount = 0;
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
    DisplayMsg active = {DISP_HOME, 0};
    uint32_t shownAt = millis();
    uint32_t holdMs = 0;
    bool sensorError = false;
    renderDisplay(active);
    for (;;) {
        if (readDisplay(msg, pdMS_TO_TICKS(250))) {
            if (msg.type == DISP_SENSOR_ERROR) sensorError = true;
            else if (msg.type == DISP_HOME && isFingerprintConnected()) sensorError = false;
            const bool management = active.type == DISP_FP_ENROLL || active.type == DISP_FP_LIFT || active.type == DISP_FP_SECOND || active.type == DISP_FP_DELETING || active.type == DISP_FP_SYNC;
            const bool faceNotice = msg.type >= DISP_FACE_CHECK && msg.type != DISP_FACE_SUCCESS;
            const bool holding = millis() - shownAt < holdMs;
            if (holding && msg.type == DISP_HOME) continue;
            // Local fingerprint workflows and relay feedback have priority.
            if (holding && faceNotice) {
                if (management || active.type == DISP_SUCCESS || active.type == DISP_ERROR ||
                    (active.type >= DISP_FP_SAVED && active.type <= DISP_FP_SYNCED)) continue;
                if (active.type == DISP_FACE_SUCCESS) continue;
                if (active.type == DISP_FACE_DELETED && msg.type != DISP_FACE_DELETED) continue;
                if (msg.type == DISP_FACE_CHECK && (active.type == DISP_FACE_VERIFIED || active.type == DISP_FACE_DENIED)) continue;
            }
            active = msg;
            shownAt = millis();
            holdMs = msg.type == DISP_FACE_DENIED ? 4000 : (msg.type == DISP_FP_ENROLL || msg.type == DISP_FP_SECOND) ? 18000 : msg.type == DISP_FP_LIFT ? 7000 : msg.type == DISP_FP_SYNC ? 30000 : msg.type >= DISP_FACE_ENROLL && msg.type <= DISP_FACE_SAVING ? 6000 : msg.type == DISP_HOME ? 0 : 3000;
        }
        if (active.type != DISP_HOME && millis() - shownAt >= holdMs) {
            active = {sensorError ? DISP_SENSOR_ERROR : DISP_HOME, 0};
            holdMs = 0;
        }
        renderDisplay(active);
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
                queueMQTTEvent("button_open", "inside_button");

                // Button operations are logged on the server without Telegram spam.
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
    attachDisplayQueue(displayQueue);
    serverUnlockQueue = xQueueCreate(SERVER_UNLOCK_QUEUE_LENGTH, sizeof(ServerUnlockRequest));
    fingerprintCommandQueue = xQueueCreate(2, sizeof(FingerprintCommandRequest));

    if (!lockEventGroup || !displayQueue || !serverUnlockQueue ||
        !fingerprintCommandQueue) {
        Serial.println(F("[SYSTEM] LOI: Khong tao duoc FreeRTOS primitive."));
        for (;;) delay(1000);
    }

    if (!fpOk) {
        DisplayMsg errMsg = { DISP_SENSOR_ERROR, 0 };
        xQueueSend(displayQueue, &errMsg, 0);
    }

    // 3. Khởi tạo Telegram Driver (tạo queue + mutex nội bộ)
    initTelegram(displayQueue, lockEventGroup);
    initMQTT(
        serverUnlockQueue,
        fingerprintCommandQueue,
        lockEventGroup,
        EVT_UNLOCK_SERVER
    );
    queueMQTTEvent(
        fpOk ? "sensor_online" : "sensor_offline",
        "fingerprint_sensor"
    );

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
