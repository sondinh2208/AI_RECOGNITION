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
