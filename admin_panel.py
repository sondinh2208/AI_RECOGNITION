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
                    fg_color=CTK_BTN_ACTIVE, text_color="#ffffff",
                    font=ctk.CTkFont(size=14, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color=CTK_TEXT,
                    font=ctk.CTkFont(size=14),
                )
    
    def _navigate(self, page_id):
        """Xử lý chuyển trang."""
        self.current_page = page_id
        self._highlight_nav(page_id)
        print(f"[NAV] Chuyển đến trang: {page_id}")
    

    # ============================================
    # MAIN AREA (Khu vực chính - Phải)
    # ============================================
    def _build_main_area(self):
        """Xây dựng khu vực chính (Form + Camera)."""
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nswe", padx=15, pady=15)
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=0)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        self._build_form_column()
        self._build_camera_column()
    

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
            border_color=CTK_ACCENT, fg_color="#0d1b2a",
        )
        self.entry_name.pack(fill="x", pady=(0, 18))
        
        # Mã nhân viên
        ctk.CTkLabel(fields, text="MÃ NHÂN VIÊN",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x", pady=(0, 5))
        self.entry_id = ctk.CTkEntry(
            fields, placeholder_text="VD: NV001", height=42,
            font=ctk.CTkFont(size=14), corner_radius=8,
            border_color=CTK_ACCENT, fg_color="#0d1b2a",
        )
        self.entry_id.pack(fill="x", pady=(0, 18))
        
        # Chức vụ
        ctk.CTkLabel(fields, text="CHỨC VỤ / PHÒNG BAN",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x", pady=(0, 5))
        self.entry_role = ctk.CTkEntry(
            fields, placeholder_text="VD: Kỹ sư phần mềm - Phòng IT", height=42,
            font=ctk.CTkFont(size=14), corner_radius=8,
            border_color=CTK_ACCENT, fg_color="#0d1b2a",
        )
        self.entry_role.pack(fill="x", pady=(0, 25))
        
        ctk.CTkFrame(form_frame, height=1, fg_color=CTK_ACCENT).pack(
            fill="x", padx=25, pady=(0, 20))
        
        # --- Buttons ---
        btn_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=(0, 15))
        
        self.btn_capture = ctk.CTkButton(
            btn_frame, text="📸  CHỤP ẢNH KHUÔN MẶT",
            font=ctk.CTkFont(size=14, weight="bold"), height=45,
            corner_radius=10, fg_color=CTK_ACCENT, hover_color="#1a4a7a",
            command=self._capture_face,
        )
        self.btn_capture.pack(fill="x", pady=(0, 8))
        
        self.btn_save = ctk.CTkButton(
            btn_frame, text="💾  LƯU DỮ LIỆU (ONE-SHOT)",
            font=ctk.CTkFont(size=15, weight="bold"), height=50,
            corner_radius=10, fg_color="#00865a", hover_color="#00a06a",
            command=self._save_employee,
        )
        self.btn_save.pack(fill="x", pady=(0, 8))
        
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
            font=ctk.CTkFont(size=12), height=35, corner_radius=8,
            fg_color=CTK_ACCENT, hover_color="#1a4a7a",
            command=self._toggle_camera,
        )
        self.btn_toggle_cam.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        self.btn_restart_cam = ctk.CTkButton(
            cam_btn_frame, text="🔄  Khởi động lại",
            font=ctk.CTkFont(size=12), height=35, corner_radius=8,
            fg_color=CTK_ACCENT, hover_color="#1a4a7a",
            command=self._restart_camera,
        )
        self.btn_restart_cam.pack(side="right", expand=True, fill="x", padx=(5, 0))
    

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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{emp_id}_{safe_name}_{timestamp}.jpg"
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
