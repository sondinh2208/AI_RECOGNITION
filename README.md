# Face Recognition Kiosk

He thong nhan dien khuon mat Kiosk su dung YOLOv8 + DeepFace.

## Cau truc thu muc

```
AI_RECOGNITION/
├── venv/                  # Virtual environment
├── data/
│   ├── known_faces/       # Anh khuon mat da dang ky
│   └── captures/          # Anh chup tu camera
├── models/                # File model (.pt, .onnx)
├── logs/                  # Log he thong
├── kiosk_camera.py        # Module camera chinh
├── requirements.txt       # Thu vien can thiet
└── README.md
```

## Cach chay

```bash
# Kich hoat venv
.\venv\Scripts\activate

# Chay chuong trinh camera
python kiosk_camera.py
```

## Phim tat
- `Q` hoac `ESC`: Thoat chuong trinh

## Giao diện HTML/CSS desktop

Giao diện mới là một Single Page Application chạy trong cửa sổ `pywebview`.
Giao diện CustomTkinter và entry point `admin_panel.py` vẫn được giữ nguyên để
đối chiếu hoặc chạy lại khi cần.

### Cài đặt

```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Chạy giao diện mới

```powershell
python web_admin.py
```

### Mô phỏng Raspberry Pi khi chưa có phần cứng

Chạy một backend và một camera nhưng mở hai cửa sổ: kiosk 3.5 inch 480x320 và giao diện
quản trị desktop.

```powershell
python web_admin.py --simulate-pi
```

Với camera ESP32-S3 truyền qua USB-OTG, dùng lệnh dưới đây để khởi động đồng thời
cầu nối camera, màn TFT 3.5 inch 480x320 mô phỏng và cửa sổ quản trị:

```powershell
.\venv\Scripts\python.exe start_usb_facecheck.py --simulate-display
```

### Camera ESP32-S3 qua Wi-Fi (khuyến nghị)

Firmware `firmware/esp32_s3_ov3660` nhận nguồn qua adapter/pin 5V và truyền ảnh
MJPEG qua Wi-Fi; cáp USB-UART chỉ cần khi nạp firmware hoặc xem log. Camera, laptop
và màn hình ESP32-035 phải cùng kết nối mạng Wi-Fi 2.4 GHz.

Nạp firmware camera qua USB-UART:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -d firmware\esp32_s3_ov3660 -t upload
```

Sau đó có thể tháo USB-UART, cấp nguồn 5V riêng cho camera và chạy:

```powershell
.\venv\Scripts\python.exe start_wifi_facecheck.py --simulate-display
```

Launcher tự tìm `facecheck-cam.local`, nếu cần sẽ quét mạng LAN, đo FPS nguồn rồi
mới mở FaceCheck. Có thể chỉ định IP camera bằng `--camera-host 192.168.8.x`.
Firmware đặt mục tiêu nguồn 18 FPS ở CIF; kênh màn hình được giới hạn 8 FPS để tránh tồn
frame. Nếu phép đo báo dưới 5 FPS, xem RSSI trong log USB-UART và đặt thiết bị gần
router hơn (khuyến nghị RSSI từ -67 dBm trở lên).

`--simulate-pi` vẫn được giữ như bí danh tương thích; tên mới
`--simulate-display` phản ánh đúng kiến trúc màn hình ESP32 độc lập.

Cửa sổ Kiosk bị khóa ở trang nhận diện và không hiển thị thanh điều hướng, form
quản trị hay bảng lịch sử. Các trang Dashboard, Người đăng ký và Lịch sử ở cửa
sổ quản trị không làm dừng camera. Khi thoát khỏi trang đăng ký, camera tự quay
lại chế độ điểm danh.

Ứng dụng khởi động API nội bộ tại `http://127.0.0.1:8765` và mở trong cửa sổ
desktop. Nếu máy chưa có `pywebview`, hệ thống sẽ mở cùng giao diện trong trình
duyệt mặc định.

### Kiến trúc

- `web_admin.py`: entry point desktop và quản lý vòng đời máy chủ.
- `web_backend/app.py`: API/stream camera/WebSocket nội bộ.
- `web_runtime.py`: adapter trạng thái, gọi trực tiếp các worker camera, đăng ký
  và nhận diện hiện có trong `mixins/`.
- `web_ui/`: HTML, CSS và JavaScript SPA; không chứa thuật toán AI hoặc ngưỡng
  nhận diện.

Camera và model chỉ được khởi tạo một lần. Chuyển tab chỉ đổi trang đang hiển thị,
không tải lại document hoặc model. Dữ liệu vẫn dùng trực tiếp
`data/embeddings.pkl`, `data/attendance_history.json`, `data/faces/`,
`database/images/` và `models/`.

### Kiểm thử nhanh không tải model/camera

```powershell
$env:FACECHECK_WEB_SKIP_AI="1"
python -m unittest tests.test_web_ui -v
```

Biến môi trường trên chỉ dành cho smoke test giao diện/API. Khi chạy thực tế bằng
`python web_admin.py`, không đặt biến này.
