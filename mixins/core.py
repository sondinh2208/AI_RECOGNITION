"""
==============================================================
CORE MIXIN
Quản lý trạng thái khởi tạo, tải AI models, cache embeddings,
warm-up ngầm và giải phóng tài nguyên hệ thống.
==============================================================
"""

import json
import pickle
import threading
import re
from collections import deque
from pathlib import Path
import numpy as np

from config import (
    ADMIN_WINDOW_WIDTH, ADMIN_WINDOW_HEIGHT, CTK_BG_MAIN,
    DATA_FACES_DIR, DEEPFACE_MODEL_NAME, ENROLLMENT_EMBEDDING_SAMPLES,
)
from ai_engine import (
    check_gpu_quick,
    load_face_model, load_mediapipe_detector,
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
        self.current_page = "dashboard"  # Mặc định mở tab Bảng điều khiển (Dashboard)
        self.kiosk_running = False
        self.enrollment_running = False
        self.is_recognizing = False       # Cờ khóa inference chống spam/giật lag
        self.kiosk_latest_frame = None
        self.kiosk_fps = 0.0
        self.kiosk_face_count = 0
        self.kiosk_thread = None
        self._kiosk_waiting_for_departure = False
        self._kiosk_idle_reset_pending = False
        self._kiosk_face_stable_since = None
        self._kiosk_face_absent_since = None
        self._kiosk_active_face_area = None
        self._kiosk_unknown_attempts = 0
        self._kiosk_conditional_candidate_id = None
        self._kiosk_conditional_confirmations = 0
        self._kiosk_conditional_attempts = 0
        self._kiosk_conditional_started_at = None
        self._kiosk_conditional_distances = []
        self._kiosk_conditional_margins = []
        self._kiosk_conditional_votes = {}
        self._attendance_cooldowns = {}
        
        # --- Dữ liệu lịch sử điểm danh thực tế (lưu bền vững trên đĩa) ---
        self.attendance_history = []
        self._attendance_history_version = 0
        self._load_attendance_history()
        
        # --- Biến trạng thái AI Enrollment ---
        self.is_face_valid = False
        self.ai_status_text = "Dua mat vao khung hinh"
        self.prev_status = None
        self.enrollment_face_samples = deque(maxlen=ENROLLMENT_EMBEDDING_SAMPLES)
        self._last_enrollment_sample_at = 0.0
        self._enrollment_valid_since = None
        self.enrollment_capture_ready = False
        self.enrollment_captured_samples = []
        self.enrollment_captured_frame = None
        self.enrollment_captured_face = None
        self._enrollment_preview_shown = False
        self._enrollment_waiting_for_face_leave = False
        
        # --- AI Models ---
        self.face_model = None
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
        """
        Nạp dữ liệu vector embeddings vào RAM để truy vấn siêu tốc.
        Tự động đối chiếu và dọn dẹp các vector mồ côi (nhân viên đã bị xóa ảnh khỏi database).
        """
        try:
            p = Path("data/embeddings.pkl")
            if p.exists():
                with open(p, "rb") as f:
                    data = pickle.load(f)
                    
                # Quét các ID nhân viên còn ảnh hợp lệ trong DATA_FACES_DIR
                faces_dir = Path(DATA_FACES_DIR)
                active_ids = set()
                if faces_dir.exists():
                    for img_f in faces_dir.glob("*.jpg"):
                        stem = img_f.stem
                        e_id = stem.split("@")[0] if "@" in stem else stem.split("_")[0]
                        active_ids.add(e_id.strip().lower())
                        
                cleaned_data = {}
                need_resave = False
                for emp_id, emp_val in data.items():
                    if str(emp_id).strip().lower() in active_ids:
                        profile = dict(emp_val)
                        role = str(profile.get("role") or "Nhân viên").strip()
                        department = profile.get("department") or profile.get("dept")

                        # Chuẩn hóa hồ sơ cũ từng lưu "Chức vụ - Phòng ban"
                        # trong cùng một trường role.
                        if not department:
                            legacy_parts = re.split(r"\s*[-–]\s*", role, maxsplit=1)
                            if len(legacy_parts) == 2:
                                role, department = legacy_parts[0], legacy_parts[1]
                            else:
                                department = "Phòng IT"
                            need_resave = True

                        if profile.get("role") != role:
                            profile["role"] = role
                            need_resave = True
                        if profile.get("department") != department:
                            profile["department"] = department
                            need_resave = True
                        if "dept" in profile:
                            profile.pop("dept", None)
                            need_resave = True

                        # Hồ sơ benchmark chỉ phục vụ kiểm thử, không được tham gia
                        # điểm danh thật nếu chưa được người quản trị chủ động bật.
                        if "recognition_enabled" not in profile:
                            profile["recognition_enabled"] = (
                                profile.get("source") != "LFW benchmark"
                            )
                            need_resave = True

                        cleaned_data[emp_id] = profile
                    else:
                        print(f"[AI CACHE] Tự động loại bỏ vector mồ côi của nhân viên đã xóa: {emp_id} ({emp_val.get('name')})")
                        need_resave = True
                        
                if need_resave:
                    with open(p, "wb") as f:
                        pickle.dump(cleaned_data, f)
                    print(f"[AI CACHE] Đã đồng bộ lại embeddings.pkl (còn {len(cleaned_data)} nhân viên hoạt động).")
                    
                self.embeddings_cache = cleaned_data
            else:
                self.embeddings_cache = {}
        except Exception as e:
            print(f"[AI] Lỗi nạp embeddings cache: {e}")
            self.embeddings_cache = {}

    def _load_attendance_history(self):
        """Nạp lịch sử điểm danh từ file data/attendance_history.json."""
        hist_file = Path("data/attendance_history.json")
        if hist_file.exists():
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    self.attendance_history = json.load(f)
                    return
            except Exception as e:
                print(f"[HISTORY] Lỗi đọc file lịch sử: {e}")
        self.attendance_history = []

    def _save_attendance_history(self):
        """Lưu lịch sử điểm danh bền vững vào file data/attendance_history.json."""
        try:
            hist_file = Path("data/attendance_history.json")
            hist_file.parent.mkdir(parents=True, exist_ok=True)
            with open(hist_file, "w", encoding="utf-8") as f:
                json.dump(self.attendance_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[HISTORY] Lỗi lưu file lịch sử: {e}")

    def _warmup_deepface_async(self):
        """Khởi động sẵn ArcFace & YOLOv8 ngầm để tab Kiosk không bị đơ giật khi nhận diện lần đầu."""
        try:
            # 1. Warm-up YOLOv8 Face trên GPU CUDA
            if hasattr(self, 'face_model') and self.face_model is not None:
                dummy_yolo = np.zeros((480, 640, 3), dtype=np.uint8)
                self.face_model(dummy_yolo, verbose=False)
                
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
        if self.kiosk_thread is not None and self.kiosk_thread.is_alive():
            self.kiosk_thread.join(timeout=0.5)
        if self.camera_thread is not None and self.camera_thread.is_alive():
            self.camera_thread.join(timeout=0.5)
            
        if self.camera_cap is not None:
            self.camera_cap.release()
            print("[CAMERA] Đã giải phóng camera.")
        self.destroy()
        print("[OK] Admin Panel đã đóng.")
