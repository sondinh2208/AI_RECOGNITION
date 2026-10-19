#include "MQTT_Driver.h"

#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ctype.h>
#include <esp_system.h>
#include <time.h>

#include "MQTT_Certificates.h"
#include "config.h"

namespace {
constexpr size_t RECENT_COMMAND_COUNT = 8;
constexpr size_t ACK_QUEUE_LENGTH = 12;
constexpr size_t EVENT_QUEUE_LENGTH = 16;

struct MQTTAckMessage {
    char commandId[49];
    char command[24];
    char state[12];
    char reason[40];
};

struct MQTTEventMessage {
    char eventId[49];
    char eventType[32];
    char source[20];
    int16_t fingerprintId;
    unsigned long uptimeMs;
};

WiFiClientSecure tlsClient;
PubSubClient mqttClient(tlsClient);

char statusTopic[96];
char commandTopic[96];
char ackTopic[96];
char eventTopic[96];
char clientId[64];
char recentCommandIds[RECENT_COMMAND_COUNT][49] = {};
size_t recentCommandIndex = 0;

QueueHandle_t unlockQueue = nullptr;
QueueHandle_t fingerprintQueue = nullptr;
QueueHandle_t ackQueue = nullptr;
QueueHandle_t eventQueue = nullptr;
EventGroupHandle_t unlockEventGroup = nullptr;
EventBits_t unlockEventBit = 0;

unsigned long lastWiFiAttempt = 0;
unsigned long lastMQTTAttempt = 0;
unsigned long lastHeartbeat = 0;
bool timeReady = false;

bool isSafeCommandId(const char *value) {
    const size_t length = strlen(value);
    if (length == 0 || length > 48) return false;

    for (size_t i = 0; i < length; ++i) {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!isalnum(c) && c != '-' && c != '_' && c != '.') return false;
    }
    return true;
}

bool wasCommandSeen(const char *commandId) {
    for (size_t i = 0; i < RECENT_COMMAND_COUNT; ++i) {
        if (recentCommandIds[i][0] != '\0' && strcmp(recentCommandIds[i], commandId) == 0) {
            return true;
        }
    }
    return false;
}

void rememberCommand(const char *commandId) {
    strlcpy(recentCommandIds[recentCommandIndex], commandId, sizeof(recentCommandIds[0]));
    recentCommandIndex = (recentCommandIndex + 1) % RECENT_COMMAND_COUNT;
}

bool publishAck(
    const char *commandId,
    const char *command,
    const char *state,
    const char *reason = nullptr
) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<320> document;
    document["device_id"] = DEVICE_ID;
    document["command_id"] = commandId;
    document["command"] = command;
    document["state"] = state;
    document["uptime_ms"] = static_cast<unsigned long>(millis());
    if (reason && reason[0] != '\0') document["reason"] = reason;

    char payload[320];
    const size_t payloadLength = serializeJson(document, payload, sizeof(payload));
    const bool sent = mqttClient.publish(
        ackTopic,
        reinterpret_cast<const uint8_t *>(payload),
        payloadLength,
        false
    );

    if (sent) {
        Serial.printf("[MQTT] ACK %s: %s\n", commandId, state);
    } else {
        Serial.printf("[MQTT] Gui ACK %s that bai.\n", commandId);
    }
    return sent;
}

void flushQueuedAcks() {
    if (!ackQueue || !mqttClient.connected()) return;

    MQTTAckMessage message;
    while (xQueuePeek(ackQueue, &message, 0) == pdTRUE) {
        const char *reason = message.reason[0] == '\0' ? nullptr : message.reason;
        if (!publishAck(message.commandId, message.command, message.state, reason)) return;
        xQueueReceive(ackQueue, &message, 0);
    }
}

bool publishEvent(const MQTTEventMessage &message) {
    if (!mqttClient.connected()) return false;

    StaticJsonDocument<384> document;
    document["schema_version"] = 1;
    document["device_id"] = DEVICE_ID;
    document["event_id"] = message.eventId;
    document["event"] = message.eventType;
    document["source"] = message.source;
    document["uptime_ms"] = message.uptimeMs;
    const time_t now = time(nullptr);
    if (now > 0) document["occurred_at"] = static_cast<int64_t>(now);
    if (message.fingerprintId >= 0) {
        document["fingerprint_id"] = message.fingerprintId;
    }

    char payload[384];
    const size_t payloadLength = serializeJson(document, payload, sizeof(payload));
    const bool sent = mqttClient.publish(
        eventTopic,
        reinterpret_cast<const uint8_t *>(payload),
        payloadLength,
        false
    );
    if (sent) {
        Serial.printf("[MQTT] Event %s: %s\n", message.eventType, message.eventId);
    }
    return sent;
}

