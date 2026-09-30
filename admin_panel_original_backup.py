"""
==============================================================
HỆ THỐNG QUẢN TRỊ AI - Admin Panel
Backend UI xây dựng bằng CustomTkinter + OpenCV Camera
==============================================================
Chức năng:
  - Dashboard tổng quan
  - Thêm nhân viên mới (Form + Camera trực tiếp)
  - Quản lý Database khuôn mặt
  - Lịch sử ra vào

Kiến trúc: OOP (Class-based), non-blocking camera loop
Tích hợp: AI Engine + eKYC Renderer (shared modules)
==============================================================
"""

import cv2
import mediapipe as mp
import customtkinter as ctk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw
import numpy as np
import sys
import os
import pickle
import threading
import time
from datetime import datetime
from pathlib import Path

# Đảm bảo UTF-8 encoding trên Windows Terminal để DeepFace/Logging không bị lỗi charmap
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from config import (
    # Admin Panel UI
    CTK_BG_DARK, CTK_BG_MAIN, CTK_ACCENT, CTK_PRIMARY,
    CTK_SUCCESS, CTK_DANGER, CTK_WARNING,
    CTK_TEXT, CTK_TEXT_DIM, CTK_CARD, CTK_SIDEBAR_HOVER, CTK_BTN_ACTIVE,
    ADMIN_WINDOW_WIDTH, ADMIN_WINDOW_HEIGHT, ADMIN_SIDEBAR_WIDTH,
    ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT, ADMIN_CAMERA_FPS_DELAY,
    DATA_FACES_DIR, DEEPFACE_MODEL_NAME,
    ARCFACE_THRESHOLD, KIOSK_MIN_FACE_WIDTH, KIOSK_RESET_DELAY_MS,
)
from ai_engine import (
    check_gpu_quick,
    load_face_model, load_person_model, load_mediapipe_detector,
    detect_faces, detect_persons,
    check_face_constraints,
    calculate_cosine_distance, draw_kiosk_face_box,
)
from ekyc_renderer import (
    compute_ekyc_layout, create_rounded_rect_mask,
    render_full_hud,
)


