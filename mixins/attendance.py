"""
==============================================================
ATTENDANCE MIXIN (KIOSK MODE)
Toàn bộ giao diện và logic nhận diện điểm danh tự động:
- Giao diện Kiosk: Camera Fit tỉ lệ bên trái, Result Card bên phải
- Bảng lịch sử điểm danh gần đây
- Luồng camera riêng biệt cho Kiosk Mode (YOLOv8 + ArcFace)
- Nhận diện vector Cosine từ cache RAM siêu tốc
- Xử lý trạng thái thành công / thất bại / reset thủ công chống đơ UI
==============================================================
"""

import cv2
import time
import threading
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image
import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_TEXT, CTK_TEXT_DIM, CTK_PRIMARY,
    CTK_SUCCESS, CTK_SIDEBAR_HOVER, ADMIN_CAMERA_FPS_DELAY,
    DEEPFACE_MODEL_NAME, ARCFACE_THRESHOLD, KIOSK_MIN_FACE_WIDTH,
)
from ai_engine import (
    detect_faces, calculate_cosine_distance, draw_kiosk_face_box,
)
from .ui_helpers import make_circular_avatar, create_default_avatar


class AttendanceMixin:
    """Mixin quản lý toàn bộ giao diện và nghiệp vụ nhận diện điểm danh (Kiosk Mode)."""

    def _build_attendance_page(self):
        """
        Xây dựng giao diện Kiosk Điểm danh công nghệ cao (chuẩn thiết kế screenshot).
        Gồm 2 phần:
          - Top Row: Cột Camera bên trái + Cột Kết quả nhận diện (Result Card) bên phải.
          - Bottom Row: Bảng Lịch sử điểm danh gần đây (Recent Attendance History).
        """
        print("[ATTENDANCE] page create start")
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
        cam_display_box = ctk.CTkFrame(cam_card, fg_color=("#0f172a", "#09101d"), corner_radius=10, height=315)
        cam_display_box.pack(fill="x", padx=12, pady=(12, 10))
        cam_display_box.pack_propagate(False)
        
        # Video feed label
        self.kiosk_camera_label = ctk.CTkLabel(
            cam_display_box, text="",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
            fg_color="transparent"
        )
        self.kiosk_camera_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        # Camera Loading Overlay (Minimal, Non-blocking, Clean)
        print("[ATTENDANCE] placeholder start")
        self.kiosk_cam_overlay = ctk.CTkFrame(
            cam_display_box, fg_color=("#0a0f1d", "#080d19"), corner_radius=10
        )
        overlay_content = ctk.CTkFrame(self.kiosk_cam_overlay, fg_color="transparent")
        overlay_content.place(relx=0.5, rely=0.5, anchor="center")
        
        self.kiosk_cam_spinner = ctk.CTkLabel(
            overlay_content, text="◌",
            font=ctk.CTkFont(size=26), text_color=("#38bdf8", "#38bdf8")
        )
        self.kiosk_cam_spinner.pack(pady=(0, 4))
        
        self.kiosk_cam_overlay_text = ctk.CTkLabel(
            overlay_content, text="Đang kết nối camera...",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=CTK_TEXT_DIM
        )
        self.kiosk_cam_overlay_text.pack()
        
        # Hiển thị loading overlay mặc định nếu camera chưa có frame
        if getattr(self, 'kiosk_latest_frame', None) is None:
            self.kiosk_cam_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.kiosk_cam_overlay.lift()
        print("[ATTENDANCE] placeholder end")
        
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
        
        print("[ATTENDANCE] page create end")
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
            ctk.CTkLabel(f3, text=item.get("id", ""), font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="both", expand=True, padx=8)
            
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

    def _start_kiosk_worker(self):
        """Khởi động luồng đọc camera Kiosk với token bảo vệ chống xung đột luồng cũ."""
        if getattr(self, 'kiosk_running', False) and getattr(self, 'kiosk_thread', None) is not None and self.kiosk_thread.is_alive():
            return
        self._kiosk_thread_token = getattr(self, '_kiosk_thread_token', 0) + 1
        cur_token = self._kiosk_thread_token
        self.kiosk_running = True
        print("[CAMERA] worker start")
        self.kiosk_thread = threading.Thread(
            target=self._kiosk_camera_loop,
            args=(cur_token,),
            daemon=True
        )
        self.kiosk_thread.start()

    def _kiosk_camera_loop(self, token=None):
        """
        Background Thread chuyên biệt cho Kiosk Mode (Nhận diện điểm danh):
        - Đọc frame camera liên tục.
        - Phát hiện khuôn mặt bằng YOLOv8.
        - Vẽ HUD Kiosk hiện đại (Bounding box bo góc, nhãn DA XAC THUC / DANG NHAN DIEN).
        - Khi có khuôn mặt hợp lệ và không bận nhận diện -> trigger luồng phụ DeepFace ArcFace.
        """
        prev_time = time.time()
        smoothed_fps = 0.0
        first_frame_logged = False
        
        while self.kiosk_running and self.camera_running:
            if token is not None and getattr(self, '_kiosk_thread_token', None) != token:
                break
                
            if self.camera_cap is None:
                time.sleep(0.05)
                continue
                
            ret, frame = self.camera_cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue
                
            if not first_frame_logged:
                print("[CAMERA] first frame")
                first_frame_logged = True
                
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
            
            # 5. Lưu frame và chuẩn bị ảnh PIL trong Background Thread (Không làm nặng UI Thread)
            self.kiosk_latest_frame = frame
            try:
                fh, fw = frame.shape[:2]
                lbl_w = getattr(self, '_cached_kiosk_w', 460)
                lbl_h = getattr(self, '_cached_kiosk_h', 315)
                scale = min(lbl_w / fw, lbl_h / fh)
                new_w = max(1, int(fw * scale))
                new_h = max(1, int(fh * scale))
                resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                canvas = np.zeros((lbl_h, lbl_w, 3), dtype=np.uint8)
                pad_x = (lbl_w - new_w) // 2
                pad_y = (lbl_h - new_h) // 2
                canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
                rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
                self.kiosk_latest_pil = Image.fromarray(rgb)
                if not getattr(self, '_first_processed_logged', False):
                    print("[ATTENDANCE] first processed frame")
                    self._first_processed_logged = True
            except Exception:
                pass
            time.sleep(0.01)

    def _update_kiosk_frame(self):
        """Main UI Thread: Lấy ảnh PIL đã định dạng từ background thread và hiển thị (siêu nhẹ, <1ms)."""
        if not self.kiosk_running or self.current_page != "attendance":
            return
        if not self.winfo_exists():
            return
            
        if hasattr(self, 'kiosk_camera_label'):
            cur_w = self.kiosk_camera_label.winfo_width()
            cur_h = self.kiosk_camera_label.winfo_height()
            if cur_w > 1 and cur_h > 1:
                self._cached_kiosk_w = cur_w
                self._cached_kiosk_h = cur_h
            else:
                cur_w = getattr(self, '_cached_kiosk_w', 460)
                cur_h = getattr(self, '_cached_kiosk_h', 315)
                
            pil_img = getattr(self, 'kiosk_latest_pil', None)
            if pil_img is not None:
                if not getattr(self, '_first_ui_frame_logged', False):
                    print("[ATTENDANCE] first UI frame")
                    self._first_ui_frame_logged = True
                self._hide_kiosk_cam_loading()
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(cur_w, cur_h))
                self.kiosk_camera_label.configure(image=ctk_img, text="")
                self.kiosk_camera_label.image = ctk_img
            elif self.kiosk_latest_frame is not None:
                self._hide_kiosk_cam_loading()
            
        if hasattr(self, 'lbl_kiosk_fps'):
            self.lbl_kiosk_fps.configure(text=f"FPS: {int(self.kiosk_fps)}")
            
        self._safe_after(ADMIN_CAMERA_FPS_DELAY, self._update_kiosk_frame)

    def _show_kiosk_cam_loading(self, text="Đang khởi động camera..."):
        """Hiển thị overlay loading tối giản trên khung camera khi đang khởi tạo hoặc chưa có frame."""
        if hasattr(self, 'kiosk_cam_overlay') and self.kiosk_cam_overlay.winfo_exists():
            if hasattr(self, 'kiosk_cam_overlay_text'):
                self.kiosk_cam_overlay_text.configure(text=text)
            self.kiosk_cam_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.kiosk_cam_overlay.lift()

    def _hide_kiosk_cam_loading(self):
        """Ẩn overlay loading khi frame camera đầu tiên đã sẵn sàng."""
        if hasattr(self, 'kiosk_cam_overlay') and self.kiosk_cam_overlay.winfo_exists():
            self.kiosk_cam_overlay.place_forget()

    def _toggle_kiosk_camera(self):
        """Tạm dừng / Tiếp tục quét camera Kiosk."""
        if self.kiosk_running:
            self.kiosk_running = False
            if hasattr(self, 'btn_kiosk_pause'):
                self.btn_kiosk_pause.configure(text="▶  Tiếp tục quét")
            if hasattr(self, 'lbl_kiosk_cam_status'):
                self.lbl_kiosk_cam_status.configure(text="● Đã tạm dừng quét", text_color="#f59e0b")
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
        - Hiển thị nút 'Quét người tiếp theo'.
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
        
        self._is_kiosk_ui_idle = False
        
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
        
        self._is_kiosk_ui_idle = False
        
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
        print("[ATTENDANCE] set_idle_state start")
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
            
        self.is_recognizing = False
        
        # Tránh re-configure 15 widgets thừa thãi nếu UI vốn đã ở trạng thái IDLE
        if getattr(self, '_is_kiosk_ui_idle', False):
            print("[ATTENDANCE] set_idle_state end")
            return
            
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
        
        self._is_kiosk_ui_idle = True
        print("[KIOSK] Đã mở khóa nhận diện (self.is_recognizing = False) - Sẵn sàng quét người tiếp theo.")
        print("[ATTENDANCE] set_idle_state end")

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
            ctk_img = ctk.CTkImage(light_image=circ_img, dark_image=circ_img, size=size)
        else:
            if not hasattr(self, '_cached_default_avatar_ctk') or border_color != "#cbd5e1":
                circ_img = create_default_avatar(size=size, bg_color="#e2e8f0", border_color=border_color)
                temp_ctk = ctk.CTkImage(light_image=circ_img, dark_image=circ_img, size=size)
                if border_color == "#cbd5e1":
                    self._cached_default_avatar_ctk = temp_ctk
                ctk_img = temp_ctk
            else:
                ctk_img = self._cached_default_avatar_ctk
            
        self.kiosk_avatar_label.configure(image=ctk_img, text="")
        self.kiosk_avatar_label.image = ctk_img
