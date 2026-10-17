#pragma once

// Sao chep tep nay thanh secrets.h, sau do dien thong tin cua ban.
// secrets.h da duoc .gitignore bo qua va khong duoc dua len Git.

// Wi-Fi 2.4 GHz cho ESP32-S3.
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// Telegram se duoc chuyen len server o giai doan sau.
// Trong thoi gian chuyen doi, firmware cu van can cac gia tri nay.
#define BOT_TOKEN       "YOUR_NEW_TELEGRAM_BOT_TOKEN"
#define ADMIN_CHAT_ID   "YOUR_TELEGRAM_CHAT_ID"
#define BOT_USERNAME    "YOUR_TELEGRAM_BOT_USERNAME"

// MQTT Cloud TLS. Khong them mat khau that vao tep example nay.
#define MQTT_HOST       "YOUR_CLUSTER.s1.eu.hivemq.cloud"
#define MQTT_PORT       8883
#define MQTT_USERNAME   "YOUR_MQTT_DEVICE_USERNAME"
#define MQTT_PASSWORD   "YOUR_MQTT_DEVICE_PASSWORD"
