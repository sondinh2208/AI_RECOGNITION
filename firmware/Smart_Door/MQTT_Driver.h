#pragma once

#include <Arduino.h>
#include <freertos/event_groups.h>
#include <freertos/queue.h>

struct ServerUnlockRequest {
    char commandId[49];
    uint32_t durationMs;
    int64_t expiresAt;
};

// Khoi tao topic va TLS client. Goi mot lan trong setup().
void initMQTT(
    QueueHandle_t serverUnlockQueue,
    EventGroupHandle_t lockEventGroup,
    EventBits_t serverUnlockBit
);

// Dua ACK vao queue de chi Task_MQTT truy cap PubSubClient.
bool queueMQTTAck(
    const char *commandId,
    const char *command,
    const char *state,
    const char *reason = nullptr
);

// FreeRTOS task quan ly Wi-Fi, MQTT reconnect va heartbeat.
void Task_MQTT(void *pvParameters);

// Trang thai ket noi de cac task khac co the kiem tra ma khong bi block.
bool isMQTTConnected();
