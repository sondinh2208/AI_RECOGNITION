#include "Display_Driver.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MQTT_Driver.h"
#include "Lock_Driver.h"

static QueueHandle_t oledQueue = nullptr;
void attachDisplayQueue(QueueHandle_t queue) { oledQueue = queue; }
void postDisplay(DisplayMsgType type, int id) {
    DisplayMsg msg = {type, id};
    if (oledQueue) xQueueSend(oledQueue, &msg, 0);
}
bool readDisplay(DisplayMsg &message, TickType_t timeout) {
    return oledQueue && xQueueReceive(oledQueue, &message, timeout) == pdTRUE;
}

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1  // Không dùng chân Reset cứng

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
static bool displayReady = false;

static void centered(const char *text, int y, int size = 1) {
    display.setTextSize(size);
    const int width = strlen(text) * 6 * size;
    display.setCursor(max(4, (128 - width) / 2), y);
    display.print(text);
}

void renderDisplay(const DisplayMsg &msg) {
    if (!displayReady) return;
    const char *title = "FACECHECK";
    const char *main = "SAN SANG";
    const char *hint = "Mat hoac van tay";
    switch (msg.type) {
        case DISP_HOME: break;
        case DISP_SUCCESS: title = "MO KHOA"; main = "THANH CONG";
            hint = msg.id > 0 ? "Xac thuc van tay" : msg.id == 0 ? "Nut ben trong" : "Lenh tu server"; break;
        case DISP_FACE_SUCCESS: title = "KHUON MAT"; main = "MO KHOA"; hint = "Xin roi khoi khung"; break;
        case DISP_FACE_VERIFIED: title = "KHUON MAT"; main = "DA XAC THUC"; hint = "Xin roi khoi khung"; break;
        case DISP_FACE_ENROLL: title = "DANG KY KHUON MAT"; main = "NHIN CAMERA"; hint = "Giu yen mat 3 giay"; break;
        case DISP_FACE_READY: title = "DANG KY KHUON MAT"; main = "DA QUET"; hint = "Cho luu tren server"; break;
        case DISP_FACE_SAVING: title = "DANG KY KHUON MAT"; main = "DANG LUU"; hint = "Vui long cho"; break;
        case DISP_FACE_SAVED: title = "DANG KY KHUON MAT"; main = "DA LUU"; hint = "Xin roi khoi khung"; break;
        case DISP_FACE_ENROLL_FAILED: title = "DANG KY KHUON MAT"; main = "LOI LUU"; hint = "Kiem tra tren server"; break;
        case DISP_FACE_CANCELLED: title = "DANG KY KHUON MAT"; main = "DA HUY QUET"; hint = "Cho luot dang ky moi"; break;
        case DISP_FACE_DELETED: title = "QUAN LY KHUON MAT"; main = "DA XOA"; hint = "Da xoa khuon mat"; break;
        case DISP_ERROR: title = "VAN TAY"; main = "TU CHOI"; hint = "Vui long thu lai"; break;
        case DISP_SENSOR_ERROR: title = "VAN TAY"; main = "LOI CAM BIEN"; hint = "Kiem tra thiet bi"; break;
        case DISP_FP_ENROLL: title = "DANG KY VAN TAY"; main = "DAT NGON TAY"; hint = "Buoc 1/2 - cho 15s"; break;
        case DISP_FP_LIFT: title = "DANG KY VAN TAY"; main = "NHAC TAY RA"; hint = "Da nhan mau lan 1"; break;
        case DISP_FP_SECOND: title = "DANG KY VAN TAY"; main = "DAT LAI TAY"; hint = "Buoc 2/2 - cung ngon"; break;
        case DISP_FP_SAVED: title = "DANG KY VAN TAY"; main = "DA LUU"; hint = "Dang ky thanh cong"; break;
        case DISP_FP_DELETING: title = "QUAN LY VAN TAY"; main = "DANG XOA"; hint = "Vui long cho"; break;
        case DISP_FP_DELETED: title = "QUAN LY VAN TAY"; main = "DA XOA"; hint = "Hoan tat"; break;
        case DISP_FP_SYNC: title = "DONG BO VAN TAY"; main = "DANG KIEM TRA"; hint = "Vui long cho"; break;
        case DISP_FP_SYNCED: title = "DONG BO VAN TAY"; main = "HOAN TAT"; hint = "Da gui danh sach"; break;
        case DISP_FP_FAILED: title = "QUAN LY VAN TAY"; main = "THAT BAI";
            hint = msg.id == -2 ? "Ngon tay da ton tai" : msg.id == -3 ? "Da het thoi gian" : "Kiem tra va thu lai"; break;
        case DISP_FACE_CHECK: title = "KHUON MAT"; main = "DANG XAC THUC"; hint = "Nhin thang camera"; break;
        case DISP_FACE_DENIED: title = "KHONG XAC THUC DUOC"; main = "TU CHOI"; hint = "Xin roi khoi khung"; break;
    }
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    centered(title, 2);
    display.drawFastHLine(4, 13, 120, SSD1306_WHITE);
    centered(main, 23, strlen(main) <= 10 ? 2 : 1);
    centered(hint, 44);
    display.drawFastHLine(4, 54, 120, SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(4, 57);
    display.print(isMQTTConnected() ? "ONLINE" : "OFFLINE");
    char footer[16];
    if (msg.id > 0) snprintf(footer, sizeof(footer), "ID #%d", msg.id);
    else snprintf(footer, sizeof(footer), "%s", isLockOpen() ? "MO KHOA" : "SAN SANG");
    display.setCursor(128 - strlen(footer) * 6 - 4, 57);
    display.print(footer);
    display.display();
}

bool initDisplay() {
    // Khởi tạo I2C với chân SDA và SCL cấu hình trong config.h
    Wire.begin(OLED_SDA, OLED_SCL);

    if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
        Serial.println(F("[OLED] ERROR: Khong tim thay man hinh OLED SSD1306!"));
        displayReady = false;
        return false;
    }

    displayReady = true;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    
    // Màn hình chào khởi động
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(20, 15);
    display.println(F("KHOA CUA DIEN"));
    display.setCursor(25, 28);
    display.println(F("THONG MINH"));
    display.drawFastHLine(10, 40, 108, SSD1306_WHITE);
    display.setCursor(18, 48);
    display.println(F("Dang khoi dong..."));
    display.display();
    
    delay(1000);
    showHomeScreen();
    return true;
}

