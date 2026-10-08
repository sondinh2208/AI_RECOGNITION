# ESP32-S3 WROOM CAM + OV3660

Firmware ngày 1 cho thiết bị FaceCheck. Bo chỉ thu hình và phát MJPEG; toàn bộ
YOLO, MediaPipe và ArcFace vẫn chạy trên laptop.

## Cách 1: PlatformIO trong VS Code

1. Mở thư mục `firmware/esp32_s3_ov3660` bằng PlatformIO.
2. Sửa `secrets.h` với Wi-Fi thật.
3. Cắm USB-UART và kiểm tra `COM11`.
4. Chọn `Build`, sau đó `Upload`, rồi `Monitor`.

`platformio.ini` đã cấu hình ESP32-S3, OPI PSRAM, cổng COM11 và Serial 115200.

## Cách 2: Arduino IDE

1. Cài Arduino IDE 2.x.
2. Cài board package `esp32 by Espressif Systems`.
3. Mở `esp32_s3_ov3660.ino`.
4. Chọn board `ESP32S3 Dev Module`.
5. Chọn `PSRAM: OPI PSRAM` nếu module có ký hiệu `R8`; nếu module ghi `R2`, chọn
   chế độ PSRAM tương ứng của board package.
6. Chọn partition có ít nhất 3 MB APP và cổng `COM11` (USB-UART).

## Wi-Fi

Sao chép `secrets.example.h` thành `secrets.h`, sau đó điền SSID và mật khẩu.
Không commit `secrets.h`; tệp này đã được `.gitignore` bỏ qua.

## Nạp và kiểm tra

1. Nạp qua cổng USB-UART COM11.
2. Mở Serial Monitor ở 115200 baud.
3. Nhấn RST nếu chưa thấy log khởi động.
4. Mở địa chỉ được in ra, ví dụ:

   - `http://192.168.1.105/`
   - `http://192.168.1.105/capture`
   - `http://192.168.1.105:81/stream`
   - `http://192.168.1.105/health`

Laptop và ESP32 phải ở cùng mạng Wi-Fi. Địa chỉ IP thật do router cấp và được
in qua Serial; không sao chép IP ví dụ vào ứng dụng.

## Chạy FaceCheck và kiểm tra FPS

Từ thư mục gốc dự án:

```powershell
.\venv\Scripts\python.exe start_wifi_facecheck.py --simulate-display
```

Chương trình tự tìm camera, đo FPS MJPEG thực nhận và cảnh báo khi dưới 5 FPS.
Firmware dùng QVGA 320x240, JPEG quality 15 và 3 frame buffer để phát mục tiêu
18 FPS qua Wi-Fi 802.11n; màn hình riêng lấy tối đa 7 FPS. TFT lấy từng JPEG mới
nhất qua `/frame.jpg` để tránh tồn khung hình và lệch thời gian.
USB-UART chỉ dùng để nạp và xem log 115200 baud, không còn mang dữ liệu ảnh.
