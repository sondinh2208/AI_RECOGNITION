#include "Fingerprint_Driver.h"
#include <HardwareSerial.h>
#include <Adafruit_Fingerprint.h>

// Sử dụng UART2 phần cứng của ESP32
static HardwareSerial serialAS608(2);
static Adafruit_Fingerprint finger = Adafruit_Fingerprint(&serialAS608);
static bool sensorReady = false;
static volatile bool isEnrolling = false;

bool initFingerprint() {
    // 1. Khởi tạo UART2 với chân TX=17, RX=18 chuẩn ESP32-S3 ở tốc độ 57600 baud
    serialAS608.begin(BAUDRATE_AS608, SERIAL_8N1, AS608_RX, AS608_TX);
    
    Serial.println(F("\n=========================================="));
    Serial.println(F("[AS608] DANG KHOI DONG VA KET NOI CAM BIEN..."));
    Serial.println(F("=========================================="));

    // 2. Chờ cảm biến khởi động nguồn (thử 10 lần, mỗi lần cách nhau 250ms = tối đa 2.5 giây)
    for (int retry = 1; retry <= 10; retry++) {
        delay(250);
        if (finger.verifyPassword()) {
            Serial.print(F("🎉 [AS608] KET NOI THANH CONG o lan thu "));
            Serial.print(retry);
            Serial.println(F("/10!"));
            Serial.println(F("------------------------------------------"));
            sensorReady = true;

            finger.getTemplateCount();
            Serial.print(F("[AS608] So luong van tay trong Flash: "));
            Serial.println(finger.templateCount);
            return true;
        }
        Serial.print(F("[AS608] Dang cho cam bien len nguon (Lan "));
        Serial.print(retry);
        Serial.println(F("/10)..."));
    }

    Serial.println(F("❌ [AS608] Khong the ket noi sau 10 lan thu!"));
    sensorReady = false;
    return false;
}




bool isFingerprintConnected() {
    return sensorReady;
}

uint16_t getFingerprintCount() {
    if (!sensorReady) return 0;
    finger.getTemplateCount();
    return finger.templateCount;
}

int checkFingerprint() {
    // Nếu đang trong chế độ đăng ký hoặc cảm biến chưa sẵn sàng thì bỏ qua
    if (!sensorReady || isEnrolling) {
        return FP_NO_FINGER;
    }
 
    // 1. Lấy ảnh vân tay từ cảm biến
    uint8_t p = finger.getImage();
    if (p != FINGERPRINT_OK) {
        return FP_NO_FINGER; // Chưa có ngón tay đặt lên
    }

    // 2. Chuyển đổi đặc trưng mẫu (Slot 1)
    p = finger.image2Tz(1);
    if (p != FINGERPRINT_OK) {
        // Ngón tay đang di chuyển hoặc chạm chưa khít -> bỏ qua để lượt quét tiếp theo chụp lại
        return FP_NO_FINGER;
    }

    // 3. Tìm kiếm vân tay siêu tốc (Fast Search)
    p = finger.fingerFastSearch();
    if (p == FINGERPRINT_OK) {
        Serial.print(F("[AS608] >> KHOP VAN TAY ID #"));
        Serial.print(finger.fingerID);
        Serial.print(F(" (Do tin cay: "));
        Serial.print(finger.confidence);
        Serial.println(F(")"));
        return finger.fingerID; // HỢP LỆ (> 0)
    } 
    else if (p == FINGERPRINT_NOTFOUND) {
        Serial.println(F("[AS608] >> TU CHOI: Van tay khong ton tai trong he thong!"));
        return FP_NOT_FOUND; // KHÔNG HỢP LỆ (-2)
    }

    return FP_NO_FINGER;
}




static void notifyProgress(EnrollCallback callback, const char* msg) {
    Serial.println(msg);
    if (callback) {
        callback(msg);
    }
}