# ============================================
# AVATAR RENDERING HELPERS
# ============================================
def make_circular_avatar(pil_img, size=(90, 90), border_color="#10b981", border_width=3):
    """Cắt ảnh thành hình tròn với viền màu hiện đại (Anti-aliased)."""
    try:
        w, h = pil_img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        cropped = pil_img.crop((left, top, left + min_dim, top + min_dim)).resize(size, Image.LANCZOS)
        
        mask = Image.new("L", size, 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0, size[0], size[1]), fill=255)
        
        circular = Image.new("RGBA", size, (0, 0, 0, 0))
        circular.paste(cropped.convert("RGBA"), (0, 0), mask=mask)
        
        if border_width > 0 and border_color:
            draw_b = ImageDraw.Draw(circular)
            draw_b.ellipse(
                (border_width // 2, border_width // 2, size[0] - border_width // 2 - 1, size[1] - border_width // 2 - 1),
                outline=border_color, width=border_width
            )
        return circular
    except Exception:
        return create_default_avatar(size=size, bg_color="#e2e8f0", border_color=border_color)


def create_default_avatar(size=(90, 90), bg_color="#f1f5f9", border_color="#cbd5e1"):
    """Tạo avatar mặc định hình tròn dạng Vector khi chưa có ảnh."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size[0] - 3, size[1] - 3), fill=bg_color, outline=border_color, width=2)
    cx, cy = size[0] // 2, size[1] // 2
    r_head = size[0] // 5
    draw.ellipse((cx - r_head, cy - r_head - 7, cx + r_head, cy + r_head - 7), fill="#94a3b8")
    draw.chord((cx - size[0]//3, cy + 4, cx + size[0]//3, cy + size[0]//2 + 8), 0, 180, fill="#94a3b8")
    return img


# ============================================
# CustomTkinter Theme
# ============================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")



# ============================================
# LỚP CHÍNH: AdminPanel
# ============================================
class AdminPanel(ctk.CTk):
    """
    Cửa sổ chính của hệ thống Quản trị AI.
    Tích hợp CustomTkinter + OpenCV Camera + AI Pipeline.
    """
    
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
        """Khởi động sẵn ArcFace ngầm để tab Kiosk không bị đơ giật 2-3 giây khi nhận diện lần đầu."""
        try:
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
    

    # ============================================
    # SIDEBAR (Thanh điều hướng trái)
    # ============================================
    def _build_sidebar(self):
        """Xây dựng thanh sidebar bên trái chuẩn SaaS FaceCheck."""
        self.sidebar = ctk.CTkFrame(
            self,
            width=ADMIN_SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=CTK_BG_DARK,
            border_width=1,
            border_color=CTK_ACCENT,
        )
        self.sidebar.grid(row=0, column=0, sticky="nswe")
        self.sidebar.grid_propagate(False)
        
        # --- Logo / Tiêu đề ---
        logo_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_container.pack(fill="x", padx=16, pady=(20, 16))
        
        # Icon tròn / bo góc FaceCheck bên trái
        badge_box = ctk.CTkFrame(
            logo_container, width=42, height=42, corner_radius=10,
            fg_color=("#2563eb", "#2563eb")
        )
        badge_box.pack(side="left", padx=(0, 10))
        badge_box.pack_propagate(False)
        ctk.CTkLabel(
            badge_box, text="📷", font=ctk.CTkFont(size=20),
            text_color="#ffffff"
        ).place(relx=0.5, rely=0.5, anchor="center")
        
        # Text Header
        logo_text_frame = ctk.CTkFrame(logo_container, fg_color="transparent")
        logo_text_frame.pack(side="left", fill="both", expand=True)
        
        ctk.CTkLabel(
            logo_text_frame, text="FaceCheck",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(fill="x")
        
        ctk.CTkLabel(
            logo_text_frame, text="Hệ thống nhận diện khuôn mặt",
            font=ctk.CTkFont(size=10),
            text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x")
        
        # --- Separator ---
        ctk.CTkFrame(self.sidebar, height=1, fg_color=CTK_ACCENT).pack(
            fill="x", padx=16, pady=(0, 12))
        
        # --- Nav buttons ---
        self.nav_buttons = {}
        nav_items = [
            ("dashboard",     "📊  Bảng điều khiển"),
            ("add_employee",  "👤  Quét khuôn mặt"),
            ("attendance",    "📍  Nhận diện điểm danh"),
            ("database",      "📁  Người đăng ký"),
            ("history",       "📑  Lịch sử ra vào"),
        ]
        
        for page_id, label in nav_items:
            btn = ctk.CTkButton(
                self.sidebar, text=label,
                font=ctk.CTkFont(size=13), height=40, anchor="w",
                corner_radius=8, fg_color="transparent",
                text_color=CTK_TEXT, hover_color=CTK_SIDEBAR_HOVER,
                command=lambda pid=page_id: self._navigate(pid),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[page_id] = btn
        
        self._highlight_nav(self.current_page)
        
        # --- Spacer ---
        ctk.CTkFrame(self.sidebar, fg_color="transparent").pack(fill="both", expand=True)
        
        # --- Footer Widget Trạng thái hệ thống (chuẩn UI screenshot) ---
        status_box = ctk.CTkFrame(
            self.sidebar, corner_radius=10,
            fg_color=("#f1f5f9", "#09111e"), border_width=1, border_color=CTK_ACCENT
        )
        status_box.pack(fill="x", padx=14, pady=(0, 14))
        
        row1 = ctk.CTkFrame(status_box, fg_color="transparent")
        row1.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            row1, text="● ", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CTK_SUCCESS
        ).pack(side="left")
        ctk.CTkLabel(
            row1, text="Hệ thống hoạt động",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=CTK_TEXT
        ).pack(side="left")
        
        ctk.CTkLabel(
            status_box, text="Camera • Kết nối ổn định",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM
        ).pack(fill="x", padx=12, pady=(0, 10))
        
        # Theme switcher
        theme_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        theme_row.pack(fill="x", padx=14, pady=(0, 14))
        
        self.appearance_mode_menu = ctk.CTkOptionMenu(
            theme_row, values=["Dark", "Light"],
            command=self._change_appearance_mode_event,
            fg_color=("#f1f5f9", "#111c2e"), button_color=CTK_ACCENT,
            text_color=CTK_TEXT, corner_radius=8, height=30
        )
        self.appearance_mode_menu.pack(fill="x")
        self.appearance_mode_menu.set("Dark")
    

    def _highlight_nav(self, active_page_id):
        """Đổi màu nút đang active trên sidebar."""
        for pid, btn in self.nav_buttons.items():
            if pid == active_page_id:
                btn.configure(
                    fg_color=CTK_BTN_ACTIVE,
                    hover_color=("#1d4ed8", "#1d4ed8"),
                    text_color=("#ffffff", "#ffffff"),
                    font=ctk.CTkFont(size=13, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=CTK_SIDEBAR_HOVER,
                    text_color=CTK_TEXT,
                    font=ctk.CTkFont(size=13),
                )
                
    def _change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode)
        self._highlight_nav(self.current_page)
        if self.current_page == "database" and hasattr(self, "search_entry"):
            self._load_database_to_scrollable(self.search_entry.get().lower())
    
    def _navigate(self, page_id):
        """Xử lý chuyển trang."""
        self.current_page = page_id
        self._highlight_nav(page_id)
        print(f"[NAV] Chuyển đến trang: {page_id}")
        self._show_page(page_id)
        
    def _show_page(self, page_id):
        """Hiển thị frame được chọn và xử lý chuyển đổi camera tương ứng."""
        self.current_page = page_id
        
        # 1. Điều chỉnh hiển thị Header Bar:
        # Trang "database" (Người đăng ký) và "add_employee" (Đăng ký mới) dùng header riêng nguyên bản để không bị trùng lặp
        if hasattr(self, 'header_bar'):
            if page_id in ["database", "add_employee"]:
                self.header_bar.grid_remove()
            else:
                self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
                if hasattr(self, 'header_title_label'):
                    if page_id == "attendance":
                        self.header_title_label.configure(text="Nhận diện điểm danh")
                        self.header_sub_label.configure(text="Quét khuôn mặt để nhận diện và điểm danh tự động")
                    elif page_id == "history":
                        self.header_title_label.configure(text="Lịch sử ra vào")
                        self.header_sub_label.configure(text="Xem toàn bộ lịch sử điểm danh và ra vào của nhân sự")
                    elif page_id == "dashboard":
                        self.header_title_label.configure(text="Bảng điều khiển")
                        self.header_sub_label.configure(text="Tổng quan hệ thống và trạng thái nhận diện khuôn mặt")
                    else:
                        self.header_title_label.configure(text="Cài đặt hệ thống")
                        self.header_sub_label.configure(text="Cấu hình hệ thống và tham số nhận diện")

        # 2. Ẩn tất cả các trang
        for pid, frame in getattr(self, "page_frames", {}).items():
            frame.grid_remove()
            
        # 3. Hiển thị trang được chọn
        if page_id in self.page_frames:
            self.page_frames[page_id].grid(row=0, column=0, sticky="nswe")
            if page_id == "database" and hasattr(self, "search_entry"):
                self._load_database_to_scrollable(self.search_entry.get().lower())
            elif page_id == "history" and hasattr(self, "_reload_full_history"):
                self._reload_full_history()
            elif page_id == "dashboard" and hasattr(self, "_reload_dashboard_stats"):
                self._reload_dashboard_stats()
            
        # 4. Điều phối luồng Camera chuyên biệt (Chống xung đột tài nguyên & giật lag)
        if page_id == "attendance":
            self.enrollment_running = False
            # Chờ luồng enrollment cũ nhả camera_cap để tránh lock DirectShow
            if self.camera_thread is not None and self.camera_thread.is_alive():
                self.camera_thread.join(timeout=0.08)
                
            self.kiosk_running = True
            if hasattr(self, 'set_idle_state'):
                self.set_idle_state()
            if self.camera_cap is None:
                self._start_camera()
            else:
                if self.kiosk_thread is None or not self.kiosk_thread.is_alive():
                    self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                    self.kiosk_thread.start()
                self._update_kiosk_frame()
        elif page_id == "add_employee":
            self.kiosk_running = False
            # Chờ luồng kiosk cũ nhả camera_cap để tránh lock DirectShow
            if self.kiosk_thread is not None and self.kiosk_thread.is_alive():
                self.kiosk_thread.join(timeout=0.08)
                
            self.enrollment_running = True
            if self.camera_cap is None:
                self._start_camera()
            else:
                if self.camera_thread is None or not self.camera_thread.is_alive():
                    self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                    self.camera_thread.start()
                self._update_frame()
        else:
            self.kiosk_running = False
            self.enrollment_running = False

    # ============================================
    # MAIN AREA (Khu vực chính - Phải)
    # ============================================
    def _build_main_area(self):
        """Xây dựng khu vực chính gồm Header Bar và Container các Trang."""
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nswe", padx=18, pady=(16, 16))
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=0)  # Top App Bar
        self.main_container.grid_rowconfigure(1, weight=1)  # Pages Container
        
        # ==========================================
        # TOP APP BAR (Tiêu đề, Đồng hồ, Profile Admin)
        # ==========================================
        self.header_bar = ctk.CTkFrame(self.main_container, fg_color="transparent", height=56)
        self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.header_bar.grid_columnconfigure(0, weight=1)
        self.header_bar.grid_columnconfigure(1, weight=0)
        
        # Left: Icon + Title + Subtitle
        header_left = ctk.CTkFrame(self.header_bar, fg_color="transparent")
        header_left.grid(row=0, column=0, sticky="w")
        
        icon_app_box = ctk.CTkFrame(
            header_left, width=44, height=44, corner_radius=10,
            fg_color=("#eff6ff", "#13233c"), border_width=1, border_color=("#bfdbfe", "#1e3a5f")
        )
        icon_app_box.pack(side="left", padx=(0, 12))
        icon_app_box.pack_propagate(False)
        ctk.CTkLabel(
            icon_app_box, text="🔲", font=ctk.CTkFont(size=18),
            text_color=("#2563eb", "#38bdf8")
        ).place(relx=0.5, rely=0.5, anchor="center")
        
        title_box = ctk.CTkFrame(header_left, fg_color="transparent")
        title_box.pack(side="left")
        
        self.header_title_label = ctk.CTkLabel(
            title_box, text="Nhận diện điểm danh",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.header_title_label.pack(anchor="w")
        
        self.header_sub_label = ctk.CTkLabel(
            title_box, text="Quét khuôn mặt để nhận diện và điểm danh tự động",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.header_sub_label.pack(anchor="w", pady=(2, 0))
        
        # Right: Date + Clock + Admin Profile
        header_right = ctk.CTkFrame(self.header_bar, fg_color="transparent")
        header_right.grid(row=0, column=1, sticky="e")
        
        # Clock & Date stack
        clock_box = ctk.CTkFrame(header_right, fg_color="transparent")
        clock_box.pack(side="left", padx=(0, 18))
        
        self.header_date_label = ctk.CTkLabel(
            clock_box, text="Thứ Hai, 22/09/2025",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="e"
        )
        self.header_date_label.pack(anchor="e")
        
        self.header_clock_label = ctk.CTkLabel(
            clock_box, text="09:24:17",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=CTK_TEXT, anchor="e"
        )
        self.header_clock_label.pack(anchor="e")
        

        
        # ==========================================
        # PAGES CONTAINER
        # ==========================================
        self.pages_container = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.pages_container.grid(row=1, column=0, sticky="nswe")
        self.pages_container.grid_columnconfigure(0, weight=1)
        self.pages_container.grid_rowconfigure(0, weight=1)
        
        self.page_frames = {}
        
        # --- Trang 1: Nhận diện điểm danh (Kiosk Mode) ---
        self._build_attendance_page()
        self.page_frames["attendance"] = self.attendance_frame
        
        # --- Trang 2: Quét khuôn mặt (Add Employee) ---
        self.main_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=0)
        self.main_frame.grid_rowconfigure(0, weight=1)
        self._build_form_column()
        self._build_camera_column()
        self.page_frames["add_employee"] = self.main_frame
        
        # --- Trang 3: Người đăng ký (Database) ---
        self.database_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self._build_database_page()
        self.page_frames["database"] = self.database_frame
        
        # --- Trang 4: Lịch sử điểm danh (History) ---
        self._build_history_page()
        self.page_frames["history"] = self.history_frame
        
        # --- Trang 5: Tổng quan (Dashboard) ---
        self._build_dashboard_page()
        self.page_frames["dashboard"] = self.dashboard_frame
        
        # Mặc định mở trang Kiosk Điểm danh
        self._show_page("attendance")
    

    # ============================================
    # KIOSK MODE: NHẬN DIỆN ĐIỂM DANH (Trang Kiosk)
    # ============================================
    def _build_attendance_page(self):
        """
        Xây dựng giao diện Kiosk Điểm danh công nghệ cao (chuẩn thiết kế screenshot).
        Gồm 2 phần:
          - Top Row: Cột Camera bên trái + Cột Kết quả nhận diện (Result Card) bên phải.
          - Bottom Row: Bảng Lịch sử điểm danh gần đây (Recent Attendance History).
        """
        self.attendance_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        self.attendance_frame.grid_columnconfigure(0, weight=1)
        self.attendance_frame.grid_rowconfigure(0, weight=0)
        self.attendance_frame.grid_rowconfigure(1, weight=1)
        
        # ==========================================
        # TOP ROW: CAMERA (Trái) & RESULT CARD (Phải)
        # ==========================================
        top_row = ctk.CTkFrame(self.attendance_frame, fg_color="transparent")
        top_row.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        top_row.grid_columnconfigure(0, weight=57, uniform="top_cards")   # Camera Left (~57%)
        top_row.grid_columnconfigure(1, weight=43, uniform="top_cards")   # Result Right (~43%)
        
        # ------------------------------------------
        # 1. CỘT TRÁI: CAMERA VÀ ĐIỀU KHIỂN
        # ------------------------------------------
        cam_card = ctk.CTkFrame(
            top_row, fg_color=CTK_CARD, corner_radius=14,
            border_width=1, border_color=CTK_ACCENT
        )
        cam_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        
        # Khung hiển thị Video với overlay badge (Chuẩn tỉ lệ camera, không méo, không phóng to mặt)
        cam_display_box = ctk.CTkFrame(cam_card, fg_color="#000000", corner_radius=10, height=315)
        cam_display_box.pack(fill="x", padx=12, pady=(12, 10))
        cam_display_box.pack_propagate(False)
        
        # Video feed label
        self.kiosk_camera_label = ctk.CTkLabel(
            cam_display_box, text="Đang khởi tạo camera Kiosk...",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
            fg_color="transparent"
        )
        self.kiosk_camera_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        # Overlay top bar (Camera đang hoạt động & FPS / Settings)
        overlay_bar = ctk.CTkFrame(cam_display_box, fg_color="transparent")
        overlay_bar.place(relx=0.02, rely=0.03, relwidth=0.96)
        
        # Badge Camera đang hoạt động (Green pill)
        status_pill = ctk.CTkFrame(
            overlay_bar, corner_radius=14,
            fg_color=("#0f172a", "#0f172a"), border_width=1, border_color=("#334155", "#334155")
        )
        status_pill.pack(side="left")
        ctk.CTkLabel(
            status_pill, text="● ", font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#10b981"
        ).pack(side="left", padx=(8, 0), pady=3)
        self.lbl_kiosk_cam_status = ctk.CTkLabel(
            status_pill, text="Camera đang hoạt động",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#f8fafc"
        )
        self.lbl_kiosk_cam_status.pack(side="left", padx=(0, 10), pady=3)
        
        # Right: FPS & Gear icon
        fps_pill = ctk.CTkFrame(
            overlay_bar, corner_radius=14,
            fg_color=("#0f172a", "#0f172a"), border_width=1, border_color=("#334155", "#334155")
        )
        fps_pill.pack(side="right")
        self.lbl_kiosk_fps = ctk.CTkLabel(
            fps_pill, text="FPS: 15",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#f8fafc"
        )
        self.lbl_kiosk_fps.pack(side="left", padx=(10, 4), pady=3)
        ctk.CTkLabel(
            fps_pill, text="⚙",
            font=ctk.CTkFont(size=11), text_color="#94a3b8"
        ).pack(side="left", padx=(0, 8), pady=3)
        
        # Banner hướng dẫn quét khuôn mặt (dưới video)
        guide_banner = ctk.CTkFrame(
            cam_card, corner_radius=10,
            fg_color=("#eff6ff", "#0e1a2f"), border_width=1, border_color=("#bfdbfe", "#1e3a5f"),
            height=48
        )
        guide_banner.pack(fill="x", padx=12, pady=(0, 12))
        guide_banner.pack_propagate(False)
        
        icon_scan_box = ctk.CTkFrame(
            guide_banner, width=30, height=30, corner_radius=15,
            fg_color=("#dbeafe", "#172554")
        )
        icon_scan_box.pack(side="left", padx=(10, 8))
        icon_scan_box.pack_propagate(False)
        ctk.CTkLabel(
            icon_scan_box, text="🔲", font=ctk.CTkFont(size=13),
            text_color="#2563eb"
        ).place(relx=0.5, rely=0.5, anchor="center")
        
        guide_text_box = ctk.CTkFrame(guide_banner, fg_color="transparent")
        guide_text_box.pack(side="left", fill="both", expand=True)
        
        self.kiosk_status_title = ctk.CTkLabel(
            guide_text_box, text="Đang quét khuôn mặt...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.kiosk_status_title.pack(fill="x", pady=(4, 0))
        
        self.kiosk_status_desc = ctk.CTkLabel(
            guide_text_box, text="Vui lòng đứng thẳng, nhìn vào camera",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_status_desc.pack(fill="x")
        
        # Soundwave icon
        ctk.CTkLabel(
            guide_banner, text="ılı", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#2563eb", "#38bdf8")
        ).pack(side="right", padx=12)

        # ------------------------------------------
        # 2. CỘT PHẢI: KẾT QUẢ NHẬN DIỆN (RESULT CARD)
        # ------------------------------------------
        self.kiosk_result_card = ctk.CTkFrame(
            top_row, fg_color=CTK_CARD, corner_radius=14,
            border_width=1, border_color=CTK_ACCENT
        )
        self.kiosk_result_card.grid(row=0, column=1, sticky="nsew")
        
        # A. Status Banner (Xanh lá / Đỏ)
        self.kiosk_res_banner_frame = ctk.CTkFrame(
            self.kiosk_result_card, corner_radius=10,
            fg_color=("#f8fafc", "#0b1322"), border_width=1, border_color=CTK_ACCENT,
            height=54
        )
        self.kiosk_res_banner_frame.pack(fill="x", padx=14, pady=(12, 10))
        self.kiosk_res_banner_frame.pack_propagate(False)
        
        self.kiosk_res_icon = ctk.CTkLabel(
            self.kiosk_res_banner_frame, text="🔍",
            font=ctk.CTkFont(size=17, weight="bold"),
            width=36, height=36, corner_radius=18,
            fg_color=("#e2e8f0", "#172338"), text_color=("#2563eb", "#38bdf8")
        )
        self.kiosk_res_icon.pack(side="left", padx=10)
        
        banner_text_col = ctk.CTkFrame(self.kiosk_res_banner_frame, fg_color="transparent")
        banner_text_col.pack(side="left", fill="both", expand=True)
        
        self.kiosk_res_title = ctk.CTkLabel(
            banner_text_col, text="Đang chờ nhận diện...",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.kiosk_res_title.pack(fill="x", pady=(6, 0))
        
        self.kiosk_res_sub = ctk.CTkLabel(
            banner_text_col, text="Sẵn sàng quét khuôn mặt tự động",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_res_sub.pack(fill="x")
        
        # B. Profile Info Section (Avatar + Họ tên + Mã NV + Chức vụ + Phòng ban)
        profile_section = ctk.CTkFrame(self.kiosk_result_card, fg_color="transparent")
        profile_section.pack(fill="x", padx=14, pady=(0, 10))
        
        # Avatar (Tròn 74x74)
        self.kiosk_avatar_label = ctk.CTkLabel(profile_section, text="", width=74, height=74)
        self.kiosk_avatar_label.pack(side="left", padx=(0, 12))
        self._set_kiosk_avatar(None, None, border_color="#cbd5e1", size=(74, 74))
        
        # Info stack
        info_col = ctk.CTkFrame(profile_section, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)
        
        self.kiosk_name_label = ctk.CTkLabel(
            info_col, text="Chưa có dữ liệu",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.kiosk_name_label.pack(fill="x", pady=(0, 2))
        
        id_row = ctk.CTkFrame(info_col, fg_color="transparent")
        id_row.pack(fill="x", pady=(0, 4))
        
        self.kiosk_id_badge = ctk.CTkLabel(
            id_row, text="Mã NV: ---",
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#f1f5f9", "#1e293b"), text_color=CTK_TEXT_DIM,
            corner_radius=10, padx=8, pady=2
        )
        self.kiosk_id_badge.pack(side="left")
        
        self.kiosk_role_label = ctk.CTkLabel(
            info_col, text="👤 Chức vụ: ---",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_role_label.pack(fill="x", pady=1)
        
        self.kiosk_dept_label = ctk.CTkLabel(
            info_col, text="🏢 Phòng ban: ---",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_dept_label.pack(fill="x", pady=1)
        
        # C. Congratulatory / Greeting Box (Màu xanh dịu chuẩn screenshot)
        self.kiosk_greeting_card = ctk.CTkFrame(
            self.kiosk_result_card, corner_radius=10,
            fg_color=("#f0fdf4", "#052312"), border_width=1, border_color=("#bbf7d0", "#0c4a25"),
            height=54
        )
        self.kiosk_greeting_card.pack(fill="x", padx=14, pady=(0, 8))
        self.kiosk_greeting_card.pack_propagate(False)
        
        ctk.CTkLabel(
            self.kiosk_greeting_card, text="🎉",
            font=ctk.CTkFont(size=16)
        ).pack(side="left", padx=(10, 6))
        
        self.kiosk_greeting_text = ctk.CTkLabel(
            self.kiosk_greeting_card,
            text="Vui lòng đứng thẳng, nhìn vào camera để điểm danh.\nChúc bạn một ngày làm việc hiệu quả!",
            font=ctk.CTkFont(size=10), text_color=("#166534", "#86efac"),
            anchor="w", justify="left"
        )
        self.kiosk_greeting_text.pack(side="left", fill="both", expand=True)
        
        ctk.CTkLabel(
            self.kiosk_greeting_card, text="😊",
            font=ctk.CTkFont(size=14)
        ).pack(side="right", padx=10)
        
        # D. Bottom Timestamp Row
        time_row = ctk.CTkFrame(self.kiosk_result_card, fg_color="transparent")
        time_row.pack(fill="x", padx=14, pady=(0, 8))
        
        self.kiosk_time_label = ctk.CTkLabel(
            time_row, text="🕒 Trạng thái: Chờ quét",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_time_label.pack(side="left")
        
        self.kiosk_saved_badge = ctk.CTkLabel(
            time_row, text="● Sẵn sàng",
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#f1f5f9", "#1e293b"), text_color=CTK_TEXT_DIM,
            corner_radius=10, padx=8, pady=2
        )
        self.kiosk_saved_badge.pack(side="right")

        # E. Nút bấm Thủ công: "Quét người tiếp theo" (Theo yêu cầu chống UI Freeze)
        self.btn_scan_next = ctk.CTkButton(
            self.kiosk_result_card,
            text="🔄  Quét người tiếp theo",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            corner_radius=8,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            text_color="#ffffff",
            command=self._reset_for_next_scan
        )
        # Mặc định ở trạng thái chờ (Idle) -> ẨN nút đi
        self.btn_scan_next.pack_forget()

        # ==========================================
        # BOTTOM ROW: LỊCH SỬ ĐIỂM DANH GẦN ĐÂY
        # ==========================================
        history_card = ctk.CTkFrame(
            self.attendance_frame, fg_color=CTK_CARD, corner_radius=14,
            border_width=1, border_color=CTK_ACCENT
        )
        history_card.grid(row=1, column=0, sticky="nsew")
        
        # Header Lịch sử
        hist_header = ctk.CTkFrame(history_card, fg_color="transparent")
        hist_header.pack(fill="x", padx=18, pady=(10, 6))
        
        ctk.CTkLabel(
            hist_header, text="🕒  Lịch sử điểm danh gần đây",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=CTK_TEXT, anchor="w"
        ).pack(side="left")
        
        btn_view_all = ctk.CTkButton(
            hist_header, text="Xem tất cả →",
            font=ctk.CTkFont(size=11, weight="bold"), height=24,
            fg_color="transparent", hover_color=CTK_SIDEBAR_HOVER,
            text_color=CTK_PRIMARY, command=lambda: self._navigate("history")
        )
        btn_view_all.pack(side="right")
        
        # Table Header Row
        tbl_head = ctk.CTkFrame(
            history_card, height=30, corner_radius=6,
            fg_color=("#f8fafc", "#0b121e")
        )
        tbl_head.pack(fill="x", padx=18, pady=(0, 4))
        tbl_head.pack_propagate(False)
        
        cols = [
            ("Thời gian", 0.18),
            ("Nhân viên", 0.22),
            ("Mã NV", 0.12),
            ("Chức vụ", 0.22),
            ("Phòng ban", 0.14),
            ("Trạng thái", 0.12)
        ]
        
        for name, relw in cols:
            f = ctk.CTkFrame(tbl_head, fg_color="transparent")
            f.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(
                f, text=name, font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_TEXT_DIM, anchor="w"
            ).pack(fill="both", expand=True, padx=8)
            
        # Table Scrollable / Dynamic Rows
        self.kiosk_history_rows_container = ctk.CTkFrame(history_card, fg_color="transparent")
        self.kiosk_history_rows_container.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        
        self._render_recent_attendance_table()
        
        return self.attendance_frame

    def _render_recent_attendance_table(self):
        """Vẽ danh sách các lượt điểm danh gần đây."""
        if not hasattr(self, 'kiosk_history_rows_container'):
            return
            
        for child in self.kiosk_history_rows_container.winfo_children():
            child.destroy()
            
        for i, item in enumerate(self.attendance_history[:5]):
            bg = ("#ffffff", "#0d1522") if i % 2 == 0 else ("#f8fafc", "#111c2e")
            row = ctk.CTkFrame(
                self.kiosk_history_rows_container, height=32, corner_radius=6,
                fg_color=bg
            )
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            
            # Thời gian
            f1 = ctk.CTkFrame(row, fg_color="transparent")
            f1.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(f1, text=item.get("time", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="both", expand=True, padx=8)
            
            # Nhân viên
            f2 = ctk.CTkFrame(row, fg_color="transparent")
            f2.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(f2, text=item.get("name", ""), font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w").pack(fill="both", expand=True, padx=8)
            
            # Mã NV
            f3 = ctk.CTkFrame(row, fg_color="transparent")
            f3.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(f3, text=item.get("id", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT, anchor="w").pack(fill="both", expand=True, padx=8)
            
            # Chức vụ
            f4 = ctk.CTkFrame(row, fg_color="transparent")
            f4.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(f4, text=item.get("role", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="both", expand=True, padx=8)
            
            # Phòng ban
            f5 = ctk.CTkFrame(row, fg_color="transparent")
            f5.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(f5, text=item.get("dept", "Phòng Ban"), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="both", expand=True, padx=8)
            
            # Trạng thái Badge
            f6 = ctk.CTkFrame(row, fg_color="transparent")
            f6.pack(side="left", fill="both", expand=True)
            is_success = "Thành công" in item.get("status", "")
            badge_fg = ("#dcfce7", "#064e3b") if is_success else ("#fee2e2", "#7f1d1d")
            badge_tx = ("#16a34a", "#86efac") if is_success else ("#ef4444", "#fca5a5")
            icon = "● " if is_success else "✕ "
            
            b = ctk.CTkLabel(
                f6, text=icon + item.get("status", "Thành công"),
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color=badge_fg, text_color=badge_tx,
                corner_radius=10, padx=8, pady=2
            )
            b.pack(side="left", padx=8)

    def _add_attendance_record(self, record):
        """Thêm bản ghi điểm danh mới và cập nhật bảng."""
        self.attendance_history.insert(0, record)
        if len(self.attendance_history) > 20:
            self.attendance_history.pop()
        self._render_recent_attendance_table()

    def _update_live_clock(self):
        """Cập nhật ngày và đồng hồ số thời gian thực trên Top Header Bar."""
        now = datetime.now()
        days = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
        day_str = days[now.weekday()]
        date_str = f"{day_str}, {now.strftime('%d/%m/%Y')}"
        time_str = now.strftime("%H:%M:%S")
        
        if hasattr(self, 'header_date_label') and self.header_date_label.winfo_exists():
            self.header_date_label.configure(text=date_str)
        if hasattr(self, 'header_clock_label') and self.header_clock_label.winfo_exists():
            self.header_clock_label.configure(text=time_str)
            
        self.after(1000, self._update_live_clock)

    # ============================================
    # FORM NHẬP LIỆU (Cột trái)
    # ============================================
    def _build_form_column(self):
        """Xây dựng form đăng ký nhân viên mới."""
        form_card = ctk.CTkFrame(
            self.main_frame, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        form_card.grid(row=0, column=0, sticky="nswe", padx=(0, 14))
        
        # --- Header ---
        header = ctk.CTkFrame(form_card, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 10))
        
        header_row = ctk.CTkFrame(header, fg_color="transparent")
        header_row.pack(fill="x")
        
        ctk.CTkLabel(
            header_row, text="ĐĂNG KÝ KHUÔN MẶT MỚI",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CTK_PRIMARY, anchor="w",
        ).pack(side="left")
        
        ctk.CTkButton(
            header_row, text="📁 Người đăng ký",
            font=ctk.CTkFont(size=12, weight="bold"), height=30, corner_radius=6,
            fg_color=("#f1f5f9", "#111c2e"), hover_color=CTK_SIDEBAR_HOVER,
            text_color=CTK_TEXT, border_width=1, border_color=CTK_ACCENT,
            command=lambda: self._navigate("database")
        ).pack(side="right")
        
        ctk.CTkLabel(
            header, text="Nhập thông tin nhân viên và chụp ảnh khuôn mặt qua Camera AI",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(4, 0))
        
        # --- Fields Frame ---
        fields = ctk.CTkFrame(form_card, fg_color="transparent")
        fields.pack(fill="x", padx=24, pady=(12, 10))
        
        # Helper: Input có icon bên trái
        def create_icon_input(parent, label_text, icon_symbol, placeholder):
            ctk.CTkLabel(
                parent, text=label_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_TEXT_DIM, anchor="w"
            ).pack(fill="x", pady=(0, 6))
            
            input_box = ctk.CTkFrame(
                parent, height=42, corner_radius=8,
                border_width=1, border_color=CTK_ACCENT,
                fg_color=("#f8fafc", "#080e1a")
            )
            input_box.pack(fill="x", pady=(0, 16))
            input_box.pack_propagate(False)
            
            icon_lbl = ctk.CTkLabel(
                input_box, text=icon_symbol,
                font=ctk.CTkFont(size=14), text_color=("#475569", "#94a3b8"),
                width=34
            )
            icon_lbl.pack(side="left", padx=(8, 0))
            
            entry = ctk.CTkEntry(
                input_box, placeholder_text=placeholder,
                font=ctk.CTkFont(size=13),
                fg_color="transparent", border_width=0,
                text_color=CTK_TEXT,
                placeholder_text_color=("#94a3b8", "#64748b")
            )
            entry.pack(side="left", fill="both", expand=True, padx=(4, 10))
            return entry
            
        # 1. Họ và tên
        self.entry_name = create_icon_input(
            fields, "HỌ VÀ TÊN NHÂN VIÊN", "👤", "Nguyễn Văn A"
        )
        
        # 2. Mã nhân viên
        self.entry_id = create_icon_input(
            fields, "MÃ NHÂN VIÊN", "🪪", "NV001"
        )
        
        # 3. Chức vụ / Phòng ban
        self.entry_role = create_icon_input(
            fields, "CHỨC VỤ / PHÒNG BAN", "✉", "Kỹ sư phần mềm - Phòng IT"
        )
        
        # --- Buttons Frame ---
        btn_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(6, 12))
        
        self.btn_capture = ctk.CTkButton(
            btn_frame, text="📸   QUÉT VÀ LƯU KHUÔN MẶT",
            font=ctk.CTkFont(size=13, weight="bold"), height=46,
            corner_radius=8,
            fg_color=("#2563eb", "#2563eb"),
            hover_color=("#1d4ed8", "#1d4ed8"),
            text_color=("#ffffff", "#ffffff"),
            command=self._start_enrollment_process,
        )
        self.btn_capture.pack(fill="x", pady=(0, 6))
        
        self.progress_bar = ctk.CTkProgressBar(
            btn_frame, mode="indeterminate",
            progress_color=("#2563eb", "#38bdf8"),
            fg_color=("#e2e8f0", "#1e293b"),
            height=6, corner_radius=3
        )
        self.progress_bar.pack_forget()

        
        # --- Alert / Guideline Box ---
        alert_box = ctk.CTkFrame(
            form_card, height=44, corner_radius=8,
            border_width=1, border_color=CTK_ACCENT,
            fg_color=("#f1f5f9", "#080e1a")
        )
        alert_box.pack(fill="x", padx=24, pady=(0, 10))
        alert_box.pack_propagate(False)
        
        ctk.CTkLabel(
            alert_box,
            text="ⓘ  Hãy đảm bảo khuôn mặt nằm chính giữa khung hình và đủ ánh sáng.",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
            anchor="w"
        ).pack(side="left", padx=14)

        
        # --- Status & Preview ---
        self.status_label = ctk.CTkLabel(
            form_card, text="",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        )
        self.status_label.pack(fill="x", padx=24, pady=(0, 6))
        
        self.preview_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        self.preview_frame.pack(fill="x", padx=24, pady=(0, 10))
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="", width=120, height=90)
        self.captured_photo = None
    

    # ============================================
    # CAMERA & SYSTEM TELEMETRY (Cột phải)
    # ============================================
    def _build_camera_column(self):
        """Xây dựng vùng hiển thị camera trực tiếp & thông tin hệ thống."""
        right_container = ctk.CTkFrame(
            self.main_frame, fg_color="transparent",
            width=ADMIN_CAMERA_WIDTH + 36,
        )
        right_container.grid(row=0, column=1, sticky="nswe")
        right_container.grid_propagate(False)
        
        # ==========================================
        # CARD 1: CAMERA TRỰC TIẾP
        # ==========================================
        cam_card = ctk.CTkFrame(
            right_container, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        cam_card.pack(fill="x", pady=(0, 12))
        
        # Header
        cam_header = ctk.CTkFrame(cam_card, fg_color="transparent")
        cam_header.pack(fill="x", padx=18, pady=(16, 10))
        
        title_row = ctk.CTkFrame(cam_header, fg_color="transparent")
        title_row.pack(fill="x")
        
        ctk.CTkLabel(
            title_row, text="🎥  CAMERA TRỰC TIẾP",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")
        
        self.cam_status_dot = ctk.CTkLabel(
            title_row, text="● LIVE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CTK_SUCCESS, anchor="e",
        )
        self.cam_status_dot.pack(side="right")
        
        # Camera Feed Box
        self.camera_label = ctk.CTkLabel(
            cam_card, text="Đang khởi tạo camera...",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
            width=ADMIN_CAMERA_WIDTH, height=ADMIN_CAMERA_HEIGHT,
            fg_color="#000000", corner_radius=8,
        )
        self.camera_label.pack(padx=18, pady=(0, 8))
        
        # Live sub-status line
        self.cam_live_status = ctk.CTkLabel(
            cam_card, text="● ĐANG CHỜ KHUÔN MẶT",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CTK_SUCCESS, anchor="w"
        )
        self.cam_live_status.pack(fill="x", padx=18, pady=(0, 10))
        
        # Control buttons row
        cam_btn_frame = ctk.CTkFrame(cam_card, fg_color="transparent")
        cam_btn_frame.pack(fill="x", padx=18, pady=(0, 14))
        
        self.btn_toggle_cam = ctk.CTkButton(
            cam_btn_frame, text="⏸  Tạm dừng",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            fg_color=("#f1f5f9", "#080e1a"), hover_color=CTK_SIDEBAR_HOVER,
            border_width=1, border_color=CTK_ACCENT,
            text_color=CTK_TEXT,
            command=self._toggle_camera,
        )
        self.btn_toggle_cam.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        self.btn_restart_cam = ctk.CTkButton(
            cam_btn_frame, text="🔄  Khởi động lại",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            fg_color=("#f1f5f9", "#080e1a"), hover_color=CTK_SIDEBAR_HOVER,
            border_width=1, border_color=CTK_ACCENT,
            text_color=CTK_TEXT,
            command=self._restart_camera,
        )
        self.btn_restart_cam.pack(side="right", expand=True, fill="x", padx=(5, 0))
        
        # ==========================================
        # CARD 2: THÔNG TIN HỆ THỐNG
        # ==========================================
        sys_card = ctk.CTkFrame(
            right_container, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        sys_card.pack(fill="both", expand=True)
        
        # Header
        sys_header = ctk.CTkFrame(sys_card, fg_color="transparent")
        sys_header.pack(fill="x", padx=18, pady=(14, 8))
        
        ctk.CTkLabel(
            sys_header, text="⏳  THÔNG TIN HỆ THỐNG",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")
        
        # Helper: Tạo dòng thông tin hệ thống
        def create_telemetry_row(parent, icon, title, initial_val, is_highlight=False):
            row = ctk.CTkFrame(parent, fg_color="transparent", height=26)
            row.pack(fill="x", padx=18, pady=3)
            row.pack_propagate(False)
            
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left")
            ctk.CTkLabel(
                left, text=f"{icon}  {title}",
                font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w"
            ).pack(side="left")
            
            val_lbl = ctk.CTkLabel(
                row, text=initial_val,
                font=ctk.CTkFont(size=12, weight="bold" if is_highlight else "normal"),
                text_color=CTK_SUCCESS if is_highlight else CTK_TEXT,
                anchor="e"
            )
            val_lbl.pack(side="right")
            return val_lbl
            
        sys_rows = ctk.CTkFrame(sys_card, fg_color="transparent")
        sys_rows.pack(fill="x", pady=(0, 14))
        
        self.lbl_stat_model = create_telemetry_row(sys_rows, "🤖", "Model", f"YOLOv8 + {DEEPFACE_MODEL_NAME}")
        self.lbl_stat_conf = create_telemetry_row(sys_rows, "🎯", "Độ tin cậy (Face)", "0.00")
        self.lbl_stat_fps = create_telemetry_row(sys_rows, "⏱", "FPS", "30")
        self.lbl_stat_status = create_telemetry_row(sys_rows, "🩺", "Trạng thái", "● Hoạt động tốt", is_highlight=True)
        
        device_text = "NVIDIA GPU" if self.device in ['cuda', '0'] else "CPU"
        self.lbl_stat_device = create_telemetry_row(sys_rows, "🖥", "Thiết bị", device_text)

    

    # ============================================
    # DATABASE TAB (Người đăng ký)
    # ============================================
    def _build_database_page(self):
        """Xây dựng giao diện cho tab Người đăng ký (SaaS Data Card)."""
        # --- Top Header ---
        header = ctk.CTkFrame(self.database_frame, fg_color="transparent")
        header.pack(fill="x", padx=25, pady=(25, 15))
        
        # Left Title
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left")
        ctk.CTkLabel(
            title_frame, text="Người đăng ký",
            font=ctk.CTkFont(size=26, weight="bold"), text_color=CTK_PRIMARY, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_frame, text="Quản lý danh sách nhân viên đã đăng ký nhận diện khuôn mặt",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM, anchor="w"
        ).pack(anchor="w", pady=(2, 0))
        
        # Right Actions
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", fill="y", pady=5)
        
        self.search_entry = ctk.CTkEntry(
            btn_frame, placeholder_text="🔍 Tìm theo tên hoặc mã nhân viên...",
            width=280, height=38, corner_radius=8,
            border_color=CTK_ACCENT, fg_color=("#f8fafc", "#080e1a"),
            text_color=CTK_TEXT, placeholder_text_color=("#94a3b8", "#64748b")
        )
        self.search_entry.pack(side="left", padx=(0, 15))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)
        
        btn_add_new = ctk.CTkButton(
            btn_frame, text="+ Đăng ký mới",
            font=ctk.CTkFont(size=14, weight="bold"), height=38,
            corner_radius=8, fg_color=("#2563eb", "#2563eb"), hover_color=("#1d4ed8", "#1d4ed8"), text_color=("#ffffff", "#ffffff"),
            command=lambda: self._navigate("add_employee")
        )
        btn_add_new.pack(side="left")

        # --- Stats Cards Section ---
        stats_frame = ctk.CTkFrame(self.database_frame, fg_color="transparent")
        stats_frame.pack(fill="x", padx=25, pady=(0, 20))
        stats_frame.grid_columnconfigure((0,1,2,3), weight=1, uniform="card")
        
        # Helper to create card
        def create_stat_card(parent, col, icon, title, value, subtext, icon_color="#3b82f6"):
            card = ctk.CTkFrame(parent, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
            card.grid(row=0, column=col, sticky="nsew", padx=8 if col > 0 else (0, 8))
            
            icon_box = ctk.CTkFrame(card, width=45, height=45, corner_radius=10, fg_color=("#f1f5f9", "#101d30"))
            icon_box.pack(side="left", padx=15, pady=15)
            icon_box.pack_propagate(False)
            ctk.CTkLabel(icon_box, text=icon, font=ctk.CTkFont(size=20), text_color=icon_color).place(relx=0.5, rely=0.5, anchor="center")
            
            text_box = ctk.CTkFrame(card, fg_color="transparent")
            text_box.pack(side="left", fill="both", expand=True, pady=15)
            ctk.CTkLabel(text_box, text=title, font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x")
            val_label = ctk.CTkLabel(text_box, text=value, font=ctk.CTkFont(size=22, weight="bold"), text_color=CTK_TEXT, anchor="w")
            val_label.pack(fill="x", pady=(2, 2))
            ctk.CTkLabel(text_box, text=subtext, font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x")
            return val_label
            
        self.lbl_total_emp = create_stat_card(stats_frame, 0, "👥", "Tổng nhân viên", "0", "Dữ liệu thực tế", "#3b82f6")
        self.lbl_registered = create_stat_card(stats_frame, 1, "✅", "Đã đăng ký", "0", "Khuôn mặt đã lưu", "#10b981")
        self.lbl_pending = create_stat_card(stats_frame, 2, "⏱", "Chưa cập nhật", "0", "Chưa có ảnh khuôn mặt", "#f59e0b")
        self.lbl_new = create_stat_card(stats_frame, 3, "👤+", "Mới trong tháng", "0", "Đăng ký gần đây", "#6366f1")
            
        # --- Main Table Frame ---
        table_container = ctk.CTkFrame(self.database_frame, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
        table_container.pack(fill="both", expand=True, padx=25, pady=(0, 20))
        
        # Table Title
        ctk.CTkLabel(
            table_container, text="Danh sách nhân viên đã đăng ký",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=CTK_TEXT, anchor="w"
        ).pack(fill="x", padx=20, pady=(15, 10))
        
        # Scrollable Frame cho các dòng dữ liệu
        self.db_scroll = ctk.CTkScrollableFrame(table_container, fg_color="transparent")
        self.db_scroll.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Pagination Footer
        footer = ctk.CTkFrame(table_container, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(10, 15))
        
        # Left footer (Hiển thị)
        show_frame = ctk.CTkFrame(footer, fg_color="transparent")
        show_frame.pack(side="left")
        ctk.CTkLabel(show_frame, text="Hiển thị", text_color=CTK_TEXT_DIM, font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 10))
        dropdown = ctk.CTkOptionMenu(show_frame, values=["10", "20", "50"], width=60, height=28, fg_color=("#f1f5f9", "#111c2e"), button_color=CTK_ACCENT, text_color=CTK_TEXT)
        dropdown.pack(side="left")
        ctk.CTkLabel(show_frame, text="kết quả", text_color=CTK_TEXT_DIM, font=ctk.CTkFont(size=13)).pack(side="left", padx=(10, 0))
        
        # Right footer (Pagination buttons)
        page_frame = ctk.CTkFrame(footer, fg_color="transparent")
        page_frame.pack(side="right")
        for text in ["<", "1", "2", "3", "...", "13", ">"]:
            fg = "#2563eb" if text == "1" else "transparent"
            tc = ("#ffffff", "#ffffff") if text == "1" else CTK_TEXT_DIM
            btn = ctk.CTkButton(page_frame, text=text, width=30, height=30, fg_color=fg, text_color=tc, font=ctk.CTkFont(size=12, weight="bold"), hover_color=CTK_SIDEBAR_HOVER)
            btn.pack(side="left", padx=2)
        
        # Load Data
        self._load_database_to_scrollable()

    def _render_table_header(self):
        thead = ctk.CTkFrame(self.db_scroll, fg_color=("#f1f5f9", "#0b111e"), height=45, corner_radius=8, border_width=1, border_color=CTK_ACCENT)
        thead.pack(fill="x", padx=5, pady=(0, 6))
        
        headers = ["", "Ảnh", "Mã NV", "Họ và tên", "Chức vụ", "Trạng thái", "Hành động"]
        weights = [3, 5, 8, 24, 16, 14, 10]
        
        for i, w in enumerate(weights):
            thead.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        for i, text in enumerate(headers):
            if i == 0:
                chk = ctk.CTkCheckBox(thead, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color=CTK_ACCENT, fg_color="#2563EB")
                chk.grid(row=0, column=i, pady=10, padx=(10, 0), sticky="w")
            else:
                ctk.CTkLabel(
                    thead, text=text, font=ctk.CTkFont(size=13, weight="bold"),
                    text_color=CTK_TEXT_DIM
                ).grid(row=0, column=i, pady=10, sticky="w", padx=10)


    def _on_search_change(self, event=None):
        query = self.search_entry.get().lower()
        self._load_database_to_scrollable(query)

    def _load_database_to_scrollable(self, query=""):
        """Đọc file và hiển thị từng dòng."""
        # Clear cũ
        for widget in self.db_scroll.winfo_children():
            widget.destroy()
            
        # Vẽ Header đồng bộ trong db_scroll
        self._render_table_header()
            
        data_dir = Path(DATA_FACES_DIR)
        image_files = list(data_dir.glob("*.jpg")) if data_dir.exists() else []
        
        # Cập nhật số liệu thực tế cho Stats Cards
        total_files = len(image_files)
        if hasattr(self, 'lbl_total_emp'):
            self.lbl_total_emp.configure(text=str(total_files))
            self.lbl_registered.configure(text=str(total_files))
            self.lbl_pending.configure(text="0")
            self.lbl_new.configure(text=str(total_files))
        
        row_idx = 0
        for img_path in image_files:
            filename = img_path.stem
            import re
            
            emp_id = "Unknown"
            emp_role = "Nhân viên"
            emp_name = "Unknown"
            
            if "@" in filename:
                parts = filename.split("@")
                emp_id = parts[0]
                if len(parts) >= 3:
                    emp_role = parts[1].replace("_", " ")
                    emp_name = parts[2].replace("_", " ")
                elif len(parts) == 2:
                    emp_name = parts[1].replace("_", " ")
            else:
                parts = filename.split("_", 1)
                emp_id = parts[0] if len(parts) > 0 else "Unknown"
                emp_name = parts[1].replace("_", " ") if len(parts) > 1 else "Unknown"
                
            emp_name = re.sub(r'\s*\d{8}\s\d{6}.*$', '', emp_name).strip()
            
            if query and query not in emp_id.lower() and query not in emp_name.lower() and query not in emp_role.lower():
                continue
                
            emp_data = {
                "img_path": img_path,
                "id": emp_id,
                "name": emp_name,
                "role": emp_role,
                "status": "Đã đăng ký"
            }
            
            self._render_employee_row(emp_data, row_idx)
            row_idx += 1

    def _render_employee_row(self, emp_data, row_index):
        bg_color = ("#ffffff", "#0d1522") if row_index % 2 == 0 else ("#f8fafc", "#111c2e") 
        
        row_frame = ctk.CTkFrame(self.db_scroll, fg_color=bg_color, corner_radius=6)
        row_frame.pack(fill="x", pady=2, padx=5)
        
        weights = [3, 5, 8, 24, 16, 14, 10]
        for i, w in enumerate(weights):
            row_frame.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        # 0. Checkbox
        checkbox = ctk.CTkCheckBox(row_frame, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color=CTK_ACCENT, fg_color="#2563EB")
        checkbox.grid(row=0, column=0, pady=8, padx=(10, 0), sticky="w")
        
        # 1. Ảnh
        try:
            pil_img = Image.open(emp_data["img_path"]).resize((35, 35))
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(35, 35))
            img_label = ctk.CTkLabel(row_frame, image=ctk_img, text="")
            img_label.image = ctk_img
        except Exception:
            img_label = ctk.CTkLabel(row_frame, text="Lỗi", text_color=CTK_DANGER)
        img_label.grid(row=0, column=1, pady=8, padx=10, sticky="w")
        
        # 2. Mã NV
        ctk.CTkLabel(row_frame, text=emp_data["id"], font=ctk.CTkFont(size=13), text_color=CTK_TEXT).grid(row=0, column=2, sticky="w", padx=10)
        
        # 3. Tên
        ctk.CTkLabel(row_frame, text=emp_data["name"], font=ctk.CTkFont(size=14, weight="bold"), text_color=CTK_TEXT).grid(row=0, column=3, sticky="w", padx=10)
        
        # 4. Chức vụ
        ctk.CTkLabel(row_frame, text=emp_data["role"], font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM).grid(row=0, column=4, sticky="w", padx=10)
        
        # 5. Trạng thái (Badge)
        badge_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        badge_frame.grid(row=0, column=5, sticky="w", padx=10)
        
        status_text = emp_data["status"]
        if "Chưa đăng ký" in status_text:
            fg_col, text_col, icon = ("#fee2e2", "#7f1d1d"), ("#991b1b", "#fca5a5"), "❌ "
        elif "Chưa cập nhật" in status_text:
            fg_col, text_col, icon = ("#ffedd5", "#78350f"), ("#ea580c", "#fcd34d"), "⏱ "
        else:
            fg_col, text_col, icon = ("#dcfce7", "#064e3b"), ("#16a34a", "#6ee7b7"), "✅ "
            
        badge = ctk.CTkLabel(
            badge_frame, text=icon + status_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=text_col, fg_color=fg_col,
            corner_radius=15, padx=10, pady=4
        )
        badge.pack(side="left")
        
        # 6. Hành động (3 nút)
        action_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        action_frame.grid(row=0, column=6, sticky="w", padx=10)
        
        btn_view = ctk.CTkButton(action_frame, text="👁", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#3b82f6", "#60a5fa"), hover_color=CTK_SIDEBAR_HOVER)
        btn_view.pack(side="left", padx=2)
        
        btn_edit = ctk.CTkButton(action_frame, text="📝", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#6b7280", "#cbd5e1"), hover_color=CTK_SIDEBAR_HOVER)
        btn_edit.pack(side="left", padx=2)
        
        btn_del = ctk.CTkButton(
            action_frame, text="🗑", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#ef4444", "#fca5a5"), hover_color=("#fee2e2", "#7f1d1d"),
            command=lambda: self._delete_single_employee(emp_data["img_path"], row_frame)
        )
        btn_del.pack(side="left", padx=2)

    def _delete_single_employee(self, img_path, row_frame):
        import os
        try:
            if img_path.exists():
                os.remove(img_path)
                print(f"[DB] Đã xóa: {img_path}")
            # Tải lại danh sách để tự động cập nhật số liệu trên thẻ Stats
            self._load_database_to_scrollable(self.search_entry.get().lower())
        except Exception as e:
            print(f"[DB] Lỗi xóa file {img_path}: {e}")

    # ============================================
    # LỊCH SỬ ĐIỂM DANH (History Page)
    # ============================================
    def _build_history_page(self):
        """Trang xem toàn bộ lịch sử điểm danh nhân sự."""
        self.history_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        
        card = ctk.CTkFrame(self.history_frame, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
        card.pack(fill="both", expand=True, padx=4, pady=4)
        
        # Header
        h_row = ctk.CTkFrame(card, fg_color="transparent")
        h_row.pack(fill="x", padx=20, pady=(18, 12))
        
        ctk.CTkLabel(
            h_row, text="📋  Toàn bộ lịch sử điểm danh nhân sự",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=CTK_TEXT, anchor="w"
        ).pack(side="left")
        
        ctk.CTkButton(
            h_row, text="🔲 Mở Kiosk Điểm danh",
            font=ctk.CTkFont(size=12, weight="bold"), height=32, corner_radius=6,
            fg_color=("#2563eb", "#2563eb"), hover_color=("#1d4ed8", "#1d4ed8"),
            command=lambda: self._navigate("attendance")
        ).pack(side="right")
        
        # Table Header
        thead = ctk.CTkFrame(card, fg_color=("#f1f5f9", "#0b121e"), height=38, corner_radius=6)
        thead.pack(fill="x", padx=16, pady=(0, 6))
        thead.pack_propagate(False)
        thead.grid_columnconfigure(0, weight=2)
        thead.grid_columnconfigure(1, weight=2)
        thead.grid_columnconfigure(2, weight=1)
        thead.grid_columnconfigure(3, weight=2)
        thead.grid_columnconfigure(4, weight=2)
        thead.grid_columnconfigure(5, weight=2)
        
        for idx, col in enumerate(["Thời gian", "Nhân viên", "Mã NV", "Chức vụ", "Phòng ban", "Trạng thái"]):
            ctk.CTkLabel(
                thead, text=col, font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_TEXT_DIM, anchor="w"
            ).grid(row=0, column=idx, padx=12, pady=8, sticky="w")
            
        self.history_full_scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.history_full_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._reload_full_history()

    def _reload_full_history(self):
        """Tải lại danh sách lịch sử điểm danh đầy đủ."""
        if not hasattr(self, 'history_full_scroll'):
            return
        for w in self.history_full_scroll.winfo_children():
            w.destroy()
            
        for idx, rec in enumerate(self.attendance_history):
            bg_c = ("#ffffff", "#0e1828") if idx % 2 == 0 else ("#f8fafc", "#080f1b")
            row = ctk.CTkFrame(self.history_full_scroll, fg_color=bg_c, height=44, corner_radius=6)
            row.pack(fill="x", pady=2)
            row.pack_propagate(False)
            row.grid_columnconfigure(0, weight=2)
            row.grid_columnconfigure(1, weight=2)
            row.grid_columnconfigure(2, weight=1)
            row.grid_columnconfigure(3, weight=2)
            row.grid_columnconfigure(4, weight=2)
            row.grid_columnconfigure(5, weight=2)
            
            ctk.CTkLabel(row, text=rec.get("time", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").grid(row=0, column=0, padx=12, pady=10, sticky="w")
            ctk.CTkLabel(row, text=rec.get("name", ""), font=ctk.CTkFont(size=12, weight="bold"), text_color=CTK_TEXT, anchor="w").grid(row=0, column=1, padx=12, pady=10, sticky="w")
            ctk.CTkLabel(row, text=rec.get("id", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").grid(row=0, column=2, padx=12, pady=10, sticky="w")
            ctk.CTkLabel(row, text=rec.get("role", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").grid(row=0, column=3, padx=12, pady=10, sticky="w")
            ctk.CTkLabel(row, text=rec.get("dept", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").grid(row=0, column=4, padx=12, pady=10, sticky="w")
            
            badge = ctk.CTkLabel(
                row, text="● " + rec.get("status", "Thành công"),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_SUCCESS, fg_color=("#ecfdf5", "#064e3b"),
                corner_radius=12, width=100, height=24
            )
            badge.grid(row=0, column=5, padx=12, pady=10, sticky="w")

    # ============================================
    # TỔNG QUAN (Dashboard Page)
    # ============================================
    def _build_dashboard_page(self):
        """Trang tổng quan (Dashboard) hệ thống."""
        self.dashboard_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
        
        # Stats row
        stats_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="transparent")
        stats_frame.pack(fill="x", padx=4, pady=(4, 16))
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dash")
        
        def create_dash_card(col, icon, title, val, sub, color):
            c = ctk.CTkFrame(stats_frame, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
            c.grid(row=0, column=col, sticky="nsew", padx=6 if col > 0 else (0, 6))
            ibox = ctk.CTkFrame(c, width=44, height=44, corner_radius=10, fg_color=("#f1f5f9", "#111c2e"))
            ibox.pack(side="left", padx=14, pady=14)
            ibox.pack_propagate(False)
            ctk.CTkLabel(ibox, text=icon, font=ctk.CTkFont(size=20), text_color=color).place(relx=0.5, rely=0.5, anchor="center")
            tbox = ctk.CTkFrame(c, fg_color="transparent")
            tbox.pack(side="left", fill="both", expand=True, pady=14)
            ctk.CTkLabel(tbox, text=title, font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x")
            lbl_v = ctk.CTkLabel(tbox, text=val, font=ctk.CTkFont(size=20, weight="bold"), text_color=CTK_TEXT, anchor="w")
            lbl_v.pack(fill="x", pady=(2, 2))
            ctk.CTkLabel(tbox, text=sub, font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x")
            return lbl_v
            
        self.lbl_dash_kiosk = create_dash_card(0, "🔲", "Kiosk Điểm danh", "Hoạt động", "YOLOv8 + ArcFace", "#10b981")
        self.lbl_dash_users = create_dash_card(1, "👥", "Người đăng ký", "9 hồ sơ", "Đã số hóa khuôn mặt", "#3b82f6")
        self.lbl_dash_att = create_dash_card(2, "🕒", "Lượt điểm danh", f"{len(self.attendance_history)} lượt", "Trong phiên làm việc", "#f59e0b")
        self.lbl_dash_engine = create_dash_card(3, "⚡", "Ngưỡng AI", "0.68", "ArcFace Cosine Metric", "#8b5cf6")
        
        # Shortcut action card
        action_card = ctk.CTkFrame(self.dashboard_frame, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
        action_card.pack(fill="x", padx=4, pady=(0, 16))
        
        ctk.CTkLabel(
            action_card, text="Lối tắt thao tác nhanh",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=CTK_TEXT, anchor="w"
        ).pack(fill="x", padx=20, pady=(16, 12))
        
        btn_row = ctk.CTkFrame(action_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 20))
        
        ctk.CTkButton(
            btn_row, text="🔲  Vào Kiosk Điểm danh",
            font=ctk.CTkFont(size=13, weight="bold"), height=42, corner_radius=8,
            fg_color=("#2563eb", "#2563eb"), hover_color=("#1d4ed8", "#1d4ed8"),
            command=lambda: self._navigate("attendance")
        ).pack(side="left", padx=(0, 12))
        
        ctk.CTkButton(
            btn_row, text="➕  Đăng ký mới",
            font=ctk.CTkFont(size=13, weight="bold"), height=42, corner_radius=8,
            fg_color=("#10b981", "#059669"), hover_color=("#047857", "#047857"),
            command=lambda: self._navigate("add_employee")
        ).pack(side="left", padx=(0, 12))
        
        ctk.CTkButton(
            btn_row, text="👥  Quản lý Người đăng ký",
            font=ctk.CTkFont(size=13, weight="bold"), height=42, corner_radius=8,
            fg_color=("#f1f5f9", "#111c2e"), hover_color=CTK_SIDEBAR_HOVER,
            text_color=CTK_TEXT, border_width=1, border_color=CTK_ACCENT,
            command=lambda: self._navigate("database")
        ).pack(side="left")

    def _reload_dashboard_stats(self):
        """Cập nhật lại số liệu trên Dashboard."""
        if hasattr(self, 'lbl_dash_att'):
            self.lbl_dash_att.configure(text=f"{len(self.attendance_history)} lượt")
        try:
            total_db = len(list(Path(DATA_FACES_DIR).glob("*.jpg")))
            if hasattr(self, 'lbl_dash_users'):
                self.lbl_dash_users.configure(text=f"{total_db} hồ sơ")
        except Exception:
            pass

    # ============================================
    # CAMERA + AI PIPELINE
    # ============================================
    def _start_camera(self):
        """Khởi tạo camera + tính layout eKYC."""
        if self.camera_cap is not None:
            self.camera_cap.release()
        
        for cam_id in [1, 0]:
            cap = cv2.VideoCapture(cam_id)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    self.camera_cap = cap
                    self.camera_running = True
                    self.frame_height, self.frame_width = frame.shape[:2]
                    
                    # Tính layout + tạo mask (1 lần)
                    self.shape_rect, corner_radius = compute_ekyc_layout(
                        self.frame_width, self.frame_height)
                    self.constraint_box = self.shape_rect
                    self.ekyc_mask = create_rounded_rect_mask(
                        self.frame_width, self.frame_height,
                        self.shape_rect, corner_radius)
                    
                    if hasattr(self, 'cam_status_dot'):
                        self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
                    if hasattr(self, 'lbl_stat_status'):
                        self.lbl_stat_status.configure(text="● Hoạt động tốt", text_color=CTK_SUCCESS)
                    if hasattr(self, 'cam_live_status'):
                        self.cam_live_status.configure(text="● ĐANG CHỜ KHUÔN MẶT", text_color=CTK_SUCCESS)
                    print(f"[CAMERA] Đã kết nối camera ID={cam_id} ({self.frame_width}x{self.frame_height})")
                    
                    if self.current_page == "attendance":
                        self.kiosk_running = True
                        self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                        self.kiosk_thread.start()
                        self._update_kiosk_frame()
                    else:
                        self.enrollment_running = True
                        self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                        self.camera_thread.start()
                        self._update_frame()
                    return
                else:
                    cap.release()
        
        if hasattr(self, 'cam_status_dot'):
            self.cam_status_dot.configure(text="● OFFLINE", text_color=CTK_DANGER)
        if hasattr(self, 'lbl_stat_status'):
            self.lbl_stat_status.configure(text="● Mất kết nối", text_color=CTK_DANGER)
        if hasattr(self, 'cam_live_status'):
            self.cam_live_status.configure(text="● KHÔNG CÓ TÍN HIỆU CAMERA", text_color=CTK_DANGER)
        if hasattr(self, 'camera_label'):
            self.camera_label.configure(text="Không thể kết nối camera.\nKiểm tra thiết bị.")
        print("[CAMERA] KHÔNG TÌM THẤY CAMERA!")
    

    # ============================================
    # KIOSK MODE CAMERA LOOP & ARCFACE AI PIPELINE
    # ============================================
    def _kiosk_camera_loop(self):
        """
        Vòng lặp Camera riêng cho Kiosk (Tab 'Nhận diện điểm danh'):
        - Dùng YOLOv8 để detect khuôn mặt.
        - CHỈ vẽ khung bounding box (hình vuông / corner brackets) bám theo khuôn mặt, KHÔNG dùng mask che tối màn hình.
        - Inference Trigger: Nếu width > 100px và not self.is_recognizing:
          Crop khuôn mặt và kích hoạt luồng ngầm (_ai_worker_recognize) chạy DeepFace ArcFace.
        """
        prev_time = time.time()
        smoothed_fps = 0.0
        
        while self.kiosk_running and self.camera_running:
            if self.camera_cap is None:
                time.sleep(0.05)
                continue
                
            ret, frame = self.camera_cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue
                
            frame = cv2.flip(frame, 1)
            clean_frame = frame.copy()
            fh, fw = frame.shape[:2]
            
            # 1. Detect khuôn mặt bằng YOLOv8
            faces = detect_faces(self.face_model, frame, self.device)
            self.kiosk_face_count = len(faces)
            
            # 2. CHỈ vẽ khung bounding box bám theo khuôn mặt, KHÔNG dùng mask che tối
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                draw_kiosk_face_box(frame, fx1, fy1, fx2, fy2, color=(100, 255, 100), thickness=3)
                
            # 3. Cơ chế kích hoạt Nhận diện (Inference Trigger):
            if len(faces) > 0 and not self.is_recognizing:
                best_face = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
                fx1, fy1, fx2, fy2, fconf = best_face
                face_w = fx2 - fx1
                face_h = fy2 - fy1
                
                # Điều kiện kích thước khung mặt đủ lớn (width >= 100px)
                if face_w >= KIOSK_MIN_FACE_WIDTH:
                    pad_x = int(face_w * 0.15)
                    pad_y = int(face_h * 0.15)
                    cx1 = max(0, fx1 - pad_x)
                    cy1 = max(0, fy1 - pad_y)
                    cx2 = min(fw, fx2 + pad_x)
                    cy2 = min(fh, fy2 + pad_y)
                    
                    if cx2 > cx1 and cy2 > cy1:
                        cropped_img = clean_frame[cy1:cy2, cx1:cx2].copy()
                        # Đặt cờ self.is_recognizing = True để bỏ qua các frame tiếp theo
                        self.is_recognizing = True
                        
                        self._safe_after(0, self._on_kiosk_recognizing_started)
                        
                        # Đẩy vào luồng ngầm (Background Thread)
                        threading.Thread(
                            target=self._ai_worker_recognize,
                            args=(cropped_img,),
                            daemon=True
                        ).start()
            
            # 4. Tính toán và làm mượt FPS
            curr_time = time.time()
            instant_fps = 1.0 / (curr_time - prev_time) if curr_time > prev_time else 0
            prev_time = curr_time
            smoothed_fps = instant_fps if smoothed_fps == 0.0 else (0.85 * smoothed_fps + 0.15 * instant_fps)
            self.kiosk_fps = smoothed_fps
            
            # 5. Lưu frame cho Main UI Thread
            self.kiosk_latest_frame = frame
            time.sleep(0.01)

    def _safe_after(self, delay_ms, func, *args):
        """Gọi after một cách an toàn giữa các luồng (Thread-safe)."""
        try:
            if hasattr(self, 'tk') and self.winfo_exists():
                return self.after(delay_ms, func, *args)
        except Exception:
            pass
        return None

    def _update_kiosk_frame(self):
        """Main UI Thread: Lấy frame từ luồng Kiosk và vẽ lên widget camera (Chuẩn tỉ lệ, không méo)."""
        if not self.kiosk_running or self.current_page != "attendance":
            return
        if not self.winfo_exists():
            return
            
        if self.kiosk_latest_frame is not None:
            output = self.kiosk_latest_frame
            w = self.kiosk_camera_label.winfo_width()
            h = self.kiosk_camera_label.winfo_height()
            if w <= 10 or h <= 10:
                w, h = 480, 315
                
            # Hiển thị trọn vẹn toàn bộ khung hình camera (Không cắt xén, không zoom to mặt)
            # Giữ tỉ lệ khuôn mặt tự nhiên 100% đúng chuẩn thực tế
            fh, fw = output.shape[:2]
            scale = min(w / max(1, fw), h / max(1, fh))
            new_w = max(1, int(fw * scale))
            new_h = max(1, int(fh * scale))
            
            resized_video = cv2.resize(output, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            
            # Đặt video ở chính giữa nền đen tràn khít box hiển thị
            canvas = np.zeros((h, w, 3), dtype=np.uint8)
            start_x = (w - new_w) // 2
            start_y = (h - new_h) // 2
            canvas[start_y:start_y + new_h, start_x:start_x + new_w] = resized_video
            
            frame_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(frame_rgb)
            
            ctk_image = ctk.CTkImage(
                light_image=pil_image, dark_image=pil_image,
                size=(w, h),
            )
            self.kiosk_camera_label.configure(image=ctk_image, text="")
            self.kiosk_camera_label.image = ctk_image
            
        if hasattr(self, 'lbl_kiosk_fps'):
            self.lbl_kiosk_fps.configure(text=f"FPS: {int(self.kiosk_fps)}")
            
        self._safe_after(ADMIN_CAMERA_FPS_DELAY, self._update_kiosk_frame)

    def _toggle_kiosk_camera(self):
        """Tạm dừng / Tiếp tục quét trong Kiosk mode."""
        if self.kiosk_running:
            self.kiosk_running = False
            if hasattr(self, 'btn_kiosk_pause'):
                self.btn_kiosk_pause.configure(text="▶  Tiếp tục quét")
            if hasattr(self, 'lbl_kiosk_cam_status'):
                self.lbl_kiosk_cam_status.configure(text="● Tạm dừng", text_color=CTK_WARNING)
        else:
            self.kiosk_running = True
            if hasattr(self, 'btn_kiosk_pause'):
                self.btn_kiosk_pause.configure(text="⏸  Dừng quét")
            if hasattr(self, 'lbl_kiosk_cam_status'):
                self.lbl_kiosk_cam_status.configure(text="● Camera đang hoạt động", text_color=CTK_SUCCESS)
            if self.kiosk_thread is None or not self.kiosk_thread.is_alive():
                self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                self.kiosk_thread.start()
            self._update_kiosk_frame()

    def _on_kiosk_recognizing_started(self):
        """Cập nhật trạng thái khi AI bắt đầu nhận diện."""
        self.kiosk_status_title.configure(text="Đang nhận diện AI (ArcFace)...")
        self.kiosk_status_desc.configure(text="Đang đối chiếu dữ liệu khuôn mặt nhân sự...")

    def _ai_worker_recognize(self, cropped_img):
        """
        Luồng xử lý ngầm (_ai_worker_recognize):
        - Nhận ảnh crop từ YOLO, chạy DeepFace.represent(img_path=cropped_img, model_name='ArcFace', enforce_detection=False).
        - Duyệt qua file embeddings.pkl, tính khoảng cách Cosine với toàn bộ nhân viên.
        - Tìm ra người có khoảng cách NHỎ NHẤT. Nếu khoảng cách min < 0.68 -> Nhận diện thành công. Nếu > 0.68 -> Người lạ.
        - Dùng self.after để gọi hàm cập nhật UI.
        """
        try:
            from deepface import DeepFace
            
            # 1. Trích xuất Vector khuôn mặt qua ArcFace
            reps = DeepFace.represent(
                img_path=cropped_img,
                model_name=DEEPFACE_MODEL_NAME, # "ArcFace"
                detector_backend="skip",
                enforce_detection=False
            )
            
            if not reps or len(reps) == 0:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return
                
            query_vector = reps[0]["embedding"]
            
            # 2. Đọc cơ sở dữ liệu vector từ RAM cache (siêu tốc, không tốn thời gian đọc đĩa)
            if self.embeddings_cache is None or len(self.embeddings_cache) == 0:
                self._reload_embeddings_cache()
            embeddings_data = self.embeddings_cache
            
            if not embeddings_data:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return
                
            # 3. Duyệt qua file embeddings.pkl, tính khoảng cách Cosine với toàn bộ nhân viên
            best_emp = None
            min_dist = 999.0
            
            for emp_id, emp_info in embeddings_data.items():
                emp_emb = emp_info.get("embedding")
                if emp_emb is not None:
                    dist = calculate_cosine_distance(query_vector, emp_emb)
                    if dist < min_dist:
                        min_dist = dist
                        best_emp = emp_info
                        
            print(f"[KIOSK AI] Min Cosine Distance: {min_dist:.4f} (Threshold: {ARCFACE_THRESHOLD}) | Match: {best_emp.get('name') if best_emp else 'Không có'}")
            
            # 4. Tìm ra người có khoảng cách NHỎ NHẤT: min < 0.68 -> Thành công. Ngược lại -> Người lạ.
            if best_emp is not None and min_dist < ARCFACE_THRESHOLD:
                self._safe_after(0, self._on_kiosk_recognition_success, best_emp, min_dist)
            else:
                self._safe_after(0, self._on_kiosk_recognition_failed, min_dist)
                
        except Exception as e:
            print(f"[KIOSK AI ERROR]: Lỗi nhận diện: {e}")
            self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)

    def _on_kiosk_recognition_success(self, emp_info, distance):
        """
        Cập nhật Giao diện Cột Phải khi THÀNH CÔNG:
        - Đổi ảnh Avatar sang ảnh của nhân viên đó (load từ database/images/).
        - Cập nhật text Họ Tên, Mã NV, Chức vụ.
        - Đổi Badge trạng thái thành màu Xanh lá với text '✅ Nhận diện thành công'.
        - Sau 3 giây, tự động reset thông tin Cột Phải về trạng thái chờ mặc định và bật lại cờ self.is_recognizing = False.
        """
        name = emp_info.get("name", "Nhân viên")
        emp_id = emp_info.get("id", "NV---")
        role_full = emp_info.get("role", "Nhân viên")
        
        parts = role_full.split("-") if "-" in role_full else role_full.split("–")
        role_name = parts[0].strip()
        dept_name = parts[1].strip() if len(parts) > 1 else "Phòng IT"
        
        now = datetime.now()
        now_str = now.strftime("%H:%M:%S - %d/%m/%Y")
        now_short = now.strftime("%H:%M:%S %d/%m/%Y")
        
        # 1. Đổi Badge trạng thái thành màu Xanh lá với text '✅ Nhận diện thành công'
        self.kiosk_res_banner_frame.configure(fg_color=("#ecfdf5", "#042c16"), border_color=("#a7f3d0", "#059669"))
        self.kiosk_res_icon.configure(text="✓", fg_color=("#10b981", "#10b981"), text_color="#ffffff")
        self.kiosk_res_title.configure(text="Nhận diện thành công", text_color=("#15803d", "#4ade80"))
        self.kiosk_res_sub.configure(text="Khuôn mặt đã được xác thực", text_color=("#166534", "#86efac"))
        
        # 2. Cập nhật text Họ Tên, Mã NV, Chức vụ, Phòng ban
        self.kiosk_name_label.configure(text=name, text_color=CTK_TEXT)
        self.kiosk_id_badge.configure(text=f"Mã NV: {emp_id}", fg_color=("#dcfce7", "#064e3b"), text_color=("#15803d", "#86efac"))
        self.kiosk_role_label.configure(text=f"👤 Chức vụ: {role_name}")
        self.kiosk_dept_label.configure(text=f"🏢 Phòng ban: {dept_name}")
        
        # 3. Đổi ảnh Avatar sang ảnh của nhân viên đó (load từ database/images/)
        self._set_kiosk_avatar(emp_info.get("image_path"), emp_id, border_color="#10b981")
        
        # 4. Hộp chúc mừng
        self.kiosk_greeting_card.configure(fg_color=("#f0fdf4", "#052e16"), border_color=("#bbf7d0", "#14532d"))
        self.kiosk_greeting_text.configure(
            text=f"Chào mừng {name} đã điểm danh thành công!\nChúc bạn 1 ngày làm việc vui vẻ!",
            text_color=("#166534", "#bbf7d0")
        )
        
        # 5. Cập nhật thời gian điểm danh
        self.kiosk_time_label.configure(text=f"🕒 Thời gian điểm danh: {now_str}")
        self.kiosk_saved_badge.configure(text="● Đã lưu", fg_color=("#dcfce7", "#064e3b"), text_color=("#15803d", "#86efac"))
        
        # 6. Cập nhật banner dưới Camera
        self.kiosk_status_title.configure(text=f"Đã xác nhận: {name} ({emp_id})")
        self.kiosk_status_desc.configure(text=f"Điểm danh tự động thành công (Sai số: {distance:.2f})")
        
        # 7. Thêm vào bảng lịch sử điểm danh gần đây
        self._add_attendance_record({
            "time": now_short,
            "name": name,
            "id": emp_id,
            "role": role_name,
            "dept": dept_name,
            "status": "Thành công"
        })
        
        # 8. Hiển thị nút bấm "Quét người tiếp theo" (Thủ công, giữ cờ is_recognizing=True để chống freeze UI)
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack(fill="x", padx=14, pady=(2, 8))

    def _on_kiosk_recognition_failed(self, distance):
        """
        Cập nhật Giao diện Cột Phải khi THẤT BẠI:
        - Ảnh giữ nguyên/xám.
        - Đổi Badge sang màu Đỏ với text '❌ Người lạ / Chưa đăng ký'.
        - Hiển thị nút "Quét người tiếp theo" để người dùng chủ động bấm quét tiếp.
        """
        now = datetime.now()
        now_str = now.strftime("%H:%M:%S - %d/%m/%Y")
        
        # 1. Đổi Badge sang màu Đỏ với text '❌ Người lạ / Chưa đăng ký'
        self.kiosk_res_banner_frame.configure(fg_color=("#fef2f2", "#450a0a"), border_color=("#fca5a5", "#991b1b"))
        self.kiosk_res_icon.configure(text="✕", fg_color=("#ef4444", "#ef4444"), text_color="#ffffff")
        self.kiosk_res_title.configure(text="❌ Người lạ / Chưa đăng ký", text_color=("#b91c1c", "#f87171"))
        self.kiosk_res_sub.configure(text="Khuôn mặt chưa được xác thực trong hệ thống", text_color=("#991b1b", "#fca5a5"))
        
        # 2. Cập nhật thông tin Người lạ
        self.kiosk_name_label.configure(text="Người lạ / Khách", text_color=("#b91c1c", "#f87171"))
        self.kiosk_id_badge.configure(text="Mã NV: ---", fg_color=("#fee2e2", "#7f1d1d"), text_color=("#991b1b", "#fca5a5"))
        self.kiosk_role_label.configure(text="👤 Chức vụ: Chưa đăng ký")
        self.kiosk_dept_label.configure(text="🏢 Phòng ban: Vui lòng liên hệ quản trị viên")
        
        # 3. Ảnh giữ nguyên / xám
        self._set_kiosk_avatar(None, None, border_color="#ef4444")
        
        # 4. Hộp cảnh báo
        self.kiosk_greeting_card.configure(fg_color=("#fff7ed", "#431407"), border_color=("#fed7aa", "#7c2d12"))
        self.kiosk_greeting_text.configure(
            text="⚠️ Cảnh báo: Khuôn mặt không khớp dữ liệu nhân sự!\nVui lòng đăng ký trước khi điểm danh.",
            text_color=("#9a3412", "#fdba74")
        )
        
        # 5. Thời gian
        self.kiosk_time_label.configure(text=f"🕒 Thời gian phát hiện: {now_str}")
        self.kiosk_saved_badge.configure(text="● Từ chối", fg_color=("#fee2e2", "#7f1d1d"), text_color=("#b91c1c", "#fca5a5"))
        
        # 6. Banner dưới camera
        self.kiosk_status_title.configure(text="Phát hiện người lạ")
        self.kiosk_status_desc.configure(text="Khuôn mặt chưa được đăng ký trong hệ thống")
        
        # 7. Hiển thị nút bấm "Quét người tiếp theo" (Thủ công, giữ cờ is_recognizing=True để chống freeze UI)
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack(fill="x", padx=14, pady=(2, 8))

    def _reset_for_next_scan(self):
        """
        Nút bấm thủ công "🔄 Quét người tiếp theo":
        1. Ẩn chính nút bấm đó đi.
        2. Dọn dẹp avatar, đưa text về lại trạng thái chờ "Đang chờ quét...".
        3. Quan trọng nhất: Mở khóa cờ self.is_recognizing = False để vòng lặp Camera nhận diện người mới.
        """
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
            
        self.set_idle_state()

    def set_idle_state(self):
        """Dọn dẹp avatar, đưa text về lại trạng thái chờ mặc định và mở khóa nhận diện khuôn mặt mới."""
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
            
        self.kiosk_res_banner_frame.configure(fg_color=("#f8fafc", "#0b1322"), border_color=CTK_ACCENT)
        self.kiosk_res_icon.configure(text="🔍", fg_color=("#e2e8f0", "#172338"), text_color=("#2563eb", "#38bdf8"))
        self.kiosk_res_title.configure(text="Đang chờ nhận diện...", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(text="Sẵn sàng quét khuôn mặt tự động", text_color=CTK_TEXT_DIM)
        
        self.kiosk_name_label.configure(text="Chưa có dữ liệu", text_color=CTK_TEXT)
        self.kiosk_id_badge.configure(text="Mã NV: ---", fg_color=("#f1f5f9", "#1e293b"), text_color=CTK_TEXT_DIM)
        self.kiosk_role_label.configure(text="👤 Chức vụ: ---")
        self.kiosk_dept_label.configure(text="🏢 Phòng ban: ---")
        
        self._set_kiosk_avatar(None, None, border_color="#cbd5e1")
        
        self.kiosk_greeting_card.configure(fg_color=("#f0fdf4", "#052312"), border_color=("#bbf7d0", "#0c4a25"))
        self.kiosk_greeting_text.configure(
            text="Vui lòng đứng thẳng, nhìn vào camera để điểm danh.\nChúc bạn một ngày làm việc hiệu quả!",
            text_color=("#166534", "#86efac")
        )
        
        self.kiosk_time_label.configure(text="🕒 Trạng thái: Chờ quét")
        self.kiosk_saved_badge.configure(text="● Sẵn sàng", fg_color=("#f1f5f9", "#1e293b"), text_color=CTK_TEXT_DIM)
        
        self.kiosk_status_title.configure(text="Đang quét khuôn mặt...")
        self.kiosk_status_desc.configure(text="Vui lòng đứng thẳng, nhìn vào camera")
        
        # QUAN TRỌNG NHẤT: BẬT LẠI CỜ ĐỂ KIOSK QUÉT NGƯỜI TIẾP THEO
        self.is_recognizing = False
        print("[KIOSK] Đã mở khóa nhận diện (self.is_recognizing = False) - Sẵn sàng quét người tiếp theo.")

    def _reset_kiosk_ui(self):
        """Hỗ trợ tương thích ngược cho set_idle_state."""
        self.set_idle_state()

    def _set_kiosk_avatar(self, img_path, emp_id=None, border_color="#10b981", size=(74, 74)):
        """Tải và hiển thị ảnh đại diện tròn của nhân viên (kích thước nhỏ gọn 74x74)."""
        pil_img = None
        
        if img_path:
            try:
                p = Path(img_path)
                if p.exists():
                    img_bytes = np.fromfile(str(p), dtype=np.uint8)
                    cv_img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
                    if cv_img is not None:
                        pil_img = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
            except Exception:
                pil_img = None
                
        if pil_img is None and emp_id:
            for folder in ["database/images", "images", "data/faces"]:
                matches = list(Path(folder).glob(f"{emp_id}*"))
                if matches:
                    try:
                        img_bytes = np.fromfile(str(matches[0]), dtype=np.uint8)
                        cv_img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
                        if cv_img is not None:
                            pil_img = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
                            break
                    except Exception:
                        pass
                        
        if pil_img is not None:
            circ_img = make_circular_avatar(pil_img, size=size, border_color=border_color, border_width=3)
        else:
            circ_img = create_default_avatar(size=size, bg_color="#e2e8f0", border_color=border_color)
            
        ctk_img = ctk.CTkImage(light_image=circ_img, dark_image=circ_img, size=size)
        self.kiosk_avatar_label.configure(image=ctk_img, text="")
        self.kiosk_avatar_label.image = ctk_img

    # ============================================
    # ENROLLMENT CAMERA LOOP (Quét khuôn mặt nhân viên mới)
    # ============================================
    def _camera_loop(self):
        """
        Background Thread: AI Pipeline Enrollment
        Chuyên đọc frame, chạy AI và render HUD để không block UI.
        """
        prev_time = time.time()
        smoothed_fps = 0.0
        
        # Biến State Machine chống nhiễu (Debounce)
        green_streak = 0
        red_streak = 0
        display_color = (50, 50, 255)  # Mặc định Đỏ
        display_text = "Dua mat vao khung hinh"
        display_locked = False
        
        while self.enrollment_running and self.camera_running:
            if self.camera_cap is None:
                break
                
            ret, frame = self.camera_cap.read()
            if not ret:
                time.sleep(0.1)
                continue
                
            frame = cv2.flip(frame, 1)
            self.current_frame = frame.copy()  # Bản sạch để chụp
            
            fw, fh = self.frame_width, self.frame_height
            
            # --- AI Inference ---
            faces = detect_faces(self.face_model, frame, self.device)
            
            mp_results = None
            if self.face_detector is not None and len(faces) > 0:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                mp_results = self.face_detector.detect(mp_image)
            
            # --- Kiểm tra ràng buộc ---
            current_color, status_text, is_locked = check_face_constraints(
                faces, self.constraint_box, mp_results, fw, fh
            )
            
            # --- State Machine Chống nhiễu (Debouncer) ---
            if is_locked:
                green_streak += 1
                red_streak = 0
            else:
                red_streak += 1
                green_streak = 0
                
            if green_streak >= 5:       # Phải ổn định 5 frames mới cho Xanh (Hợp lệ)
                display_locked = True
                display_color = current_color
                display_text = status_text
            elif red_streak >= 2:       # Chỉ cần 2 frames là báo Đỏ ngay (Lỗi)
                display_locked = False
                display_color = current_color
                display_text = status_text
            
            # Cập nhật biến trạng thái (Atomic)
            self.is_face_valid = display_locked
            self.ai_status_text = display_text
            
            # Lưu telemetry
            highest_conf = 0.0
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                if fconf > highest_conf:
                    highest_conf = fconf
            self.current_fps = smoothed_fps
            self.current_conf = highest_conf
            
            # Log terminal
            if display_text != self.prev_status:
                icon = "✅ XANH" if display_locked else "🔴 ĐỎ"
                print(f"[{icon}] {display_text}")
                self.prev_status = display_text
                
            # --- Vẽ Bounding Box & Confidence (Dành cho Debug) ---
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                cv2.rectangle(frame, (fx1, fy1), (fx2, fy2), (255, 150, 0), 2)
                cv2.putText(frame, f"YOLO: {fconf:.2f}", (fx1, fy1 - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 150, 0), 2)
                            
            if mp_results and mp_results.detections:
                for det in mp_results.detections:
                    mp_score = det.categories[0].score
                    bbox = det.bounding_box
                    mx = int(bbox.origin_x)
                    my = int(bbox.origin_y)
                    cv2.putText(frame, f"MP: {mp_score:.2f}", (mx, my - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            # --- Render HUD ---
            if self.ekyc_mask is not None:
                output = render_full_hud(
                    frame, self.ekyc_mask, self.shape_rect,
                    display_color, display_text, fw, fh
                )
            else:
                output = frame
                
            # --- Tính và vẽ FPS ---
            curr_time = time.time()
            instant_fps = 1.0 / (curr_time - prev_time) if curr_time > prev_time else 0
            prev_time = curr_time
            
            if smoothed_fps == 0.0:
                smoothed_fps = instant_fps
            else:
                smoothed_fps = 0.9 * smoothed_fps + 0.1 * instant_fps
                
            cv2.putText(output, f"FPS: {int(smoothed_fps)}", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2, cv2.LINE_AA)
                
            self.latest_processed_frame = output
            time.sleep(0.001)

    def _update_frame(self):
        """
        Main UI Thread: Lấy frame đã xử lý và vẽ lên màn hình.
        """
        if not self.camera_running or self.current_page != "add_employee":
            return
            
        if self.latest_processed_frame is not None:
            output = self.latest_processed_frame
            
            # --- Convert → Tkinter Image ---
            output_resized = cv2.resize(output, (ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT), interpolation=cv2.INTER_LINEAR)
            frame_rgb = cv2.cvtColor(output_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(frame_rgb)
            
            ctk_image = ctk.CTkImage(
                light_image=pil_image, dark_image=pil_image,
                size=(ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT),
            )
            
            self.camera_label.configure(image=ctk_image, text="")
            self.photo_image = ctk_image
        
        # --- Cập nhật Telemetry Metrics ---
        if hasattr(self, 'lbl_stat_fps'):
            self.lbl_stat_fps.configure(text=f"{int(getattr(self, 'current_fps', 0))}")
            
        if hasattr(self, 'lbl_stat_conf'):
            c_val = getattr(self, 'current_conf', 0.0)
            self.lbl_stat_conf.configure(text=f"{c_val:.2f}" if c_val > 0 else "0.00")
            
        if hasattr(self, 'cam_live_status'):
            if self.is_face_valid:
                self.cam_live_status.configure(
                    text="● KHUÔN MẶT ĐƯỢC NHẬN DIỆN",
                    text_color=CTK_SUCCESS
                )
            elif getattr(self, 'current_conf', 0.0) > 0:
                self.cam_live_status.configure(
                    text=f"● {self.ai_status_text.upper()}",
                    text_color=CTK_WARNING
                )
            else:
                self.cam_live_status.configure(
                    text="● CHƯA PHÁT HIỆN KHUÔN MẶT",
                    text_color=CTK_DANGER
                )
        
        self.after(ADMIN_CAMERA_FPS_DELAY, self._update_frame)
    

    def _toggle_camera(self):
        """Tạm dừng / tiếp tục camera."""
        if self.camera_running:
            self.camera_running = False
            self.btn_toggle_cam.configure(text="▶  Tiếp tục")
            self.cam_status_dot.configure(text="● PAUSED", text_color=CTK_WARNING)
            if hasattr(self, 'lbl_stat_status'):
                self.lbl_stat_status.configure(text="● Tạm dừng", text_color=CTK_WARNING)
            if hasattr(self, 'cam_live_status'):
                self.cam_live_status.configure(text="● TẠM DỪNG CAMERA", text_color=CTK_WARNING)
        else:
            self.camera_running = True
            self.btn_toggle_cam.configure(text="⏸  Tạm dừng")
            self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
            if hasattr(self, 'lbl_stat_status'):
                self.lbl_stat_status.configure(text="● Hoạt động tốt", text_color=CTK_SUCCESS)
            
            if self.camera_thread is None or not self.camera_thread.is_alive():
                self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                self.camera_thread.start()
                
            self._update_frame()
    

    def _restart_camera(self):
        """Khởi động lại camera."""
        self.camera_running = False
        if self.camera_cap is not None:
            self.camera_cap.release()
            self.camera_cap = None
        self.camera_label.configure(image=None, text="Đang khởi động lại camera...")
        self.cam_status_dot.configure(text="● RESTARTING", text_color=CTK_WARNING)
        if hasattr(self, 'lbl_stat_status'):
            self.lbl_stat_status.configure(text="● Đang khởi động...", text_color=CTK_WARNING)
        if hasattr(self, 'cam_live_status'):
            self.cam_live_status.configure(text="● ĐANG KẾT NỐI LẠI...", text_color=CTK_WARNING)
        self.after(500, self._start_camera)

    

    # ============================================
    # CHỤP ẢNH & LƯU DỮ LIỆU (1-CLICK ENROLLMENT)
    # ============================================
    def _start_enrollment_process(self):
        """
        Quy trình 1-click Quét và Lưu khuôn mặt (Main Thread):
        1. Kiểm tra Họ tên và Mã NV hợp lệ.
        2. Kiểm tra trạng thái AI (self.is_face_valid == True).
        3. Cắt (crop) khuôn mặt tại frame hiện tại và hiển thị preview ngay.
        4. Kích hoạt hiệu ứng loading (thanh tiến trình indeterminate, disable nút).
        5. Đẩy việc trích xuất Vector AI (DeepFace) và lưu trữ sang Background Thread.
        """
        # --- ANTI-SPAM LOGIC ---
        if not hasattr(self, 'spam_count'):
            self.spam_count = 0
            self.last_capture_time = 0
            self.is_capture_locked = False
            
        if self.is_capture_locked:
            return
            
        current_time = time.time()
        # Nếu khoảng cách bấm > 3 giây, reset bộ đếm spam
        if current_time - self.last_capture_time > 3.0:
            self.spam_count = 0
            
        self.spam_count += 1
        self.last_capture_time = current_time
        
        if self.spam_count >= 5:
            self.is_capture_locked = True
            self.btn_capture.configure(state="disabled")
            self._set_status("⏳ CẢNH BÁO: Bấm quá nhanh! Nút quét khóa 5 giây.", CTK_WARNING)
            
            def unlock_capture():
                self.is_capture_locked = False
                self.spam_count = 0
                self.btn_capture.configure(state="normal")
                self._set_status("✅ Đã mở khóa nút quét. Bạn có thể tiếp tục.", CTK_SUCCESS)
                
            self.after(5000, unlock_capture)
            return
        # --- END ANTI-SPAM ---

        # 1. Kiểm tra thông tin Form
        name = self.entry_name.get().strip()
        emp_id = self.entry_id.get().strip()
        role = self.entry_role.get().strip()
        
        if not name:
            self._set_status("❌ Vui lòng nhập HỌ VÀ TÊN!", CTK_DANGER)
            return
        if not emp_id:
            self._set_status("❌ Vui lòng nhập MÃ NHÂN VIÊN!", CTK_DANGER)
            return
        if self.current_frame is None or not self.camera_running:
            self._set_status("❌ Camera chưa sẵn sàng hoặc đang tạm dừng!", CTK_DANGER)
            return
            
        # 2. Kiểm tra khuôn mặt hợp lệ (Khung Xanh lá)
        if not getattr(self, 'is_face_valid', False):
            self._set_status("❌ KHUÔN MẶT CHƯA HỢP LỆ: Vui lòng đưa mặt vào đúng vị trí khung xanh!", CTK_DANGER)
            return
        
        frame = self.current_frame.copy()
        fw, fh = self.frame_width, self.frame_height
        
        # 3. Cắt (crop) lấy khuôn mặt ngay tại frame hiện tại
        faces = detect_faces(self.face_model, frame, self.device)
        face_crop = frame
        if len(faces) > 0:
            fx1, fy1, fx2, fy2, _ = faces[0]
            pad_x = int((fx2 - fx1) * 0.1)
            pad_y = int((fy2 - fy1) * 0.1)
            cx1 = max(0, fx1 - pad_x)
            cy1 = max(0, fy1 - pad_y)
            cx2 = min(fw, fx2 + pad_x)
            cy2 = min(fh, fy2 + pad_y)
            if cx2 > cx1 and cy2 > cy1:
                face_crop = frame[cy1:cy2, cx1:cx2]
        
        # Lưu frame và hiển thị Preview ngay lập tức
        self.captured_photo = frame
        preview_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        preview_pil = Image.fromarray(preview_rgb).resize((160, 120), Image.LANCZOS)
        preview_ctk = ctk.CTkImage(light_image=preview_pil, dark_image=preview_pil, size=(160, 120))
        self.preview_label.configure(image=preview_ctk, text="")
        self.preview_label.image = preview_ctk
        self.preview_label.pack(pady=(5, 0))
        
        # 4. Kích hoạt Loading UI
        self.btn_capture.configure(state="disabled", text="⏳ Đang trích xuất Vector AI...")
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.start()
        self._set_status("⏳ Đang trích xuất Vector AI và lưu dữ liệu...", CTK_WARNING)
        
        # 5. Khởi chạy AI Thread chạy ngầm (Non-blocking UI)
        threading.Thread(
            target=self._ai_worker_save_face,
            args=(face_crop, frame, name, emp_id, role),
            daemon=True
        ).start()

    def _ai_worker_save_face(self, face_crop, full_frame, name, emp_id, role):
        """
        Background Thread: Trích xuất Vector khuôn mặt qua DeepFace và lưu file.
        Không thao tác trực tiếp với UI ở đây.
        """
        try:
            from deepface import DeepFace
            
            # --- 1. Trích xuất Vector AI (Facenet512 512-dim) ---
            reps = DeepFace.represent(
                img_path=face_crop,
                model_name=DEEPFACE_MODEL_NAME,
                detector_backend="skip",
                enforce_detection=False
            )
            embedding_vector = reps[0]["embedding"] if reps and len(reps) > 0 else None
            
            # --- 2. Lưu ảnh ra các thư mục ---
            safe_name = name.replace(" ", "_")
            safe_role = role.replace(" ", "_") if role else "Nhân_viên"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{emp_id}@{safe_role}@{safe_name}_{timestamp}.jpg"
            
            Path(DATA_FACES_DIR).mkdir(parents=True, exist_ok=True)
            Path("images").mkdir(parents=True, exist_ok=True)
            Path("database/images").mkdir(parents=True, exist_ok=True)
            Path("data").mkdir(parents=True, exist_ok=True)
            
            filepath_main = Path(DATA_FACES_DIR) / filename
            filepath_crop = Path("images") / filename
            filepath_db = Path("database/images") / filename
            
            # Lưu ảnh an toàn hỗ trợ ký tự tiếng Việt (Unicode) trên Windows
            try:
                cv2.imencode('.jpg', full_frame)[1].tofile(str(filepath_main))
                cv2.imencode('.jpg', face_crop)[1].tofile(str(filepath_crop))
                cv2.imencode('.jpg', face_crop)[1].tofile(str(filepath_db))
            except Exception:
                cv2.imwrite(str(filepath_main), full_frame)
                cv2.imwrite(str(filepath_crop), face_crop)
            
            # --- 3. Lưu Vector vào embeddings.pkl ---
            embeddings_file = Path("data/embeddings.pkl")
            embeddings_data = {}
            if embeddings_file.exists():
                try:
                    with open(embeddings_file, "rb") as f:
                        embeddings_data = pickle.load(f)
                except Exception as pe:
                    print(f"[AI WORKER] Cảnh báo đọc embeddings.pkl: {pe}")
                    embeddings_data = {}
                    
            embeddings_data[emp_id] = {
                "id": emp_id,
                "name": name,
                "role": role,
                "embedding": embedding_vector,
                "image_path": str(filepath_main),
                "timestamp": timestamp
            }
            
            with open(embeddings_file, "wb") as f:
                pickle.dump(embeddings_data, f)
                
            # Cập nhật ngay cache embeddings trong RAM
            self.embeddings_cache = embeddings_data
                
            print(f"[AI WORKER] Trích xuất Vector & lưu thành công: {emp_id} - {name} ({filepath_main})")
            
            # --- 4. Báo kết quả về Main Thread ---
            self.after(0, self._finish_enrollment, True, name, emp_id, "")
            
        except Exception as e:
            print(f"[AI WORKER ERROR] Lỗi xử lý AI: {e}")
            self.after(0, self._finish_enrollment, False, name, emp_id, str(e))

    def _finish_enrollment(self, success, name="", emp_id="", error_msg=""):
        """
        Main Thread: Nhận tín hiệu kết thúc từ AI Worker và hoàn tất UI.
        """
        # 1. Dừng và ẩn thanh loading
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        
        # 2. Khôi phục nút bấm
        self.btn_capture.configure(state="normal", text="📸   QUÉT VÀ LƯU KHUÔN MẶT")
        
        # 3. Cập nhật kết quả
        if success:
            self._set_status(f"✅ Quét và lưu khuôn mặt thành công: {name} ({emp_id})!", CTK_SUCCESS)
            
            # Tự động xóa các ô nhập liệu
            self.entry_name.delete(0, "end")
            self.entry_id.delete(0, "end")
            self.entry_role.delete(0, "end")
            
            # Cập nhật danh sách database nếu có
            if hasattr(self, '_load_database_to_scrollable') and hasattr(self, 'db_scroll'):
                query = self.search_entry.get().lower() if hasattr(self, 'search_entry') else ""
                self._load_database_to_scrollable(query)
        else:
            self._set_status(f"❌ Lưu dữ liệu thất bại: {error_msg}", CTK_DANGER)

    def _capture_face(self):
        """Hỗ trợ tương thích ngược cho hàm chụp ảnh."""
        self._start_enrollment_process()

    def _save_employee(self):
        """Hỗ trợ tương thích ngược cho hàm lưu nhân viên."""
        self._start_enrollment_process()

    def _set_status(self, text, color=CTK_TEXT_DIM):
        """Cập nhật dòng trạng thái trên form."""
        self.status_label.configure(text=text, text_color=color)
    

    # ============================================
    # CLEANUP
    # ============================================
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


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    print("=" * 50)
    print("  ADMIN PANEL - HỆ THỐNG QUẢN TRỊ AI")
    print("  CustomTkinter + OpenCV + AI Pipeline")
    print("=" * 50)
    print()
    
    app = AdminPanel()
    app.mainloop()