void flushQueuedEvents() {
    if (!eventQueue || !mqttClient.connected()) return;

    MQTTEventMessage message;
    while (xQueuePeek(eventQueue, &message, 0) == pdTRUE) {
        if (!publishEvent(message)) return;
        xQueueReceive(eventQueue, &message, 0);
    }
}

void rejectOpen(const char *commandId, const char *reason) {
    Serial.printf("[MQTT] Tu choi OPEN %s: %s\n", commandId, reason);
    publishAck(commandId, "OPEN", "failed", reason);
}

void rejectFingerprintCommand(
    const char *commandId,
    const char *command,
    const char *reason
) {
    Serial.printf("[MQTT] Tu choi %s %s: %s\n", command, commandId, reason);
    publishAck(commandId, command, "failed", reason);
}

bool commandExpiryIsValid(JsonDocument &document, int64_t &expiresAt) {
    expiresAt = document["expires_at"] | static_cast<int64_t>(0);
    const int64_t now = static_cast<int64_t>(time(nullptr));
    return now > 0 && expiresAt >= now &&
           expiresAt <= now + MQTT_COMMAND_MAX_FUTURE_SECONDS;
}

void handleFingerprintCommand(
    JsonDocument &document,
    const char *commandId,
    const char *command
) {
    if (wasCommandSeen(commandId)) {
        rejectFingerprintCommand(commandId, command, "duplicate_command");
        return;
    }
    const int fingerprintId = document["fingerprint_id"] | 0;
    if (fingerprintId < 1 || fingerprintId > 127) {
        rejectFingerprintCommand(commandId, command, "invalid_fingerprint_id");
        return;
    }
    int64_t expiresAt = 0;
    if (!commandExpiryIsValid(document, expiresAt)) {
        rejectFingerprintCommand(commandId, command, "invalid_or_expired_command");
        return;
    }
    if (!fingerprintQueue) {
        rejectFingerprintCommand(commandId, command, "fingerprint_controller_unavailable");
        return;
    }

    FingerprintCommandRequest request = {};
    strlcpy(request.commandId, commandId, sizeof(request.commandId));
    strlcpy(request.command, command, sizeof(request.command));
    request.fingerprintId = static_cast<uint8_t>(fingerprintId);
    request.expiresAt = expiresAt;
    if (xQueueSend(fingerprintQueue, &request, 0) != pdTRUE) {
        rejectFingerprintCommand(commandId, command, "fingerprint_queue_full");
        return;
    }
    rememberCommand(commandId);
    Serial.printf("[MQTT] Da dua %s ID #%d vao hang doi.\n", command, fingerprintId);
}

void handleOpenCommand(JsonDocument &document, const char *commandId) {
    if (wasCommandSeen(commandId)) {
        rejectOpen(commandId, "duplicate_command");
        return;
    }

    const long durationMs = document["duration_ms"] | 0L;
    if (durationMs < static_cast<long>(SERVER_UNLOCK_MIN_DURATION_MS) ||
        durationMs > static_cast<long>(SERVER_UNLOCK_MAX_DURATION_MS)) {
        rejectOpen(commandId, "invalid_duration_ms");
        return;
    }

    const int64_t expiresAt = document["expires_at"] | static_cast<int64_t>(0);
    const int64_t now = static_cast<int64_t>(time(nullptr));
    if (now <= 0) {
        rejectOpen(commandId, "clock_not_ready");
        return;
    }
    if (expiresAt < now) {
        rejectOpen(commandId, "expired_command");
        return;
    }
    if (expiresAt > now + MQTT_COMMAND_MAX_FUTURE_SECONDS) {
        rejectOpen(commandId, "expiry_too_far");
        return;
    }
    if (!unlockQueue || !unlockEventGroup || unlockEventBit == 0) {
        rejectOpen(commandId, "lock_controller_unavailable");
        return;
    }

    ServerUnlockRequest request = {};
    strlcpy(request.commandId, commandId, sizeof(request.commandId));
    request.durationMs = static_cast<uint32_t>(durationMs);
    request.expiresAt = expiresAt;

    if (xQueueSend(unlockQueue, &request, 0) != pdTRUE) {
        rejectOpen(commandId, "unlock_queue_full");
        return;
    }

    rememberCommand(commandId);
    xEventGroupSetBits(unlockEventGroup, unlockEventBit);
    Serial.printf("[MQTT] Da dua OPEN %s vao hang doi khoa.\n", commandId);
}

