#ifndef FINGERPRINT_DRIVER_H
#define FINGERPRINT_DRIVER_H

#include "config.h"

// Định nghĩa các mã trạng thái trả về của checkFingerprint()
#define FP_NO_FINGER     0  // Không có ngón tay đặt trên cảm biến
#define FP_READ_ERROR   -1  // Lỗi đọc hình ảnh hoặc giao tiếp UART
#define FP_NOT_FOUND    -2  // Có đặt tay nhưng không tìm thấy vân tay trong bộ nhớ (Vân tay sai)

/**
 * @brief Khởi tạo cảm biến vân tay AS608 qua UART2
 * @return true nếu tìm thấy cảm biến và xác thực thành công, ngược lại false
 */
bool initFingerprint();

/**
 * @brief Quét và kiểm tra vân tay
 * @return 
 *   - ID vân tay (> 0) nếu tìm thấy vân tay hợp lệ
 *   - FP_NO_FINGER (0) nếu không có ngón tay đặt lên
 *   - FP_NOT_FOUND (-2) nếu vân tay không khớp
 *   - FP_READ_ERROR (-1) nếu lỗi đọc cảm biến
 */
int checkFingerprint();

typedef void (*EnrollCallback)(const char* message);

/**
 * @brief Đăng ký (học) vân tay mới vào bộ nhớ AS608
 * @param id ID cần lưu (1 - 127)
 * @param callback Callback để đẩy thông báo tiến trình ra Telegram (tuỳ chọn)
 * @return true nếu đăng ký thành công, false nếu thất bại
 */
bool enrollFingerprint(uint8_t id, EnrollCallback callback = nullptr);

// Ma loi cua lan dang ky gan nhat, dung de gui ACK ro nghia ve server.
// Tra ve nullptr neu dang ky thanh cong hoac chua co loi.
const char* getLastFingerprintEnrollError();

// ID da ton tai neu lan dang ky bi chan vi trung cung mot ngon tay.
uint16_t getDuplicateFingerprintId();

/**
 * @brief Xóa một ID vân tay khỏi bộ nhớ cảm biến
 * @param id ID cần xóa (1 - 127)
 * @return true nếu xóa thành công
 */
bool deleteFingerprint(uint8_t id);

/**
 * @brief Xóa toàn bộ vân tay trong bộ nhớ cảm biến
 * @return true nếu xóa trắng thành công
 */
bool emptyAllFingerprints();

/**
 * @brief Lấy số lượng mẫu vân tay hiện đang lưu trong cảm biến
 * @return Số lượng vân tay đã đăng ký
 */
uint16_t getFingerprintCount();

// Kiem tra mot slot ID co mau hay khong, khong sua/xoa du lieu.
bool fingerprintExists(uint8_t id);

/**
 * @brief Kiểm tra xem cảm biến vân tay có sẵn sàng hoạt động không
 */
bool isFingerprintConnected();

// Thu khoi phuc AS608 mot lan, khong block lau.
bool tryReconnectFingerprint();

#endif // FINGERPRINT_DRIVER_H
