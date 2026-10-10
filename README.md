# FaceCheck

Hệ thống nhận diện khuôn mặt và điểm danh dùng YOLOv8, MediaPipe và ArcFace.
Ứng dụng hiện dùng một giao diện web desktop và một launcher duy nhất.

## Cài đặt

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

Để chạy bộ kiểm thử, cài thêm dependency phát triển:

```powershell
pip install -r requirements-dev.txt
```

## Khởi động

Webcam USB hoặc camera laptop (mặc định):

```powershell
python facecheck.py
# hoặc chọn camera cụ thể
python facecheck.py webcam --camera-index 1
```

ESP32-S3 OV3660 truyền ảnh qua USB-OTG:

```powershell
python facecheck.py usb
# nếu cần chỉ định cổng
python facecheck.py usb --usb-port COM12
```

ESP32-S3 OV3660 truyền MJPEG qua Wi-Fi:

```powershell
python facecheck.py wifi
python facecheck.py wifi --camera-host 192.168.1.105
```

Thêm `--simulate-display` vào bất kỳ lệnh nào để mở cửa sổ mô phỏng màn hình
TFT 3.5 inch 480x320 bên cạnh cửa sổ quản trị:

```powershell
python facecheck.py wifi --simulate-display
```

Chỉ chạy một phiên FaceCheck tại một thời điểm. Muốn đổi nguồn camera, hãy đóng
ứng dụng hiện tại rồi chạy lại launcher.

## Kiến trúc chính

- `facecheck.py`: launcher duy nhất; chọn nguồn camera và nạp `.env`.
- `web_admin.py`: quản lý backend và cửa sổ desktop.
- `web_backend/app.py`: API, WebSocket và luồng MJPEG.
- `web_runtime.py`: trạng thái ứng dụng và cầu nối tới pipeline AI.
- `mixins/`: pipeline camera, đăng ký và nhận diện đang được web runtime dùng.
- `web_ui/`: giao diện HTML/CSS/JavaScript.
- `tools/usb_camera_bridge.py`: cầu nối ESP32 USB-OTG sang MJPEG nội bộ.
- `firmware/`: firmware camera Wi-Fi, camera USB và màn hình ESP32-035.

Camera và model chỉ được khởi tạo một lần. Việc chuyển trang trong giao diện
không mở lại camera hoặc tải lại model.

## Kiểm thử

Smoke test không tải model hoặc camera:

```powershell
$env:FACECHECK_WEB_SKIP_AI="1"
python -m unittest discover -s tests -v
```
