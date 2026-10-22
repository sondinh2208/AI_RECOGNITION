#ifndef DISPLAY_DRIVER_H
#define DISPLAY_DRIVER_H

#include "config.h"
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

// ==========================================
// KIỂU DỮ LIỆU QUEUE MÀN HÌNH (dùng chung toàn project)
// ==========================================

/**
 * @brief Loại thông điệp gửi đến Task_Display
 */
enum DisplayMsgType {
    DISP_HOME,          // Màn hình chờ quét vân tay
    DISP_SUCCESS,       // Màn hình mở cửa thành công
    DISP_ERROR,         // Màn hình cảnh báo vân tay sai
    DISP_SENSOR_ERROR,  // Màn hình lỗi phần cứng cảm biến
    DISP_FP_ENROLL,
    DISP_FP_SAVED,
    DISP_FP_DELETED,
    DISP_FP_FAILED,
    DISP_FP_LIFT,
    DISP_FP_SECOND,
    DISP_FP_DELETING,
    DISP_FP_SYNC,
    DISP_FP_SYNCED,
    DISP_FACE_CHECK,
    DISP_FACE_DENIED,
    DISP_FACE_SUCCESS,
    DISP_FACE_VERIFIED,
    DISP_FACE_ENROLL,
    DISP_FACE_READY,
    DISP_FACE_SAVING,
    DISP_FACE_SAVED,
    DISP_FACE_ENROLL_FAILED,
    DISP_FACE_CANCELLED,
    DISP_FACE_DELETED
};

/**
 * @brief Thông điệp gửi qua displayQueue giữa các Task
 * @param id: ID vân tay (>0), 0=nút nhấn bên trong, -1=Telegram remote
 */
struct DisplayMsg {
    DisplayMsgType type;
    int id;
};

void attachDisplayQueue(QueueHandle_t queue);
void postDisplay(DisplayMsgType type, int id = 0);
bool readDisplay(DisplayMsg &message, TickType_t timeout);
void renderDisplay(const DisplayMsg &message);

// ==========================================
// API CÔNG KHAI CỦA DISPLAY DRIVER
// ==========================================

/**
 * @brief Khởi tạo màn hình OLED SSD1306 qua giao tiếp I2C
 * @return true nếu khởi tạo thành công, false nếu không tìm thấy màn hình
 */
bool initDisplay();

/**
 * @brief Hiển thị màn hình chính (chờ quét vân tay)
 */
void showHomeScreen();

/**
 * @brief Hiển thị màn hình mở cửa thành công
 * @param id ID vân tay (>0). id=0: Nút nhấn bên trong. id=-1: Mở từ Telegram.
 */
void showSuccessScreen(int id);

/**
 * @brief Hiển thị màn hình cảnh báo khi vân tay không hợp lệ
 */
void showErrorScreen();

/**
 * @brief Hiển thị màn hình báo lỗi phần cứng cảm biến vân tay
 */
void showSensorErrorScreen();

// Hien thi trang thai thao tac quan ly van tay tu server.
void showFingerprintManagementScreen(DisplayMsgType type, int id);

#endif // DISPLAY_DRIVER_H