void onMQTTMessage(char *topic, byte *payload, unsigned int length) {
    if (strcmp(topic, commandTopic) != 0) return;

    if (length == 0 || length >= 384) {
        Serial.println(F("[MQTT] Bo qua command rong hoac qua dai."));
        return;
    }

    StaticJsonDocument<384> document;
    const DeserializationError error = deserializeJson(document, payload, length);
    if (error) {
        Serial.printf("[MQTT] JSON command khong hop le: %s\n", error.c_str());
        return;
    }

    const char *command = document["command"] | "";
    const char *commandId = document["command_id"] | "";
    const char *targetDevice = document["device_id"] | DEVICE_ID;

    if (!isSafeCommandId(commandId)) {
        Serial.println(F("[MQTT] Tu choi command_id thieu, qua dai hoac co ky tu la."));
        return;
    }
    if (strcmp(targetDevice, DEVICE_ID) != 0) {
        Serial.println(F("[MQTT] Bo qua command cua thiet bi khac."));
        return;
    }

    Serial.printf("[MQTT] Nhan command %s (%s)\n", command, commandId);
    if (strcmp(command, "PING") == 0) {
        publishAck(commandId, command, "received");
        return;
    }
    if (strcmp(command, "OPEN") == 0) {
        handleOpenCommand(document, commandId);
        return;
    }
    if (strcmp(command, "FINGERPRINT_ENROLL") == 0 ||
        strcmp(command, "FINGERPRINT_DELETE") == 0) {
        handleFingerprintCommand(document, commandId, command);
        return;
    }

    publishAck(commandId, command, "failed", "unsupported_command");
}

void publishStatus(const char *state) {
    if (!mqttClient.connected()) return;

    char payload[224];
    snprintf(
        payload,
        sizeof(payload),
        "{\"device_id\":\"%s\",\"state\":\"%s\",\"firmware\":\"%s\","
        "\"uptime_ms\":%lu,\"rssi_dbm\":%ld}",
        DEVICE_ID,
        state,
        FIRMWARE_VERSION,
        static_cast<unsigned long>(millis()),
        static_cast<long>(WiFi.RSSI())
    );

    if (mqttClient.publish(statusTopic, payload, true)) {
        Serial.printf("[MQTT] Status da gui: %s\n", state);
    } else {
        Serial.println(F("[MQTT] Gui status that bai."));
    }
}

void ensureWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;
    if (millis() - lastWiFiAttempt < MQTT_RECONNECT_INTERVAL_MS) return;

    lastWiFiAttempt = millis();
    Serial.println(F("[WiFi] Dang ket noi de dung MQTT..."));
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool ensureTime() {
    if (timeReady) return true;
    if (WiFi.status() != WL_CONNECTED) return false;

    static bool ntpStarted = false;
    if (!ntpStarted) {
        configTime(0, 0, "pool.ntp.org", "time.google.com");
        ntpStarted = true;
        Serial.println(F("[NTP] Dang dong bo thoi gian cho TLS..."));
    }

    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 50)) return false;

    timeReady = true;
    Serial.println(F("[NTP] Da dong bo, TLS co the xac thuc chung chi."));
    return true;
}

void ensureMQTT() {
    if (mqttClient.connected()) return;
    if (!ensureTime()) return;
    if (millis() - lastMQTTAttempt < MQTT_RECONNECT_INTERVAL_MS) return;

    lastMQTTAttempt = millis();
    Serial.printf("[MQTT] Dang ket noi %s:%u...\n", MQTT_HOST, MQTT_PORT);

    const char *offlinePayload =
        "{\"device_id\":\"" DEVICE_ID "\",\"state\":\"offline\"}";

    const bool connected = mqttClient.connect(
        clientId,
        MQTT_USERNAME,
        MQTT_PASSWORD,
        statusTopic,
        1,
        true,
        offlinePayload
    );

    if (connected) {
        Serial.println(F("[MQTT] Ket noi HiveMQ Cloud thanh cong."));
        if (mqttClient.subscribe(commandTopic, 1)) {
            Serial.printf("[MQTT] Da subscribe: %s\n", commandTopic);
        } else {
            Serial.println(F("[MQTT] Subscribe command that bai."));
        }
        publishStatus("online");
        lastHeartbeat = millis();
    } else {
        Serial.printf("[MQTT] Ket noi that bai, state=%d. Thu lai sau.\n", mqttClient.state());
    }
}
}  // namespace

