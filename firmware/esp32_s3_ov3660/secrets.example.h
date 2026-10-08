#pragma once

// 1: ESP32 tu phat Wi-Fi rieng cho laptop ket noi truc tiep.
// 0: ESP32 ket noi vao Wi-Fi nha bang WIFI_SSID/WIFI_PASSWORD.
#define CAMERA_USE_SOFT_AP 0
#define CAMERA_AP_SSID "FaceCheck-Camera"
#define CAMERA_AP_PASSWORD "FaceCheck2026"

// Sao chép tệp này thành secrets.h rồi điền Wi-Fi của bạn.
// secrets.h đã được .gitignore bỏ qua để tránh đưa mật khẩu lên Git.
#define WIFI_SSID "TEN_WIFI"
#define WIFI_PASSWORD "MAT_KHAU_WIFI"