void showHomeScreen() {
    if (!displayReady) return;

    display.clearDisplay();
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    
    // Header
    display.setTextSize(1);
    display.setCursor(28, 8);
    display.println(F("SMART LOCK"));
    display.drawFastHLine(8, 20, 112, SSD1306_WHITE);
    
    // Body
    display.setCursor(18, 30);
    display.println(F("SAN SANG QUET"));
    display.setCursor(28, 44);
    display.println(F("VAN TAY..."));
    display.display();
}

void showSuccessScreen(int id) {
    if (!displayReady) return;

    display.clearDisplay();
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    
    // Header
    display.setTextSize(2);
    display.setCursor(20, 10);
    display.println(F("MO CUA!"));
    display.drawFastHLine(8, 30, 112, SSD1306_WHITE);
    
    // Body
    display.setTextSize(1);
    if (id > 0) {
        display.setCursor(22, 42);
        display.print(F("ID Van Tay: #"));
        display.print(id);
    } else if (id == 0) {
        display.setCursor(10, 42);
        display.print(F("Nut Nhan Ben Trong"));
    } else {
        display.setCursor(25, 42);
        display.print(F("Lenh Tu Server"));
    }
    display.display();
}

void showErrorScreen() {
    if (!displayReady) return;

    display.clearDisplay();
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    
    // Header
    display.setTextSize(2);
    display.setCursor(15, 10);
    display.println(F("TU CHOI!"));
    display.drawFastHLine(8, 30, 112, SSD1306_WHITE);
    
    // Body
    display.setTextSize(1);
    display.setCursor(22, 36);
    display.println(F("Khong khop ID!"));
    display.setCursor(16, 48);
    display.println(F("Vui long thu lai"));
    display.display();
}

void showSensorErrorScreen() {
    if (!displayReady) return;

    display.clearDisplay();
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    
    display.setTextSize(1);
    display.setCursor(25, 12);
    display.println(F("CANH BAO LOI"));
    display.drawFastHLine(8, 24, 112, SSD1306_WHITE);
    display.setCursor(14, 34);
    display.println(F("Loi cam bien AS608"));
    display.setCursor(16, 48);
    display.println(F("Kiem tra day UART"));
    display.display();
}

void showFingerprintManagementScreen(DisplayMsgType type, int id) {
    if (!displayReady) return;

    display.clearDisplay();
    display.drawRoundRect(0, 0, 128, 64, 4, SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(18, 8);
    if (type == DISP_FP_ENROLL) display.println(F("DANG KY VAN TAY"));
    else if (type == DISP_FP_SAVED) display.println(F("DA LUU VAN TAY"));
    else if (type == DISP_FP_DELETED) display.println(F("DA XOA VAN TAY"));
    else display.println(F("THAO TAC THAT BAI"));
    display.drawFastHLine(8, 20, 112, SSD1306_WHITE);
    display.setCursor(38, 28);
    display.print(F("ID #"));
    display.println(id);
    display.setCursor(12, 45);
    if (type == DISP_FP_ENROLL) display.println(F("DAT-NHAC-DAT LAI"));
    else if (type == DISP_FP_FAILED) display.println(F("VUI LONG THU LAI"));
    else display.println(F("HOAN TAT"));
    display.display();
}
