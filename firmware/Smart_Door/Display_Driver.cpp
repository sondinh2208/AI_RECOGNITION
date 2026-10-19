#include "Display_Driver.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1  // Không dùng chân Reset cứng

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
static bool displayReady = false;

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
