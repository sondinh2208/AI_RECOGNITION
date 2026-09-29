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
from PIL import Image, ImageTk
from datetime import datetime
from pathlib import Path
import threading
import time

from config import (
    # Admin Panel UI
    CTK_BG_DARK, CTK_BG_MAIN, CTK_ACCENT, CTK_PRIMARY,
    CTK_SUCCESS, CTK_DANGER, CTK_WARNING,
    CTK_TEXT, CTK_TEXT_DIM, CTK_CARD, CTK_SIDEBAR_HOVER, CTK_BTN_ACTIVE,
    ADMIN_WINDOW_WIDTH, ADMIN_WINDOW_HEIGHT, ADMIN_SIDEBAR_WIDTH,
    ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT, ADMIN_CAMERA_FPS_DELAY,
    DATA_FACES_DIR,
)
from ai_engine import (
    check_gpu_quick,
    load_face_model, load_person_model, load_mediapipe_detector,
    detect_faces, detect_persons,
    check_face_constraints,
)
from ekyc_renderer import (
    compute_ekyc_layout, create_rounded_rect_mask,
    render_full_hud,
)


# ============================================
# CustomTkinter Theme
# ============================================
ctk.set_appearance_mode("light")
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
        self.title("HỆ THỐNG QUẢN TRỊ AI - NHẬN DIỆN KHUÔN MẶT")
        self.geometry(f"{ADMIN_WINDOW_WIDTH}x{ADMIN_WINDOW_HEIGHT}")
        self.minsize(1000, 600)
        self.configure(fg_color=CTK_BG_MAIN)
        
        # --- Biến trạng thái Camera ---
        self.camera_cap = None
        self.camera_running = False
        self.current_frame = None       # Frame gốc (sạch, không HUD)
        self.latest_processed_frame = None
        self.camera_thread = None
        self.current_page = "add_employee"
        self.photo_image = None
        
        # --- Biến trạng thái AI ---
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
        
        # --- Load AI Models ---
        self._load_ai_models()
        
        # --- Xây dựng UI ---
        self._build_sidebar()
        self._build_main_area()
        
        # --- Khởi động camera ---
        self._start_camera()
        
        # --- Xử lý khi đóng cửa sổ ---
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
    

    def _load_ai_models(self):
        """Load tất cả AI models qua ai_engine module."""
        print("[AI] Đang load AI models...")
        self.device = check_gpu_quick()
        self.face_model, self.device = load_face_model(self.device)
        self.person_model, self.device = load_person_model(self.device)
        self.face_detector = load_mediapipe_detector()
        print("[AI] Load models hoàn tất.")
    

    # ============================================
    # SIDEBAR (Thanh điều hướng trái)
    # ============================================
    def _build_sidebar(self):
        """Xây dựng thanh sidebar bên trái."""
        self.sidebar = ctk.CTkFrame(
            self,
            width=ADMIN_SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=CTK_BG_DARK,
        )
        self.sidebar.grid(row=0, column=0, sticky="nswe")
        self.sidebar.grid_propagate(False)
        
        # --- Logo / Tiêu đề ---
        logo_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", padx=20, pady=(25, 5))
        
        ctk.CTkLabel(
            logo_frame, text="⚙  HỆ THỐNG",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x")
        
        ctk.CTkLabel(
            logo_frame, text="QUẢN TRỊ AI",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=CTK_PRIMARY, anchor="w",
        ).pack(fill="x")
        
        # --- Separator ---
        ctk.CTkFrame(self.sidebar, height=2, fg_color=CTK_ACCENT).pack(
            fill="x", padx=20, pady=(15, 20))
        
        # --- Nav buttons ---
        self.nav_buttons = {}
        nav_items = [
            ("dashboard",     "📊  Bảng điều khiển"),
            ("add_employee",  "👤  Quét khuôn mặt"),
            ("attendance",    "📍  Nhận diện điểm danh"),
            ("database",      "📁  Người đăng ký"),
            ("history",       "📋  Lịch sử ra vào"),
        ]
        
        for page_id, label in nav_items:
            btn = ctk.CTkButton(
                self.sidebar, text=label,
                font=ctk.CTkFont(size=14), height=42, anchor="w",
                corner_radius=8, fg_color="transparent",
                text_color=CTK_TEXT, hover_color=CTK_SIDEBAR_HOVER,
                command=lambda pid=page_id: self._navigate(pid),
            )
            btn.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[page_id] = btn
        
        self._highlight_nav("add_employee")
        
        # --- Footer ---
        ctk.CTkFrame(self.sidebar, fg_color="transparent").pack(fill="both", expand=True)
        ctk.CTkFrame(self.sidebar, height=1, fg_color=CTK_ACCENT).pack(
            fill="x", padx=20, pady=(0, 10))
        
        self.appearance_mode_menu = ctk.CTkOptionMenu(
            self.sidebar, values=["Light", "Dark"],
            command=self._change_appearance_mode_event,
            fg_color=CTK_CARD, button_color=CTK_ACCENT, text_color=CTK_TEXT
        )
        self.appearance_mode_menu.pack(padx=20, pady=(0, 10))
        self.appearance_mode_menu.set("Light")
        
        ctk.CTkLabel(
            self.sidebar, text="AI Recognition v2.0",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
        ).pack(pady=(0, 5))
        
        ctk.CTkLabel(
            self.sidebar, text="CUDA GPU-Accelerated",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM,
        ).pack(pady=(0, 20))
    

    def _highlight_nav(self, active_page_id):
        """Đổi màu nút đang active trên sidebar."""
        for pid, btn in self.nav_buttons.items():
            if pid == active_page_id:
                btn.configure(
                    fg_color=CTK_BTN_ACTIVE, text_color=("#ffffff", "#ffffff"),
                    font=ctk.CTkFont(size=14, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color=CTK_TEXT,
                    font=ctk.CTkFont(size=14),
                )
                
    def _change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode)
    
    def _navigate(self, page_id):
        """Xử lý chuyển trang."""
        self.current_page = page_id
        self._highlight_nav(page_id)
        print(f"[NAV] Chuyển đến trang: {page_id}")
        self._show_page(page_id)
        
    def _show_page(self, page_id):
        """Hiển thị frame được chọn và xử lý camera."""
        # Ẩn tất cả các trang
        for pid, frame in getattr(self, "page_frames", {}).items():
            frame.grid_remove()
            
        # Hiển thị trang hiện tại
        if page_id in self.page_frames:
            self.page_frames[page_id].grid()
            if page_id == "database" and hasattr(self, "search_entry"):
                self._load_database_to_scrollable(self.search_entry.get().lower())
            
        # Quản lý vòng lặp camera để tiết kiệm tài nguyên
        if page_id == "add_employee" or page_id == "attendance":
            if not self.camera_running and self.camera_cap is not None:
                self._toggle_camera()
            elif self.camera_cap is None:
                self._start_camera()
        else:
            if self.camera_running:
                self._toggle_camera()

    # ============================================
    # MAIN AREA (Khu vực chính - Phải)
    # ============================================
    def _build_main_area(self):
        """Xây dựng khu vực chính (Gồm nhiều trang thay đổi)."""
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nswe", padx=15, pady=15)
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=1)
        
        self.page_frames = {}
        
        # --- Trang 1: Quét khuôn mặt (Add Employee) ---
        self.main_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.main_frame.grid(row=0, column=0, sticky="nswe")
        
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=0)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        self._build_form_column()
        self._build_camera_column()
        self.page_frames["add_employee"] = self.main_frame
        
        # --- Trang 2: Người đăng ký (Database) ---
        self.database_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.database_frame.grid(row=0, column=0, sticky="nswe")
        self._build_database_page()
        self.page_frames["database"] = self.database_frame
        
        # Mặc định mở trang Add Employee
        self._show_page("add_employee")
    

    # ============================================
    # FORM NHẬP LIỆU (Cột trái)
    # ============================================
    def _build_form_column(self):
        """Xây dựng form đăng ký nhân viên mới."""
        form_frame = ctk.CTkFrame(self.main_frame, fg_color=CTK_CARD, corner_radius=12)
        form_frame.grid(row=0, column=0, sticky="nswe", padx=(0, 12))
        
        # --- Header ---
        header = ctk.CTkFrame(form_frame, fg_color="transparent")
        header.pack(fill="x", padx=25, pady=(25, 5))
        
        ctk.CTkLabel(
            header, text="ĐĂNG KÝ KHUÔN MẶT MỚI",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CTK_PRIMARY, anchor="w",
        ).pack(fill="x")
        
        ctk.CTkLabel(
            header, text="Nhập thông tin nhân viên và chụp ảnh khuôn mặt",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(3, 0))
        
        ctk.CTkFrame(form_frame, height=2, fg_color=CTK_ACCENT).pack(
            fill="x", padx=25, pady=(15, 20))
        
        # --- Fields ---
        fields = ctk.CTkFrame(form_frame, fg_color="transparent")
        fields.pack(fill="x", padx=25)
        
        # Họ và tên
        ctk.CTkLabel(fields, text="HỌ VÀ TÊN NHÂN VIÊN",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x", pady=(0, 5))
        self.entry_name = ctk.CTkEntry(
            fields, placeholder_text="VD: Nguyễn Văn A", height=42,
            font=ctk.CTkFont(size=14), corner_radius=8,
            border_color=CTK_ACCENT, fg_color=("#ffffff", "#0d1b2a"),
            text_color=CTK_TEXT, placeholder_text_color=CTK_TEXT_DIM,
        )
        self.entry_name.pack(fill="x", pady=(0, 18))
        
        # Mã nhân viên
        ctk.CTkLabel(fields, text="MÃ NHÂN VIÊN",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x", pady=(0, 5))
        self.entry_id = ctk.CTkEntry(
            fields, placeholder_text="VD: NV001", height=42,
            font=ctk.CTkFont(size=14), corner_radius=8,
            border_color=CTK_ACCENT, fg_color=("#ffffff", "#0d1b2a"),
            text_color=CTK_TEXT, placeholder_text_color=CTK_TEXT_DIM,
        )
        self.entry_id.pack(fill="x", pady=(0, 18))
        
        # Chức vụ
        ctk.CTkLabel(fields, text="CHỨC VỤ / PHÒNG BAN",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x", pady=(0, 5))
        self.entry_role = ctk.CTkEntry(
            fields, placeholder_text="VD: Kỹ sư phần mềm - Phòng IT", height=42,
            font=ctk.CTkFont(size=14), corner_radius=8,
            border_color=CTK_ACCENT, fg_color=("#ffffff", "#0d1b2a"),
            text_color=CTK_TEXT, placeholder_text_color=CTK_TEXT_DIM,
        )
        self.entry_role.pack(fill="x", pady=(0, 25))
        
        ctk.CTkFrame(form_frame, height=1, fg_color=CTK_ACCENT).pack(
            fill="x", padx=25, pady=(0, 20))
        
        # --- Buttons ---
        btn_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=(0, 15))
        
        self.btn_capture = ctk.CTkButton(
            btn_frame, text="📸  CHỤP ẢNH KHUÔN MẶT",
            font=ctk.CTkFont(size=14, weight="bold"), height=46,
            corner_radius=8, fg_color=("#2563eb", "#3b82f6"), hover_color=("#1d4ed8", "#2563eb"),
            text_color=("#ffffff", "#ffffff"),
            command=self._capture_face,
        )
        self.btn_capture.pack(fill="x", pady=(0, 10))
        
        self.btn_save = ctk.CTkButton(
            btn_frame, text="💾  LƯU DỮ LIỆU (ONE-SHOT)",
            font=ctk.CTkFont(size=14, weight="bold"), height=48,
            corner_radius=8, fg_color=("#10b981", "#059669"), hover_color=("#059669", "#047857"),
            text_color=("#ffffff", "#ffffff"),
            command=self._save_employee,
        )
        self.btn_save.pack(fill="x", pady=(0, 10))
        
        # --- Status ---
        self.status_label = ctk.CTkLabel(
            form_frame, text="⏳ Sẵn sàng đăng ký nhân viên mới",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        )
        self.status_label.pack(fill="x", padx=25, pady=(0, 20))
        
        # --- Preview ---
        self.preview_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        self.preview_frame.pack(fill="x", padx=25, pady=(0, 15))
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="", width=160, height=120)
        self.captured_photo = None
    

    # ============================================
    # CAMERA COLUMN (Cột phải)
    # ============================================
    def _build_camera_column(self):
        """Xây dựng vùng hiển thị camera trực tiếp."""
        camera_frame = ctk.CTkFrame(
            self.main_frame, fg_color=CTK_CARD, corner_radius=12,
            width=ADMIN_CAMERA_WIDTH + 40,
        )
        camera_frame.grid(row=0, column=1, sticky="nswe")
        camera_frame.grid_propagate(False)
        
        # --- Header ---
        cam_header = ctk.CTkFrame(camera_frame, fg_color="transparent")
        cam_header.pack(fill="x", padx=20, pady=(20, 10))
        
        title_row = ctk.CTkFrame(cam_header, fg_color="transparent")
        title_row.pack(fill="x")
        
        ctk.CTkLabel(
            title_row, text="📹  CAMERA TRỰC TIẾP",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")
        
        self.cam_status_dot = ctk.CTkLabel(
            title_row, text="● LIVE",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_SUCCESS, anchor="e",
        )
        self.cam_status_dot.pack(side="right")
        
        # --- Camera label ---
        self.camera_label = ctk.CTkLabel(
            camera_frame, text="Đang khởi tạo camera...",
            font=ctk.CTkFont(size=14), text_color=CTK_TEXT_DIM,
            width=ADMIN_CAMERA_WIDTH, height=ADMIN_CAMERA_HEIGHT,
            fg_color="#000000", corner_radius=8,
        )
        self.camera_label.pack(padx=20, pady=(0, 10))
        
        # --- Info ---
        self.cam_info_label = ctk.CTkLabel(
            camera_frame, text="Đang kết nối camera...",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
        )
        self.cam_info_label.pack(pady=(0, 5))
        
        # --- Control buttons ---
        cam_btn_frame = ctk.CTkFrame(camera_frame, fg_color="transparent")
        cam_btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        self.btn_toggle_cam = ctk.CTkButton(
            cam_btn_frame, text="⏸  Tạm dừng",
            font=ctk.CTkFont(size=12, weight="bold"), height=36, corner_radius=8,
            fg_color=("#f1f5f9", "#1e293b"), hover_color=("#e2e8f0", "#334155"),
            border_width=1, border_color=("#cbd5e1", "#334155"),
            text_color=CTK_TEXT,
            command=self._toggle_camera,
        )
        self.btn_toggle_cam.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        self.btn_restart_cam = ctk.CTkButton(
            cam_btn_frame, text="🔄  Khởi động lại",
            font=ctk.CTkFont(size=12, weight="bold"), height=36, corner_radius=8,
            fg_color=("#f1f5f9", "#1e293b"), hover_color=("#e2e8f0", "#334155"),
            border_width=1, border_color=("#cbd5e1", "#334155"),
            text_color=CTK_TEXT,
            command=self._restart_camera,
        )
        self.btn_restart_cam.pack(side="right", expand=True, fill="x", padx=(5, 0))
    

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
            width=280, height=38, corner_radius=8, border_color=CTK_ACCENT, fg_color=("#ffffff", "#0d1b2a")
        )
        self.search_entry.pack(side="left", padx=(0, 15))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)
        
        btn_add_new = ctk.CTkButton(
            btn_frame, text="+ Đăng ký mới",
            font=ctk.CTkFont(size=14, weight="bold"), height=38,
            corner_radius=8, fg_color="#2563eb", hover_color="#1d4ed8", text_color=("#ffffff", "#ffffff"),
            command=lambda: self._navigate("add_employee")
        )
        btn_add_new.pack(side="left")

        # --- Stats Cards Section ---
        stats_frame = ctk.CTkFrame(self.database_frame, fg_color="transparent")
        stats_frame.pack(fill="x", padx=25, pady=(0, 20))
        stats_frame.grid_columnconfigure((0,1,2,3), weight=1, uniform="card")
        
        # Helper to create card
        def create_stat_card(parent, col, icon, title, value, subtext, icon_color="#3b82f6"):
            card = ctk.CTkFrame(parent, fg_color=CTK_CARD, corner_radius=12)
            card.grid(row=0, column=col, sticky="nsew", padx=8 if col > 0 else (0, 8))
            
            icon_box = ctk.CTkFrame(card, width=45, height=45, corner_radius=10, fg_color=("#f1f5f9", "#1e293b"))
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
        table_container = ctk.CTkFrame(self.database_frame, fg_color=CTK_CARD, corner_radius=12)
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
        dropdown = ctk.CTkOptionMenu(show_frame, values=["10", "20", "50"], width=60, height=28, fg_color=CTK_CARD, button_color=CTK_ACCENT, text_color=CTK_TEXT)
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
        thead = ctk.CTkFrame(self.db_scroll, fg_color=CTK_BG_DARK, height=45, corner_radius=8)
        thead.pack(fill="x", padx=5, pady=(0, 6))
        
        headers = ["", "Ảnh", "Mã NV", "Họ và tên", "Chức vụ", "Trạng thái", "Hành động"]
        weights = [3, 5, 8, 24, 16, 14, 10]
        
        for i, w in enumerate(weights):
            thead.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        for i, text in enumerate(headers):
            if i == 0:
                chk = ctk.CTkCheckBox(thead, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color="#D1D5DB", fg_color="#2563EB")
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
        bg_color = ("#ffffff", "#1e2a3a") if row_index % 2 == 0 else ("#f8fafc", "#253447") 
        
        row_frame = ctk.CTkFrame(self.db_scroll, fg_color=bg_color, corner_radius=6)
        row_frame.pack(fill="x", pady=2, padx=5)
        
        weights = [3, 5, 8, 24, 16, 14, 10]
        for i, w in enumerate(weights):
            row_frame.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        # 0. Checkbox
        checkbox = ctk.CTkCheckBox(row_frame, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color="#D1D5DB", fg_color="#2563EB")
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
        
        btn_view = ctk.CTkButton(action_frame, text="👁", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=("#e5e7eb", "#334155"), text_color=("#3b82f6", "#60a5fa"), hover_color=CTK_SIDEBAR_HOVER)
        btn_view.pack(side="left", padx=2)
        
        btn_edit = ctk.CTkButton(action_frame, text="📝", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=("#e5e7eb", "#334155"), text_color=("#6b7280", "#cbd5e1"), hover_color=CTK_SIDEBAR_HOVER)
        btn_edit.pack(side="left", padx=2)
        
        btn_del = ctk.CTkButton(
            action_frame, text="🗑", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1, border_color=("#e5e7eb", "#334155"), text_color=("#ef4444", "#fca5a5"), hover_color=("#fee2e2", "#7f1d1d"),
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
                    
                    self.cam_info_label.configure(
                        text=f"Camera ID: {cam_id}  |  {self.frame_width}x{self.frame_height}  |  AI Active",
                        text_color=CTK_SUCCESS)
                    self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
                    print(f"[CAMERA] Đã kết nối camera ID={cam_id} ({self.frame_width}x{self.frame_height})")
                    
                    self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                    self.camera_thread.start()
                    
                    self._update_frame()
                    return
                else:
                    cap.release()
        
        self.cam_info_label.configure(text="❌ Không tìm thấy camera!", text_color=CTK_DANGER)
        self.cam_status_dot.configure(text="● OFFLINE", text_color=CTK_DANGER)
        self.camera_label.configure(text="Không thể kết nối camera.\nKiểm tra thiết bị.")
        print("[CAMERA] KHÔNG TÌM THẤY CAMERA!")
    

    def _camera_loop(self):
        """
        Background Thread: AI Pipeline
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
        
        while self.camera_running:
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
            # Tối ưu FPS: Tắt detect_persons vì hiện tại không dùng tới để hiển thị UI
            # persons = detect_persons(self.person_model, frame, self.device)
            
            mp_results = None
            # Tối ưu FPS cấp độ 2: Chỉ chạy MediaPipe (tính góc nghiêng) khi YOLO phát hiện có khuôn mặt!
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
                
            # Cập nhật trạng thái hiển thị dựa trên streak
            if green_streak >= 5:       # Phải ổn định 5 frames mới cho Xanh (Hợp lệ)
                display_locked = True
                display_color = current_color
                display_text = status_text
            elif red_streak >= 2:       # Chỉ cần 2 frames là báo Đỏ ngay (Lỗi)
                display_locked = False
                display_color = current_color
                display_text = status_text
            # Nếu chưa đủ streak, GIỮ NGUYÊN trạng thái của display_color và display_text
            
            # Cập nhật biến trạng thái (Atomic)
            self.is_face_valid = display_locked
            self.ai_status_text = display_text
            
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
            
            # Làm mượt FPS bằng Exponential Moving Average (tránh nhảy số liên tục)
            if smoothed_fps == 0.0:
                smoothed_fps = instant_fps
            else:
                smoothed_fps = 0.9 * smoothed_fps + 0.1 * instant_fps
                
            cv2.putText(output, f"FPS: {int(smoothed_fps)}", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2, cv2.LINE_AA)
                
            self.latest_processed_frame = output
            time.sleep(0.001)  # Giảm sleep xuống 1ms để giải phóng tối đa FPS

    def _update_frame(self):
        """
        Main UI Thread: Lấy frame đã xử lý và vẽ lên màn hình.
        """
        if not self.camera_running:
            return
            
        if self.latest_processed_frame is not None:
            output = self.latest_processed_frame
            
            # --- Convert → Tkinter Image ---
            # Dùng INTER_LINEAR siêu nhanh
            output_resized = cv2.resize(output, (ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT), interpolation=cv2.INTER_LINEAR)
            frame_rgb = cv2.cvtColor(output_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(frame_rgb)
            
            # Khôi phục CTkImage để hỗ trợ HighDPI Scaling (giải quyết lỗi khung hình bị nhỏ lại hoặc sai lệch mask)
            # Vì ta đã chạy đa luồng và cv2.resize siêu nhanh nên CTkImage lúc này không còn là bottle-neck quá lớn.
            ctk_image = ctk.CTkImage(
                light_image=pil_image, dark_image=pil_image,
                size=(ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT),
            )
            
            self.camera_label.configure(image=ctk_image, text="")
            self.photo_image = ctk_image
        
        self.after(ADMIN_CAMERA_FPS_DELAY, self._update_frame)
    

    def _toggle_camera(self):
        """Tạm dừng / tiếp tục camera."""
        if self.camera_running:
            self.camera_running = False
            self.btn_toggle_cam.configure(text="▶  Tiếp tục")
            self.cam_status_dot.configure(text="● PAUSED", text_color=CTK_WARNING)
            self.cam_info_label.configure(text="Camera đã tạm dừng", text_color=CTK_WARNING)
        else:
            self.camera_running = True
            self.btn_toggle_cam.configure(text="⏸  Tạm dừng")
            self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
            self.cam_info_label.configure(text="Camera đang hoạt động", text_color=CTK_SUCCESS)
            
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
        self.after(500, self._start_camera)
    

    # ============================================
    # CHỤP ẢNH & LƯU DỮ LIỆU
    # ============================================
    def _capture_face(self):
        """Chụp frame hiện tại làm ảnh khuôn mặt."""
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
            self._set_status("⏳ CẢNH BÁO: Bấm quá nhanh! Nút chụp khóa 10 giây.", CTK_WARNING)
            
            def unlock_capture():
                self.is_capture_locked = False
                self.spam_count = 0
                self.btn_capture.configure(state="normal")
                self._set_status("✅ Đã mở khóa nút chụp. Bạn có thể tiếp tục.", CTK_SUCCESS)
                
            self.after(10000, unlock_capture)
            return
        # --- END ANTI-SPAM ---

        if self.current_frame is None:
            self._set_status("❌ Camera chưa sẵn sàng!", CTK_DANGER)
            return
        
        frame = self.current_frame.copy()
        
        # --- BƯỚC 1: QUÉT LẠI BỨC ẢNH VỚI CHẾ ĐỘ NGHIÊM NGẶT (STRICT MODE) ---
        # Kiểm tra che khuất và độ tự tin cao để tránh lưu ảnh lỗi
        fw, fh = self.frame_width, self.frame_height
        faces = detect_faces(self.face_model, frame, self.device)
        
        if len(faces) == 0:
            self._set_status("❌ ẢNH BỊ TỪ CHỐI: Không tìm thấy khuôn mặt!", CTK_DANGER)
            return
            
        mp_results = None
        if self.face_detector is not None:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            mp_results = self.face_detector.detect(mp_image)
            
        _, status_text, is_locked = check_face_constraints(
            faces, self.constraint_box, mp_results, fw, fh, strict_mode=True
        )
        
        if not is_locked:
            self._set_status(f"❌ ẢNH BỊ TỪ CHỐI: {status_text}", CTK_DANGER)
            return
        
        # --- BƯỚC 2: NẾU VƯỢT QUA KIỂM TRA -> CHẤP NHẬN ẢNH ---
        self.captured_photo = frame
        
        preview_rgb = cv2.cvtColor(self.captured_photo, cv2.COLOR_BGR2RGB)
        preview_pil = Image.fromarray(preview_rgb).resize((160, 120), Image.LANCZOS)
        preview_ctk = ctk.CTkImage(
            light_image=preview_pil, dark_image=preview_pil, size=(160, 120))
        self.preview_label.configure(image=preview_ctk, text="")
        self.preview_label.image = preview_ctk
        self.preview_label.pack(pady=(5, 0))
        
        self._set_status("📸 Bức ảnh đạt chuẩn! Nhấn LƯU DỮ LIỆU để hoàn tất.", CTK_SUCCESS)
    

    def _save_employee(self):
        """
        Lưu thông tin nhân viên + ảnh khuôn mặt.
        BẮT BUỘC: self.is_face_valid == True mới cho lưu.
        """
        name = self.entry_name.get().strip()
        emp_id = self.entry_id.get().strip()
        role = self.entry_role.get().strip()
        
        if not name:
            self._set_status("❌ Vui lòng nhập HỌ VÀ TÊN!", CTK_DANGER)
            return
        if not emp_id:
            self._set_status("❌ Vui lòng nhập MÃ NHÂN VIÊN!", CTK_DANGER)
            return
        if self.captured_photo is None:
            self._set_status("❌ Chưa chụp ảnh! Nhấn CHỤP ẢNH trước.", CTK_DANGER)
            return
        
        # Lưu ảnh
        safe_name = name.replace(" ", "_")
        safe_role = role.replace(" ", "_") if role else "Nhân_viên"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{emp_id}@{safe_role}@{safe_name}_{timestamp}.jpg"
        filepath = Path(DATA_FACES_DIR) / filename
        
        cv2.imwrite(str(filepath), self.captured_photo)
        
        print(f"[SAVE] Đã lưu: {filepath}")
        print(f"       Tên: {name} | Mã NV: {emp_id} | Chức vụ: {role or 'N/A'}")
        
        self._set_status(f"✅ Đã lưu thành công: {filename}", CTK_SUCCESS)
        
        # Reset form
        self.entry_name.delete(0, "end")
        self.entry_id.delete(0, "end")
        self.entry_role.delete(0, "end")
        self.captured_photo = None
        self.preview_label.configure(image=None, text="")
        self.preview_label.pack_forget()
    

    def _set_status(self, text, color=CTK_TEXT_DIM):
        """Cập nhật dòng trạng thái trên form."""
        self.status_label.configure(text=text, text_color=color)
    

    # ============================================
    # CLEANUP
    # ============================================
    def _on_closing(self):
        """Giải phóng tài nguyên khi đóng."""
        self.camera_running = False
        if self.camera_thread is not None:
            self.camera_thread.join(timeout=1.0)
            
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
