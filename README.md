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