void initMQTT(
    QueueHandle_t serverUnlockQueue,
    QueueHandle_t fingerprintCommandQueue,
    EventGroupHandle_t lockEventGroup,
    EventBits_t serverUnlockBit
) {
    unlockQueue = serverUnlockQueue;
    fingerprintQueue = fingerprintCommandQueue;
    unlockEventGroup = lockEventGroup;
    unlockEventBit = serverUnlockBit;
    ackQueue = xQueueCreate(ACK_QUEUE_LENGTH, sizeof(MQTTAckMessage));
    eventQueue = xQueueCreate(EVENT_QUEUE_LENGTH, sizeof(MQTTEventMessage));

    snprintf(statusTopic, sizeof(statusTopic), "smartdoor/v1/%s/status", DEVICE_ID);
    snprintf(commandTopic, sizeof(commandTopic), "smartdoor/v1/%s/command", DEVICE_ID);
    snprintf(ackTopic, sizeof(ackTopic), "smartdoor/v1/%s/ack", DEVICE_ID);
    snprintf(eventTopic, sizeof(eventTopic), "smartdoor/v1/%s/event", DEVICE_ID);
    snprintf(
        clientId,
        sizeof(clientId),
        "%s-%08lx",
        DEVICE_ID,
        static_cast<unsigned long>(esp_random())
    );

    tlsClient.setCACert(ISRG_ROOT_X1);
    mqttClient.setServer(MQTT_HOST, MQTT_PORT);
    mqttClient.setCallback(onMQTTMessage);
    mqttClient.setKeepAlive(30);
    mqttClient.setSocketTimeout(5);
    mqttClient.setBufferSize(512);

    if (!ackQueue) Serial.println(F("[MQTT] LOI: Khong tao duoc ACK queue."));
    if (!eventQueue) Serial.println(F("[MQTT] LOI: Khong tao duoc event queue."));
    Serial.printf("[MQTT] Client da khoi tao: %s\n", clientId);
}

bool queueMQTTAck(
    const char *commandId,
    const char *command,
    const char *state,
    const char *reason
) {
    if (!ackQueue) return false;

    MQTTAckMessage message = {};
    strlcpy(message.commandId, commandId, sizeof(message.commandId));
    strlcpy(message.command, command, sizeof(message.command));
    strlcpy(message.state, state, sizeof(message.state));
    if (reason) strlcpy(message.reason, reason, sizeof(message.reason));

    if (xQueueSend(ackQueue, &message, 0) == pdTRUE) return true;
    Serial.printf("[MQTT] ACK queue day, mat ACK %s:%s.\n", commandId, state);
    return false;
}

bool isMQTTConnected() {
    return mqttClient.connected();
}

bool queueMQTTEvent(
    const char *eventType,
    const char *source,
    int16_t fingerprintId
) {
    if (!eventQueue || !eventType || !source) return false;

    MQTTEventMessage message = {};
    snprintf(
        message.eventId,
        sizeof(message.eventId),
        "evt-%08lx-%08lx",
        static_cast<unsigned long>(millis()),
        static_cast<unsigned long>(esp_random())
    );
    strlcpy(message.eventType, eventType, sizeof(message.eventType));
    strlcpy(message.source, source, sizeof(message.source));
    message.fingerprintId = fingerprintId;
    message.uptimeMs = static_cast<unsigned long>(millis());

    if (xQueueSend(eventQueue, &message, 0) == pdTRUE) return true;
    Serial.printf("[MQTT] Event queue day, mat su kien %s.\n", eventType);
    return false;
}

void Task_MQTT(void *pvParameters) {
    (void)pvParameters;

    for (;;) {
        ensureWiFi();

        if (WiFi.status() == WL_CONNECTED) {
            ensureMQTT();
            if (mqttClient.connected()) {
                mqttClient.loop();
                flushQueuedAcks();
                flushQueuedEvents();
                if (millis() - lastHeartbeat >= MQTT_HEARTBEAT_INTERVAL_MS) {
                    publishStatus("online");
                    lastHeartbeat = millis();
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}