bool enrollFingerprint(uint8_t id, EnrollCallback callback) {
    if (!sensorReady || id == 0 || id > 127) {
        notifyProgress(callback, "❌ ID không hợp lệ (chỉ chấp nhận từ 1 đến 127)!");
        return false;
    }

    isEnrolling = true; // Tạm khóa Task quét nền
    int p = -1;
    
    char buf[128];
    snprintf(buf, sizeof(buf), "🔄 BẮT ĐẦU ĐĂNG KÝ VÂN TAY CHO ID #%d", id);
    notifyProgress(callback, buf);

    // Bước 1: Quét lần 1
    notifyProgress(callback, "👉 [BƯỚC 1/2] Đặt ngón tay cần đăng ký lên cảm biến (chờ trong 15s)...");
    unsigned long startWait1 = millis();
    while (p != FINGERPRINT_OK) {
        if (millis() - startWait1 > 15000) {
            notifyProgress(callback, "⏱️ Quá thời gian (15s)! Đã hủy đăng ký.");
            isEnrolling = false;
            return false;
        }
        p = finger.getImage();
        if (p == FINGERPRINT_NOFINGER) {
            delay(50);
            continue;
        }
        if (p != FINGERPRINT_OK) {
            notifyProgress(callback, "❌ Lỗi chụp ảnh lần 1. Vui lòng thử lại!");
            isEnrolling = false;
            return false;
        }
    }

    p = finger.image2Tz(1);
    if (p != FINGERPRINT_OK) {
        notifyProgress(callback, "❌ Lỗi tạo đặc trưng mẫu lần 1!");
        isEnrolling = false;
        return false;
    }
    notifyProgress(callback, "✅ Đã chụp xong lần 1! Vui lòng NHẤC NGÓN TAY RA...");

    // Bước 2: Chờ nhấc tay (timeout 5s)
    delay(1000);
    p = 0;
    unsigned long startLift = millis();
    while (p != FINGERPRINT_NOFINGER && (millis() - startLift < 5000)) {
        p = finger.getImage();
        delay(50);
    }

    // Bước 3: Quét lần 2 (cùng ngón tay đó)
    notifyProgress(callback, "👉 [BƯỚC 2/2] Đặt lại CÙNG NGÓN TAY ĐÓ lên cảm biến (chờ trong 15s)...");
    p = -1;
    unsigned long startWait2 = millis();
    while (p != FINGERPRINT_OK) {
        if (millis() - startWait2 > 15000) {
            notifyProgress(callback, "⏱️ Quá thời gian (15s)! Đã hủy đăng ký.");
            isEnrolling = false;
            return false;
        }
        p = finger.getImage();
        if (p == FINGERPRINT_NOFINGER) {
            delay(50);
            continue;
        }
        if (p != FINGERPRINT_OK) {
            notifyProgress(callback, "❌ Lỗi chụp ảnh lần 2. Vui lòng thử lại!");
            isEnrolling = false;
            return false;
        }
    }

    p = finger.image2Tz(2);
    if (p != FINGERPRINT_OK) {
        notifyProgress(callback, "❌ Lỗi tạo đặc trưng mẫu lần 2!");
        isEnrolling = false;
        return false;
    }

    // Bước 4: Tạo model mẫu
    p = finger.createModel();
    if (p != FINGERPRINT_OK) {
        notifyProgress(callback, "❌ THẤT BẠI: Hai lần quét không khớp mẫu vân tay!");
        isEnrolling = false;
        return false;
    }

    // Bước 5: Lưu vào Flash của cảm biến AS608
    p = finger.storeModel(id);
    if (p == FINGERPRINT_OK) {
        snprintf(buf, sizeof(buf), "🎉 CHÚC MỪNG: Đăng ký THÀNH CÔNG vân tay ID #%d!", id);
        notifyProgress(callback, buf);
        isEnrolling = false;
        return true;
    } else {
        notifyProgress(callback, "❌ THẤT BẠI: Không thể lưu mẫu vào bộ nhớ Flash!");
        isEnrolling = false;
        return false;
    }
}


bool deleteFingerprint(uint8_t id) {
    if (!sensorReady || id == 0 || id > 127) return false;
    isEnrolling = true;
    
    uint8_t p = finger.deleteModel(id);
    if (p == FINGERPRINT_OK) {
        Serial.print(F("[AS608] Da xoa thanh cong van tay ID #"));
        Serial.println(id);
        isEnrolling = false;
        return true;
    } else {
        Serial.print(F("[AS608] Khong the xoa van tay ID #"));
        Serial.println(id);
        isEnrolling = false;
        return false;
    }
}

bool emptyAllFingerprints() {
    if (!sensorReady) return false;
    isEnrolling = true;
    
    uint8_t p = finger.emptyDatabase();
    if (p == FINGERPRINT_OK) {
        Serial.println(F("[AS608] Da XOA TOAN BO co so du lieu van tay!"));
        isEnrolling = false;
        return true;
    } else {
        Serial.println(F("[AS608] Xoa database that bai!"));
        isEnrolling = false;
        return false;
    }
}