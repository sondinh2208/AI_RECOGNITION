#include "Lock_Driver.h"

static bool lockState = false;

void initLock() {
    pinMode(LOCK_PIN, OUTPUT);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(LED_GREEN_PIN, OUTPUT);
    pinMode(LED_RED_PIN, OUTPUT);

    // Trạng thái an toàn ban đầu:
    // Relay kích mức thấp (Active LOW) -> xuất HIGH để ngắt relay (khóa đóng)
    digitalWrite(LOCK_PIN, HIGH);
    digitalWrite(LED_GREEN_PIN, LOW);
    digitalWrite(LED_RED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    
    lockState = false;
}

void openLock() {
    // Relay kích mức thấp (Active LOW) -> xuất LOW để kích mở relay
    digitalWrite(LOCK_PIN, LOW);
    digitalWrite(LED_GREEN_PIN, HIGH);
    lockState = true;
    Serial.println(F("[LOCK] Trang thai: MO KHOA (Relay Active LOW -> Kich ON, LED Green ON)"));
}

void closeLock() {
    // Relay kích mức thấp (Active LOW) -> xuất HIGH để ngắt relay (dong khoa)
    digitalWrite(LOCK_PIN, HIGH);
    digitalWrite(LED_GREEN_PIN, LOW);
    lockState = false;
    Serial.println(F("[LOCK] Trang thai: DONG KHOA (Relay Inactive -> Ngat OFF, LED Green OFF)"));
}

void alertSuccess() {
    // 1 tiếng bíp ngắn (150ms)
    digitalWrite(BUZZER_PIN, HIGH);
    delay(150);
    digitalWrite(BUZZER_PIN, LOW);
}

void alertError() {
    // Bật LED Đỏ và phát 2 tiếng bíp dứt khoát
    digitalWrite(LED_RED_PIN, HIGH);
    for (int i = 0; i < 2; i++) {
        digitalWrite(BUZZER_PIN, HIGH);
        delay(100);
        digitalWrite(BUZZER_PIN, LOW);
        delay(100);
    }
    // Chú ý: LED Đỏ vẫn được giữ sáng để người dùng nhận biết, sẽ tắt khi clearErrorAlert() được gọi
}

void clearErrorAlert() {
    digitalWrite(LED_RED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
}

bool isLockOpen() {
    return lockState;
}