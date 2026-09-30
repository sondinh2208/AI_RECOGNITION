"""
==============================================================
CORE MIXIN
Quản lý trạng thái khởi tạo, tải AI models, cache embeddings,
warm-up ngầm và giải phóng tài nguyên hệ thống.
==============================================================
"""

import pickle
import threading
from pathlib import Path
import numpy as np

from config import (
    ADMIN_WINDOW_WIDTH, ADMIN_WINDOW_HEIGHT, CTK_BG_MAIN,
    DATA_FACES_DIR, DEEPFACE_MODEL_NAME,
)
from ai_engine import (
    check_gpu_quick,
    load_face_model, load_person_model, load_mediapipe_detector,
)


class CoreMixin:
    """Mixin quản lý vòng đời ứng dụng và nạp tài nguyên AI."""

    def __init__(self):
        super().__init__()
        
        # --- Cấu hình cửa sổ ---
        self.title("FaceCheck - HỆ THỐNG ĐIỂM DANH & QUẢN TRỊ AI")
        self.geometry(f"{ADMIN_WINDOW_WIDTH}x{ADMIN_WINDOW_HEIGHT}")
        self.minsize(1150, 720)
        self.configure(fg_color=CTK_BG_MAIN)
        
        # --- Biến trạng thái Camera chung ---
        self.camera_cap = None
        self.camera_running = False
        self.current_frame = None       # Frame gốc (sạch, không HUD)
        self.latest_processed_frame = None
        self.camera_thread = None
        self.photo_image = None
        self.current_fps = 0
        self.current_conf = 0.0
        
        # --- Biến luồng Kiosk Mode (Nhận diện điểm danh) ---
        self.current_page = "attendance"  # Mặc định mở tab Kiosk
        self.kiosk_running = False
        self.enrollment_running = False
        self.is_recognizing = False       # Cờ khóa inference chống spam/giật lag
        self.kiosk_latest_frame = None
        self.kiosk_fps = 0.0
        self.kiosk_face_count = 0
        self.kiosk_reset_timer = None
        self.kiosk_thread = None
        
        # --- Dữ liệu lịch sử điểm danh gần đây ---
        self.attendance_history = [
            {
                "time": "09:24:17 22/09/2025",
                "name": "Nguyễn Văn A",
                "id": "NV001",
                "role": "Kỹ sư phần mềm",
                "dept": "Phòng IT",
                "status": "Thành công"
            },
            {
                "time": "08:56:03 22/09/2025",
                "name": "Trần Thị B",
                "id": "NV015",
                "role": "Nhân viên kinh doanh",
                "dept": "Phòng Kinh doanh",
                "status": "Thành công"
            },
            {
                "time": "08:52:11 22/09/2025",
                "name": "Lê Văn C",
                "id": "NV023",
                "role": "Kỹ thuật viên",
                "dept": "Phòng Kỹ thuật",
                "status": "Thành công"
            },
            {
                "time": "08:47:36 22/09/2025",
                "name": "Phạm Thị D",
                "id": "NV034",
                "role": "Nhân viên Marketing",
                "dept": "Phòng Marketing",
                "status": "Thành công"
            }
        ]
        
        # --- Biến trạng thái AI Enrollment ---
        self.is_face_valid = False
        self.ai_status_text = "Dua mat vao khung hinh"
        self.prev_status = None
        
        # --- AI Models ---
        self.face_model = None
        self.person_model = None
        self.face_detector = None
        self.device = 'cpu'
        
        # --- Layout eKYC ---
        self.shape_rect = None
        self.constraint_box = None
        self.ekyc_mask = None
        self.frame_width = 640
        self.frame_height = 480
        
        # --- Tạo thư mục data ---
        Path(DATA_FACES_DIR).mkdir(parents=True, exist_ok=True)
        Path("images").mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
        
        # --- Cache Vector nhận diện vào RAM ---
        self.embeddings_cache = None
        
        # --- Load AI Models ---
        self._load_ai_models()
        
        # --- Xây dựng UI ---
        self._build_sidebar()
        self._build_main_area()
        
        # --- Bắt đầu đồng hồ thời gian thực ---
        self._update_live_clock()
        
        # --- Khởi động camera nếu chưa được mở ---
        if self.camera_cap is None:
            self._start_camera()
        
        # --- Xử lý khi đóng cửa sổ ---
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _reload_embeddings_cache(self):
        """Nạp dữ liệu vector embeddings vào RAM để truy vấn siêu tốc, tránh đọc file đĩa lặp đi lặp lại."""
        try:
            p = Path("data/embeddings.pkl")
            if p.exists():
                with open(p, "rb") as f:
                    self.embeddings_cache = pickle.load(f)
            else:
                self.embeddings_cache = {}
        except Exception as e:
            print(f"[AI] Lỗi nạp embeddings cache: {e}")
            self.embeddings_cache = {}

    def _warmup_deepface_async(self):
        """Khởi động sẵn ArcFace & YOLOv8 ngầm để tab Kiosk không bị đơ giật khi nhận diện lần đầu."""
        try:
            # 1. Warm-up YOLOv8 Face & Person trên GPU CUDA
            if hasattr(self, 'face_model') and self.face_model is not None:
                dummy_yolo = np.zeros((480, 640, 3), dtype=np.uint8)
                self.face_model(dummy_yolo, verbose=False)
            if hasattr(self, 'person_model') and self.person_model is not None:
                dummy_yolo = np.zeros((480, 640, 3), dtype=np.uint8)
                self.person_model(dummy_yolo, verbose=False)
                
            # 2. Warm-up DeepFace ArcFace
            print("[AI] Đang warm-up DeepFace ArcFace trong nền...")
            from deepface import DeepFace
            dummy_img = np.zeros((112, 112, 3), dtype=np.uint8)
            DeepFace.represent(
                img_path=dummy_img,
                model_name=DEEPFACE_MODEL_NAME,
                detector_backend="skip",
                enforce_detection=False
            )
            print("[AI] DeepFace ArcFace đã sẵn sàng (Warm-up thành công).")
        except Exception as e:
            print(f"[AI] Warm-up ArcFace note: {e}")

    def _load_ai_models(self):
        """Load tất cả AI models qua ai_engine module."""
        print("[AI] Đang load AI models...")
        self.device = check_gpu_quick()
        self.face_model, self.device = load_face_model(self.device)
        self.person_model, self.device = load_person_model(self.device)
        self.face_detector = load_mediapipe_detector()
        
        # Nạp cache embeddings vào RAM
        self._reload_embeddings_cache()
        
        # Khởi động sẵn DeepFace ArcFace trong nền
        threading.Thread(target=self._warmup_deepface_async, daemon=True).start()
        print("[AI] Load models hoàn tất.")

    def _on_closing(self):
        """Giải phóng tài nguyên khi đóng."""
        self.kiosk_running = False
        self.enrollment_running = False
        self.camera_running = False
        if self.kiosk_reset_timer:
            try:
                self.after_cancel(self.kiosk_reset_timer)
            except Exception:
                pass
        if self.kiosk_thread is not None and self.kiosk_thread.is_alive():
            self.kiosk_thread.join(timeout=0.5)
        if self.camera_thread is not None and self.camera_thread.is_alive():
            self.camera_thread.join(timeout=0.5)
            
        if self.camera_cap is not None:
            self.camera_cap.release()
            print("[CAMERA] Đã giải phóng camera.")
        self.destroy()
        print("[OK] Admin Panel đã đóng.")
