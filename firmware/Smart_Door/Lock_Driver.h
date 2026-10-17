#ifndef LOCK_DRIVER_H
#define LOCK_DRIVER_H

#include "config.h"

/**
 * @brief Khởi tạo các chân GPIO cho Relay, Buzzer, LED Xanh và LED Đỏ
 */
void initLock();

/**
 * @brief Kích hoạt Relay mở khóa và bật LED Xanh
 */
void openLock();

/**
 * @brief Ngắt Relay đóng khóa và tắt LED Xanh
 */
void closeLock();

/**
 * @brief Phát 1 tiếng bíp ngắn báo hiệu mở khóa thành công
 */
void alertSuccess();

/**
 * @brief Phát 2 tiếng bíp cảnh báo và bật LED Đỏ khi có lỗi / từ chối
 */
void alertError();

/**
 * @brief Tắt LED Đỏ và còi cảnh báo lỗi
 */
void clearErrorAlert();

/**
 * @brief Lấy trạng thái hiện tại của khóa
 * @return true nếu khóa đang mở, false nếu đang đóng
 */
bool isLockOpen();

#endif // LOCK_DRIVER_H