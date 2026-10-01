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
    CTK_CARD, CTK_ACCENT, CTK_TEXT, CTK_TEXT_DIM, CTK_PRIMARY, CTK_PRIMARY_HOVER,
    CTK_SUCCESS, CTK_DANGER, CTK_SIDEBAR_HOVER, CTK_BG_MAIN, ADMIN_CAMERA_FPS_DELAY,
    DEEPFACE_MODEL_NAME, ARCFACE_THRESHOLD, KIOSK_MIN_FACE_WIDTH,
    KIOSK_FACE_STABLE_SECONDS, KIOSK_FACE_LEAVE_SECONDS,
    KIOSK_ATTENDANCE_COOLDOWN_SECONDS, KIOSK_UNKNOWN_CONFIRMATIONS,
    KIOSK_DETECTION_INTERVAL_SECONDS,
)
from ai_engine import (
    detect_faces, calculate_cosine_distance, draw_kiosk_face_box,
    align_face_crop, validate_kiosk_face_candidate,
)
from .ui_helpers import (
    make_circular_avatar,
    create_default_avatar,
)


class AttendanceMixin:
    """Mixin quản lý toàn bộ giao diện và nghiệp vụ nhận diện điểm danh (Kiosk Mode)."""

    def _build_attendance_page(self):
        """
        Xây dựng giao diện Điểm danh chuẩn Enterprise HR:
        - Layout tinh gọn, loại bỏ card lồng nhau, màu sắc nhã nhặn.
        - Camera chiếm ~62% bề ngang, hiển thị rõ nét, không HUD/badge kỹ thuật.
        - Panel kết quả bên phải chiếm ~38%, thông tin rõ ràng, không khối xanh lá choán màn hình.
        - Bảng lịch sử điểm danh bên dưới dạng data table phẳng chuẩn doanh nghiệp.
        """
        print("[ATTENDANCE] page create start")
        self.attendance_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)
        self.attendance_frame.grid_columnconfigure(0, weight=1)
        self.attendance_frame.grid_rowconfigure(0, weight=0)
        self.attendance_frame.grid_rowconfigure(1, weight=1)

        # ==========================================
        # TOP ROW: CAMERA (Trái ~62%) & RESULT PANEL (Phải ~38%)
        # ==========================================
        top_row = ctk.CTkFrame(self.attendance_frame, fg_color="transparent")
        top_row.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        # Camera là nội dung chính: ưu tiên 60% chiều ngang, panel kết quả 40%.
        # Tỷ lệ này vẫn đủ chỗ cho thông tin nhân viên ở độ rộng cửa sổ tối thiểu.
        top_row.grid_columnconfigure(0, weight=60, uniform="top_cards")
        top_row.grid_columnconfigure(1, weight=40, uniform="top_cards")
        
        # ------------------------------------------
        # 1. CỘT TRÁI: CAMERA VÀ KHUNG HƯỚNG DẪN
        # ------------------------------------------
        cam_card = ctk.CTkFrame(
            top_row, fg_color=CTK_CARD, corner_radius=8,
            border_width=1, border_color=CTK_ACCENT
        )
        cam_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        
        # Khung hiển thị Video (chuẩn tỉ lệ 4:3, theme-aware, thon gọn chiều ngang)
        cam_display_box = ctk.CTkFrame(cam_card, fg_color=("#F1F5F9", "#0F172A"), corner_radius=6, height=350)
        cam_display_box.pack(fill="both", expand=True, padx=12, pady=12)
        cam_display_box.pack_propagate(False)
        
        # Video feed label
        self.kiosk_camera_label = ctk.CTkLabel(
            cam_display_box, text="",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
            fg_color="transparent"
        )
        self.kiosk_camera_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        
        # Camera Loading Overlay (Theme-aware, Clean & Minimal)
        print("[ATTENDANCE] placeholder start")
        self.kiosk_cam_overlay = ctk.CTkFrame(
            cam_display_box, fg_color=("#F8FAFC", "#0F172A"), corner_radius=6
        )
        overlay_content = ctk.CTkFrame(self.kiosk_cam_overlay, fg_color="transparent")
        overlay_content.place(relx=0.5, rely=0.5, anchor="center")
        
        cam_icon_badge = ctk.CTkFrame(
            overlay_content, width=42, height=42, corner_radius=21,
            fg_color=("#EFF6FF", "#1E293B"), border_width=1, border_color=("#DBEAFE", "#334155")
        )
        cam_icon_badge.pack(pady=(0, 10))
        cam_icon_badge.pack_propagate(False)
        
        self.kiosk_cam_spinner = ctk.CTkLabel(
            cam_icon_badge, text="📷",
            font=ctk.CTkFont(size=18), text_color=CTK_PRIMARY
        )
        self.kiosk_cam_spinner.place(relx=0.5, rely=0.5, anchor="center")
        
        self.kiosk_cam_overlay_text = ctk.CTkLabel(
            overlay_content, text="Đang khởi động camera...",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=CTK_TEXT
        )
        self.kiosk_cam_overlay_text.pack(pady=(0, 2))
        
        self.kiosk_cam_overlay_sub = ctk.CTkLabel(
            overlay_content, text="Vui lòng chờ trong giây lát",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM
        )
        self.kiosk_cam_overlay_sub.pack()
        
        if getattr(self, 'kiosk_latest_frame', None) is None:
            self.kiosk_cam_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.kiosk_cam_overlay.lift()
            self._kiosk_loading_state_visible = True
            print("[ATTENDANCE] loading state shown")
        print("[ATTENDANCE] placeholder end")
        
        # Dummy widgets (ẩn, không hiển thị lên giao diện) để tương thích mã nguồn backend
        self.lbl_kiosk_cam_dot = ctk.CTkLabel(cam_card, text="")
        self.lbl_kiosk_cam_status = ctk.CTkLabel(cam_card, text="")
        self.lbl_kiosk_fps = ctk.CTkLabel(cam_card, text="")
        self.kiosk_status_title = ctk.CTkLabel(cam_card, text="")
        self.kiosk_status_desc = ctk.CTkLabel(cam_card, text="")
        self.kiosk_status_time = ctk.CTkLabel(cam_card, text="")
        self.kiosk_guide_icon = ctk.CTkLabel(cam_card, text="")

        # ------------------------------------------
        # 2. CỘT PHẢI: KẾT QUẢ ĐIỂM DANH (ENTERPRISE PANEL)
        # ------------------------------------------
        self.kiosk_result_card = ctk.CTkFrame(
            top_row, fg_color=CTK_CARD, corner_radius=8,
            border_width=1, border_color=CTK_ACCENT
        )
        self.kiosk_result_card.grid(row=0, column=1, sticky="nsew")
        
        # A. Status Header (Khớp 100% Mockup: Icon tròn xanh lá tick trắng ✓ + text trạng thái)
        self.kiosk_res_banner_frame = ctk.CTkFrame(
            self.kiosk_result_card, fg_color="transparent"
        )
        self.kiosk_res_banner_frame.pack(fill="x", padx=22, pady=(18, 14))
        
        self.kiosk_res_icon = ctk.CTkLabel(
            self.kiosk_res_banner_frame, text="✓",
            font=ctk.CTkFont(size=18, weight="bold"),
            width=36, height=36, corner_radius=18,
            fg_color=("#16A34A", "#16A34A"), text_color="#FFFFFF"
        )
        self.kiosk_res_icon.pack(side="left", padx=(0, 14), anchor="center")
        
        banner_text_col = ctk.CTkFrame(self.kiosk_res_banner_frame, fg_color="transparent")
        banner_text_col.pack(side="left", fill="both", expand=True)
        
        self.kiosk_res_title = ctk.CTkLabel(
            banner_text_col, text="Điểm danh thành công",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#15803D", "#22C55E"), anchor="w"
        )
        self.kiosk_res_title.pack(fill="x", pady=(0, 2))
        
        self.kiosk_res_sub = ctk.CTkLabel(
            banner_text_col, text="Thời gian vào: Chờ quét",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.kiosk_res_sub.pack(fill="x")
        
        # Divider 1 (Phân cách nhẹ giữa Status và Thông tin nhân sự)
        self.kiosk_div_1 = ctk.CTkFrame(self.kiosk_result_card, height=1, fg_color=CTK_ACCENT)
        self.kiosk_div_1.pack(fill="x", padx=22, pady=0)
        
        # B. Thông tin Nhân sự (Avatar tròn 80x80 + Họ tên lớn + Mã NV + Chức vụ / Phòng ban)
        profile_section = ctk.CTkFrame(self.kiosk_result_card, fg_color="transparent")
        profile_section.pack(fill="x", padx=22, pady=(16, 16))
        
        # Avatar (Tròn 80x80 với viền sáng thanh lịch)
        self.kiosk_avatar_label = ctk.CTkLabel(profile_section, text="", width=80, height=80)
        self.kiosk_avatar_label.pack(side="left", padx=(0, 16), anchor="center")
        self._set_kiosk_avatar(None, None, border_color="#E2E8F0", size=(80, 80))
        
        info_col = ctk.CTkFrame(profile_section, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)
        
        self.kiosk_name_label = ctk.CTkLabel(
            info_col, text="Chưa có lượt quét",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.kiosk_name_label.pack(fill="x", pady=(0, 3))
        
        # Mã NV: Label "Mã NV: " thường và mã NV đậm
        id_row = ctk.CTkFrame(info_col, fg_color="transparent")
        id_row.pack(fill="x", pady=(0, 4))
        
        self.kiosk_id_title = ctk.CTkLabel(
            id_row, text="Mã NV: ",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM,
            anchor="w"
        )
        self.kiosk_id_title.pack(side="left")
        
        self.kiosk_id_badge = ctk.CTkLabel(
            id_row, text="---",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=CTK_TEXT,
            anchor="w"
        )
        self.kiosk_id_badge.pack(side="left")
        
        self.kiosk_role_label = ctk.CTkLabel(
            info_col, text="💼  Chức vụ: ---",
            font=ctk.CTkFont(size=12), text_color=("#4B5563", "#94A3B8"), anchor="w"
        )
        self.kiosk_role_label.pack(fill="x", pady=1)
        
        self.kiosk_dept_label = ctk.CTkLabel(
            info_col, text="🏢  Phòng ban: ---",
            font=ctk.CTkFont(size=12), text_color=("#4B5563", "#94A3B8"), anchor="w"
        )
        self.kiosk_dept_label.pack(fill="x")
        
        # C. Thông điệp / Lời chào / Thông báo hệ thống
        self.kiosk_notice_container = ctk.CTkFrame(
            self.kiosk_result_card, fg_color="transparent"
        )
        self.kiosk_notice_container.pack(fill="x", padx=22, pady=(14, 14))

        # C1. Normal Greeting Box (dùng khi Success hoặc Idle: hiển thị phẳng không viền)
        self.kiosk_greeting_card = ctk.CTkFrame(self.kiosk_notice_container, fg_color="transparent")
        self.kiosk_greeting_card.pack(fill="x")
        
        self.kiosk_greeting_icon = ctk.CTkLabel(self.kiosk_greeting_card, text="", width=0)
        self.kiosk_greeting_icon.pack_forget()

        self.kiosk_greeting_title = ctk.CTkLabel(
            self.kiosk_greeting_card,
            text="Vui lòng đứng thẳng, nhìn vào camera để điểm danh.",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=CTK_TEXT,
            anchor="w"
        )
        self.kiosk_greeting_title.pack(fill="x", pady=(0, 2))

        self.kiosk_greeting_text = ctk.CTkLabel(
            self.kiosk_greeting_card,
            text="Chúc bạn một ngày làm việc hiệu quả.",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM,
            anchor="w"
        )
        self.kiosk_greeting_text.pack(fill="x")

        # C2. Warning / Unknown Notice Box (khớp 100% hình ảnh mẫu khi Chưa nhận diện được)
        self.kiosk_warning_box = ctk.CTkFrame(
            self.kiosk_notice_container,
            fg_color=("#F8FAFC", "#111C2E"),
            border_width=1, border_color=("#E2E8F0", "#1E293B"),
            corner_radius=8
        )

        warn_content = ctk.CTkFrame(self.kiosk_warning_box, fg_color="transparent")
        warn_content.pack(fill="x", padx=12, pady=10)
        
        warn_info_icon = ctk.CTkLabel(
            warn_content, text="ⓘ",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#2563EB", "#3B82F6"), width=24
        )
        warn_info_icon.pack(side="left", padx=(0, 8), anchor="center")

        warn_v_divider = ctk.CTkFrame(warn_content, width=1, height=28, fg_color=("#DBEAFE", "#334155"))
        warn_v_divider.pack(side="left", padx=(0, 10), fill="y")

        warn_text_col = ctk.CTkFrame(warn_content, fg_color="transparent")
        warn_text_col.pack(side="left", fill="both", expand=True)

        self.kiosk_warning_line1 = ctk.CTkLabel(
            warn_text_col, text="Khuôn mặt chưa có trong hệ thống dữ liệu.",
            font=ctk.CTkFont(size=12), text_color=("#4B5563", "#94A3B8"), anchor="w"
        )
        self.kiosk_warning_line1.pack(fill="x")

        self.kiosk_warning_line2 = ctk.CTkLabel(
            warn_text_col, text="Vui lòng đăng ký trước khi điểm danh.",
            font=ctk.CTkFont(size=12), text_color=("#4B5563", "#94A3B8"), anchor="w"
        )
        self.kiosk_warning_line2.pack(fill="x")

        # Divider 3 (Phân cách nhẹ giữa Lời chào và Thời gian)
        self.kiosk_div_3 = ctk.CTkFrame(self.kiosk_result_card, height=1, fg_color=CTK_ACCENT)
        self.kiosk_div_3.pack(fill="x", padx=22, pady=0)
        
        # D. Thời gian (Icon đồng hồ + 2 dòng text)
        time_section = ctk.CTkFrame(self.kiosk_result_card, fg_color="transparent")
        time_section.pack(fill="x", padx=22, pady=(14, 16))
        
        self.kiosk_time_icon = ctk.CTkLabel(
            time_section, text="🕒",
            font=ctk.CTkFont(size=22), text_color=CTK_TEXT_DIM, width=28
        )
        self.kiosk_time_icon.pack(side="left", padx=(0, 12), anchor="center")
        
        time_col = ctk.CTkFrame(time_section, fg_color="transparent")
        time_col.pack(side="left", fill="both", expand=True)
        
        time_head_lbl = ctk.CTkLabel(
            time_col, text="Thời gian điểm danh",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
        )
        time_head_lbl.pack(fill="x", pady=(0, 2))
        
        self.kiosk_time_label = ctk.CTkLabel(
            time_col, text="Chờ quét...",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=CTK_TEXT, anchor="w"
        )
        self.kiosk_time_label.pack(fill="x")
        
        self.kiosk_saved_badge = ctk.CTkLabel(
            time_section, text=""
        )
        self.kiosk_saved_badge.pack_forget()

        # E. Nút chỉ dùng để thử lại khi nhận diện thất bại.
        self.btn_scan_next = ctk.CTkButton(
            self.kiosk_result_card,
            text="↻   Quét lại",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            corner_radius=8,
            fg_color=("#2563EB", "#2563EB"),
            hover_color=("#1D4ED8", "#1D4ED8"),
            text_color="#ffffff",
            command=self._reset_for_next_scan
        )
        self.btn_scan_next.pack_forget()

        # Trạng thái tự động thay cho nút thao tác ở luồng bình thường.
        self.kiosk_auto_status = ctk.CTkFrame(
            self.kiosk_result_card,
            height=42,
            corner_radius=8,
            fg_color=("#F8FAFC", "#111827"),
            border_width=1,
            border_color=("#E2E8F0", "#334155"),
        )
        self.kiosk_auto_status.pack(fill="x", padx=22, pady=(0, 18))
        self.kiosk_auto_status.pack_propagate(False)
        self.kiosk_auto_status_label = ctk.CTkLabel(
            self.kiosk_auto_status,
            text="●  Tự động nhận diện đang hoạt động",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#15803D", "#4ADE80"),
        )
        self.kiosk_auto_status_label.place(relx=0.5, rely=0.5, anchor="center")

        # Nút bấm phụ: "+ Đăng ký nhân viên này" (Khớp 100% Mockup: Viền xanh lá, nền trắng, chữ xanh lá)
        self.btn_kiosk_register = ctk.CTkButton(
            self.kiosk_result_card,
            text="+   Đăng ký nhân viên này",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=42,
            corner_radius=8,
            fg_color=("#FFFFFF", "#1E293B"),
            border_width=1,
            border_color=("#10B981", "#059669"),
            hover_color=("#ECFDF5", "#064E3B"),
            text_color=("#059669", "#10B981"),
            command=lambda: self._navigate("add_employee")
        )
        self.btn_kiosk_register.pack_forget()

        # ==========================================
        # BOTTOM ROW: BẢNG LỊCH SỬ ĐIỂM DANH HÔM NAY
        # ==========================================
        history_card = ctk.CTkFrame(
            self.attendance_frame, fg_color=CTK_CARD, corner_radius=8,
            border_width=1, border_color=CTK_ACCENT
        )
        history_card.grid(row=1, column=0, sticky="nsew")
        history_card.grid_columnconfigure(0, weight=1)
        history_card.grid_rowconfigure(2, weight=1)
        
        # Header Lịch sử
        hist_header = ctk.CTkFrame(history_card, fg_color="transparent")
        hist_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(10, 6))
        
        ctk.CTkLabel(
            hist_header, text="🕒 Lịch sử điểm danh hôm nay",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=CTK_TEXT, anchor="w"
        ).pack(side="left")
        
        btn_view_all = ctk.CTkButton(
            hist_header, text="Xem tất cả →",
            font=ctk.CTkFont(size=11, weight="bold"), height=26,
            corner_radius=6,
            fg_color="transparent", hover_color=CTK_SIDEBAR_HOVER,
            text_color=CTK_PRIMARY, command=lambda: self._navigate("history")
        )
        btn_view_all.pack(side="right")
        
        # Table Header Row (Căn chỉnh theo tỉ lệ cột chuẩn doanh nghiệp)
        tbl_head = ctk.CTkFrame(
            history_card, height=30, corner_radius=0,
            fg_color=("#F8FAFC", "#111C2E")
        )
        tbl_head.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 2))
        tbl_head.pack_propagate(False)
        
        self._kiosk_col_defs = [
            ("Thời gian", 18),
            ("Nhân viên", 20),
            ("Mã NV", 12),
            ("Chức vụ", 20),
            ("Phòng ban", 16),
            ("Trạng thái", 14),
        ]
        
        for col_idx, (name, w) in enumerate(self._kiosk_col_defs):
            tbl_head.grid_columnconfigure(
                col_idx, weight=w, uniform="kiosk_history_column"
            )
            lbl = ctk.CTkLabel(
                tbl_head, text=name, font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_TEXT_DIM, anchor="w"
            )
            lbl.grid(row=0, column=col_idx, sticky="w", padx=10, pady=4)
            
        # Table Dynamic Rows Container
        self.kiosk_history_rows_container = ctk.CTkFrame(history_card, fg_color="transparent")
        self.kiosk_history_rows_container.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 6))

        self._render_recent_attendance_table()
        
        print("[ATTENDANCE] page create end")
        return self.attendance_frame

    def _render_recent_attendance_table(self):
        """Vẽ tối đa 5 lượt điểm danh mới nhất."""
        if not hasattr(self, 'kiosk_history_rows_container'):
            return

        first_record = self.attendance_history[0] if self.attendance_history else {}
        render_signature = (
            getattr(self, '_attendance_history_version', 0),
            len(self.attendance_history),
            first_record.get("time"),
            first_record.get("id"),
        )
        if render_signature == getattr(self, '_recent_history_render_signature', None):
            return
        self._recent_history_render_signature = render_signature

        for child in self.kiosk_history_rows_container.winfo_children():
            child.destroy()
            
        total_items = len(self.attendance_history)
        if not total_items:
            empty_lbl = ctk.CTkLabel(
                self.kiosk_history_rows_container,
                text="Chưa có lượt điểm danh nào",
                font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM
            )
            empty_lbl.pack(pady=10)
            return

        recent_items = self.attendance_history[:5]

        col_defs = getattr(self, '_kiosk_col_defs', [
            ("Thời gian", 18),
            ("Nhân viên", 20),
            ("Mã NV", 12),
            ("Chức vụ", 20),
            ("Phòng ban", 16),
            ("Trạng thái", 14),
        ])

        for i, item in enumerate(recent_items):
            bg = ("#ffffff", "#0d1522") if i % 2 == 0 else ("#f8fafc", "#111c2e")
            row = ctk.CTkFrame(
                self.kiosk_history_rows_container, height=32, corner_radius=0,
                fg_color=bg
            )
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            for c_idx, (_, w) in enumerate(col_defs):
                row.grid_columnconfigure(
                    c_idx, weight=w, uniform="kiosk_history_column"
                )
            
            # Thời gian
            ctk.CTkLabel(
                row, text=item.get("time", ""),
                font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=10, pady=4)
            
            # Nhân viên
            ctk.CTkLabel(
                row, text=item.get("name", ""),
                font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=10, pady=4)
            
            # Mã NV
            ctk.CTkLabel(
                row, text=item.get("id", ""),
                font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
            ).grid(row=0, column=2, sticky="w", padx=10, pady=4)
            
            # Chức vụ
            ctk.CTkLabel(
                row, text=item.get("role", ""),
                font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
            ).grid(row=0, column=3, sticky="w", padx=10, pady=4)
            
            # Phòng ban
            ctk.CTkLabel(
                row, text=item.get("dept", "Phòng Ban"),
                font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM, anchor="w"
            ).grid(row=0, column=4, sticky="w", padx=10, pady=4)
            
            # Trạng thái: Dạng text phẳng với chấm tròn, KHÔNG dùng pill background
            is_success = "Thành công" in item.get("status", "")
            status_color = ("#16a34a", "#22c55e") if is_success else ("#dc2626", "#ef4444")
            icon = "● " if is_success else "✕ "
            
            b = ctk.CTkLabel(
                row, text=icon + item.get("status", "Thành công"),
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="transparent", text_color=status_color
            )
            b.grid(row=0, column=5, sticky="w", padx=10, pady=4)

    def _add_attendance_record(self, record):
        """Thêm bản ghi điểm danh mới và cập nhật cả 2 bảng (Recent & Full History)."""
        self.attendance_history.insert(0, record)
        self._attendance_history_version = getattr(self, '_attendance_history_version', 0) + 1
        if len(self.attendance_history) > 100:
            self.attendance_history.pop()
        self._render_recent_attendance_table()
        if hasattr(self, '_reload_full_history'):
            self._reload_full_history()
        if hasattr(self, '_save_attendance_history'):
            self._save_attendance_history()
        if hasattr(self, '_reload_dashboard_stats'):
            self._reload_dashboard_stats()

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
        last_detection_at = 0.0
        cached_faces = []

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
            
            # 1. Giới hạn tần suất YOLO; camera vẫn cập nhật mượt bằng kết quả gần nhất.
            detection_now = time.perf_counter()
            if detection_now - last_detection_at >= KIOSK_DETECTION_INTERVAL_SECONDS:
                cached_faces = detect_faces(self.face_model, frame, self.device)
                last_detection_at = detection_now
            faces = cached_faces
            self.kiosk_face_count = len(faces)
            
            # 2. Vẽ khung góc nhẹ nhàng bám theo khuôn mặt (Xanh neon chuẩn Mockup)
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                draw_kiosk_face_box(frame, fx1, fy1, fx2, fy2, color=(100, 255, 100), thickness=3)
                
            curr_now = time.time()
            if len(faces) > 0:
                self._kiosk_last_face_ts = curr_now
                self._kiosk_face_absent_since = None
                if len(faces) == 1 and self._kiosk_face_stable_since is None:
                    self._kiosk_face_stable_since = curr_now
                elif len(faces) > 1:
                    self._kiosk_face_stable_since = None
                    if curr_now - getattr(self, '_last_multi_face_notice_ts', 0) >= 1.0:
                        self._last_multi_face_notice_ts = curr_now
                        self._safe_after(0, self._show_multiple_faces_notice)
            else:
                self._kiosk_face_stable_since = None
                if self._kiosk_face_absent_since is None:
                    self._kiosk_face_absent_since = curr_now

                # Chỉ mở lượt mới sau khi người hiện tại thực sự rời khỏi khung hình.
                face_absent_for = curr_now - self._kiosk_face_absent_since
                if (
                    face_absent_for >= KIOSK_FACE_LEAVE_SECONDS
                    and not self.is_recognizing
                    and not getattr(self, '_kiosk_idle_reset_pending', False)
                    and (
                        getattr(self, '_kiosk_waiting_for_departure', False)
                        or not getattr(self, '_is_kiosk_ui_idle', True)
                    )
                ):
                    self._kiosk_waiting_for_departure = False
                    self._kiosk_idle_reset_pending = True
                    self._safe_after(0, self.set_idle_state)

            # 3. Cơ chế kích hoạt Nhận diện Tự Động (Auto Inference Trigger):
            face_stable_for = (
                curr_now - self._kiosk_face_stable_since
                if self._kiosk_face_stable_since is not None else 0.0
            )
            if (
                len(faces) == 1
                and not self.is_recognizing
                and not getattr(self, '_kiosk_waiting_for_departure', False)
                and face_stable_for >= KIOSK_FACE_STABLE_SECONDS
            ):
                best_face = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
                fx1, fy1, fx2, fy2, fconf = best_face
                face_w = fx2 - fx1
                face_h = fy2 - fy1
                
                if face_w < KIOSK_MIN_FACE_WIDTH:
                    self._kiosk_face_stable_since = curr_now
                    self._safe_after(
                        0, self._on_kiosk_face_quality_rejected,
                        "Vui lòng di chuyển lại gần camera"
                    )
                else:
                    is_valid_face, quality_message = validate_kiosk_face_candidate(
                        (fx1, fy1, fx2, fy2),
                        fconf,
                        clean_frame,
                        getattr(self, 'face_detector', None),
                    )
                    if not is_valid_face:
                        self._kiosk_face_stable_since = curr_now
                        self._safe_after(
                            0, self._on_kiosk_face_quality_rejected, quality_message
                        )
                        continue

                    pad_x = int(face_w * 0.20)
                    pad_y = int(face_h * 0.20)
                    cx1 = max(0, fx1 - pad_x)
                    cy1 = max(0, fy1 - pad_y)
                    cx2 = min(fw, fx2 + pad_x)
                    cy2 = min(fh, fy2 + pad_y)
                    
                    if cx2 > cx1 and cy2 > cy1:
                        cropped_img = clean_frame[cy1:cy2, cx1:cx2].copy()
                        # Đặt cờ self.is_recognizing = True để bỏ qua các frame tiếp theo
                        self.is_recognizing = True
                        self._kiosk_face_stable_since = None
                        
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
                is_dark = ctk.get_appearance_mode().lower() == "dark"
                bg_bgr = (42, 23, 15) if is_dark else (249, 245, 241)
                canvas = np.full((lbl_h, lbl_w, 3), bg_bgr, dtype=np.uint8)
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
                if not getattr(self, '_first_valid_frame_logged', False):
                    print("[ATTENDANCE] first valid frame received")
                    self._first_valid_frame_logged = True
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
            self.kiosk_cam_overlay.configure(fg_color=("#F8FAFC", "#0F172A"))
            if hasattr(self, 'kiosk_cam_overlay_text'):
                self.kiosk_cam_overlay_text.configure(text=text, text_color=CTK_TEXT)
            if hasattr(self, 'kiosk_cam_overlay_sub'):
                self.kiosk_cam_overlay_sub.configure(text_color=CTK_TEXT_DIM)
            self.kiosk_cam_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.kiosk_cam_overlay.lift()
            if not getattr(self, '_kiosk_loading_state_visible', False):
                self._kiosk_loading_state_visible = True
                print("[ATTENDANCE] loading state shown")
            if hasattr(self, 'lbl_kiosk_cam_status'):
                self.lbl_kiosk_cam_status.configure(text="Đang khởi động camera...", text_color=CTK_TEXT)
            if hasattr(self, 'lbl_kiosk_cam_dot'):
                self.lbl_kiosk_cam_dot.configure(text="◌ ", text_color=CTK_PRIMARY)

    def _hide_kiosk_cam_loading(self):
        """Ẩn overlay loading khi frame camera đầu tiên đã sẵn sàng."""
        if hasattr(self, 'kiosk_cam_overlay') and self.kiosk_cam_overlay.winfo_exists():
            self.kiosk_cam_overlay.place_forget()
            if getattr(self, '_kiosk_loading_state_visible', False):
                self._kiosk_loading_state_visible = False
                print("[ATTENDANCE] loading state hidden")
            if hasattr(self, 'lbl_kiosk_cam_status'):
                self.lbl_kiosk_cam_status.configure(text="Camera đang hoạt động", text_color=CTK_TEXT)
            if hasattr(self, 'lbl_kiosk_cam_dot'):
                self.lbl_kiosk_cam_dot.configure(text="● ", text_color=CTK_SUCCESS)

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
        """Cập nhật trạng thái khi bắt đầu nhận diện."""
        self.kiosk_status_title.configure(text="● Đang nhận diện khuôn mặt...")
        self.kiosk_status_desc.configure(text="Đang đối chiếu dữ liệu nhân sự...")
        self._show_auto_scan_status("◌  Đang xác minh khuôn mặt...", CTK_PRIMARY)

    def _show_multiple_faces_notice(self):
        """Thông báo rõ khi có nhiều hơn một khuôn mặt trong camera."""
        if self.is_recognizing or getattr(self, '_kiosk_waiting_for_departure', False):
            return
        self._kiosk_unknown_attempts = 0
        self._is_kiosk_ui_idle = False
        self.kiosk_res_title.configure(text="Có nhiều khuôn mặt", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(
            text="Vui lòng chỉ để một người trong khung hình", text_color=CTK_TEXT_DIM
        )
        self._show_auto_scan_status(
            "●  Đang chờ một khuôn mặt", ("#B45309", "#FBBF24")
        )

    def _on_kiosk_face_quality_rejected(self, message):
        """Hướng dẫn căn mặt; không gắn nhãn người lạ khi đầu vào chưa hợp lệ."""
        if self.is_recognizing or getattr(self, '_kiosk_waiting_for_departure', False):
            return
        self._kiosk_unknown_attempts = 0
        self._is_kiosk_ui_idle = False
        self.kiosk_res_title.configure(text="Căn chỉnh khuôn mặt", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(text=message, text_color=CTK_TEXT_DIM)
        self._show_auto_scan_status(
            "●  Chưa đủ điều kiện nhận diện", ("#B45309", "#FBBF24")
        )

    def _on_kiosk_unknown_confirmation_pending(self, attempt):
        """Thông báo hệ thống đang xác minh lại trước khi kết luận người lạ."""
        self._is_kiosk_ui_idle = False
        self.kiosk_res_title.configure(text="Đang xác minh lại", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(
            text=f"Lần kiểm tra {attempt}/{KIOSK_UNKNOWN_CONFIRMATIONS}",
            text_color=CTK_TEXT_DIM,
        )
        self._show_auto_scan_status("◌  Vui lòng giữ nguyên khuôn mặt", CTK_PRIMARY)

    def _show_auto_scan_status(self, text, color=("#15803D", "#4ADE80")):
        """Hiển thị trạng thái của cơ chế quét tự động ở cuối panel kết quả."""
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
        if hasattr(self, 'kiosk_auto_status'):
            self.kiosk_auto_status.pack(fill="x", padx=22, pady=(0, 18))
            self.kiosk_auto_status_label.configure(text=text, text_color=color)

    def _show_retry_actions(self):
        """Chỉ hiện nút Quét lại khi lần nhận diện vừa thất bại."""
        if hasattr(self, 'kiosk_auto_status'):
            self.kiosk_auto_status.pack_forget()
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.configure(text="↻   Quét lại")
            self.btn_scan_next.pack(fill="x", padx=22, pady=(0, 8))

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
            
            # 1. Tự động Căn chỉnh xoay thẳng mặt (Face Alignment) dựa vào 2 mắt
            detector = getattr(self, 'face_detector', None)
            aligned_img, tilt_angle = align_face_crop(cropped_img, detector)
            if abs(tilt_angle) > 2.0:
                print(f"[KIOSK AI] Face Alignment: Đã căn chỉnh xoay mặt {tilt_angle:.1f}° về thẳng")
            
            # 2. Trích xuất Vector khuôn mặt qua ArcFace trên ảnh đã căn chỉnh
            reps = DeepFace.represent(
                img_path=aligned_img,
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
                self._kiosk_unknown_attempts = 0
                self._safe_after(0, self._on_kiosk_recognition_success, best_emp, min_dist)
            else:
                self._kiosk_unknown_attempts = getattr(self, '_kiosk_unknown_attempts', 0) + 1
                if self._kiosk_unknown_attempts < KIOSK_UNKNOWN_CONFIRMATIONS:
                    attempt = self._kiosk_unknown_attempts
                    self.is_recognizing = False
                    self._kiosk_face_stable_since = time.time()
                    self._safe_after(0, self._on_kiosk_unknown_confirmation_pending, attempt)
                else:
                    self._kiosk_unknown_attempts = 0
                    self._safe_after(0, self._on_kiosk_recognition_failed, min_dist)
                
        except Exception as e:
            print(f"[KIOSK AI ERROR]: Lỗi nhận diện: {e}")
            self.is_recognizing = False
            self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)

    def _on_kiosk_recognition_success(self, emp_info, distance):
        """
        Cập nhật Giao diện Cột Phải khi THÀNH CÔNG:
        - Đổi ảnh Avatar sang ảnh của nhân viên đó (load từ database/images/).
        - Cập nhật text Họ Tên, Mã NV, Chức vụ, Phòng ban.
        - Đổi Status Header sang thành công (icon tròn xanh lá ✓, text xanh).
        - Chờ khuôn mặt rời khung rồi tự động mở lượt tiếp theo.
        """
        name = emp_info.get("name", "Nhân viên")
        emp_id = emp_info.get("id", "NV---")
        role_full = emp_info.get("role", "Nhân viên")

        parts = role_full.split("-") if "-" in role_full else role_full.split("–")
        role_name = parts[0].strip()
        dept_name = parts[1].strip() if len(parts) > 1 else "Phòng IT"

        now = datetime.now()
        now_str = now.strftime("%H:%M:%S · %d/%m/%Y")
        now_short = now.strftime("%H:%M:%S %d/%m/%Y")
        now_ts = time.time()

        cooldowns = getattr(self, '_attendance_cooldowns', {})
        previous = cooldowns.get(emp_id)
        is_duplicate = bool(
            previous and now_ts - previous[0] < KIOSK_ATTENDANCE_COOLDOWN_SECONDS
        )
        
        self._is_kiosk_ui_idle = False
        
        # 1. Status Header (Khớp 100% Mockup: icon tròn xanh lá tick trắng ✓)
        self.kiosk_res_banner_frame.configure(fg_color="transparent")
        self.kiosk_res_icon.configure(
            text="✓", fg_color=("#16A34A", "#16A34A"), text_color="#FFFFFF"
        )
        if is_duplicate:
            self.kiosk_res_title.configure(text="Đã điểm danh trước đó", text_color=("#B45309", "#FBBF24"))
            self.kiosk_res_sub.configure(
                text=f"Lần gần nhất: {previous[1]}", text_color=CTK_TEXT_DIM
            )
        else:
            self.kiosk_res_title.configure(text="Điểm danh thành công", text_color=("#15803D", "#22C55E"))
            self.kiosk_res_sub.configure(text=f"Thời gian vào: {now_str}", text_color=CTK_TEXT_DIM)
        
        # 2. Cập nhật thông tin Nhân viên
        self.kiosk_name_label.configure(text=name, text_color=CTK_TEXT)
        if hasattr(self, 'kiosk_id_title'):
            self.kiosk_id_title.configure(text="Mã NV: ")
        self.kiosk_id_badge.configure(text=emp_id, text_color=CTK_TEXT)
        self.kiosk_role_label.configure(text=f"💼  Chức vụ: {role_name}")
        self.kiosk_dept_label.configure(text=f"🏢  Phòng ban: {dept_name}")
        
        # 3. Đổi ảnh Avatar sang ảnh nhân viên (80x80 với viền sáng thanh lịch)
        self._set_kiosk_avatar(emp_info.get("image_path"), emp_id, border_color="#E2E8F0", size=(80, 80))
        
        # 4. Lời chào mừng (ẩn warning box, hiện greeting card)
        if hasattr(self, 'kiosk_warning_box'):
            self.kiosk_warning_box.pack_forget()
        if hasattr(self, 'kiosk_greeting_card'):
            self.kiosk_greeting_card.pack(fill="x")
        if hasattr(self, 'kiosk_greeting_title'):
            self.kiosk_greeting_title.configure(text=f"Chào mừng {name}.", text_color=CTK_TEXT)
        self.kiosk_greeting_text.configure(
            text="Chúc bạn một ngày làm việc hiệu quả.",
            text_color=CTK_TEXT_DIM
        )
        
        # 5. Cập nhật thời gian điểm danh
        self.kiosk_time_label.configure(text=now_str)
        if hasattr(self, 'kiosk_saved_badge'):
            self.kiosk_saved_badge.pack_forget()
        
        # 6. Cập nhật banner dưới Camera (Đã ghi nhận + timestamp)
        self.kiosk_status_title.configure(text=f"Đã ghi nhận: {name} ({emp_id})")
        self.kiosk_status_desc.configure(text="Điểm danh thành công vào hệ thống")
        if hasattr(self, 'kiosk_status_time'):
            self.kiosk_status_time.configure(text=now_short)
        
        # 7. Chỉ ghi một lượt cho mỗi nhân viên trong thời gian cooldown.
        if not is_duplicate:
            cooldowns[emp_id] = (now_ts, now_str)
            self._attendance_cooldowns = cooldowns
            self._add_attendance_record({
                "time": now_short,
                "name": name,
                "id": emp_id,
                "role": role_name,
                "dept": dept_name,
                "status": "Thành công"
            })

        # 8. Không cần thao tác thủ công; chờ người hiện tại rời khung hình.
        if hasattr(self, 'btn_kiosk_register'):
            self.btn_kiosk_register.pack_forget()
        status_text = (
            "●  Đã ghi nhận trước đó · Vui lòng rời khung hình"
            if is_duplicate else
            "●  Đã ghi nhận · Vui lòng rời khung hình"
        )
        self._show_auto_scan_status(status_text)
        self._kiosk_waiting_for_departure = True
        self.is_recognizing = False

    def _on_kiosk_recognition_failed(self, distance):
        """
        Cập nhật Giao diện Cột Phải khi THẤT BẠI:
        - Khớp 100% Mockup Chưa nhận diện được:
          + Icon tròn nền hồng/đỏ nhạt với dấu ✕ đỏ.
          + Tiêu đề: 'Chưa nhận diện được'
          + Phụ đề: 'Khuôn mặt chưa có trong hệ thống'
          + Tên: 'Người lạ / Khách'
          + Mã NV: '🪪  Mã NV: —'
          + Hộp thông báo màu xanh nhạt có icon ⓘ
          + Nút Quét lại và nút đăng ký nhân viên.
        - Người dùng có thể thử lại ngay hoặc rời khung để hệ thống tự mở lượt mới.
        """
        now = datetime.now()
        now_str = now.strftime("%H:%M:%S · %d/%m/%Y")
        now_short = now.strftime("%H:%M:%S %d/%m/%Y")
        
        self._is_kiosk_ui_idle = False
        
        # 1. Đổi Status Header sang cảnh báo (Khớp 100% Mockup: icon tròn nền hồng nhạt, dấu ✕ đỏ)
        self.kiosk_res_banner_frame.configure(fg_color="transparent")
        self.kiosk_res_icon.configure(
            text="✕", fg_color=("#FEE2E2", "#450A0A"), text_color=("#EF4444", "#F87171")
        )
        self.kiosk_res_title.configure(text="Chưa nhận diện được", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(text="Khuôn mặt chưa có trong hệ thống", text_color=CTK_TEXT_DIM)
        
        # 2. Cập nhật thông tin Người lạ
        self.kiosk_name_label.configure(text="Người lạ / Khách", text_color=CTK_TEXT)
        if hasattr(self, 'kiosk_id_title'):
            self.kiosk_id_title.configure(text="🪪  Mã NV: ")
        self.kiosk_id_badge.configure(text="—", text_color=CTK_TEXT_DIM)
        self.kiosk_role_label.configure(text="💼  Chức vụ: Chưa đăng ký")
        self.kiosk_dept_label.configure(text="🏢  Phòng ban: Vui lòng liên hệ quản trị viên")
        
        # 3. Ảnh mặc định
        self._set_kiosk_avatar(None, None, border_color="#E2E8F0", size=(80, 80))
        
        # 4. Hiển thị hộp thông báo hệ thống có icon ⓘ (Khớp 100% Mockup)
        if hasattr(self, 'kiosk_greeting_card'):
            self.kiosk_greeting_card.pack_forget()
        if hasattr(self, 'kiosk_warning_box'):
            self.kiosk_warning_box.pack(fill="x")
        
        # 5. Thời gian
        self.kiosk_time_label.configure(text=now_str)
        if hasattr(self, 'kiosk_saved_badge'):
            self.kiosk_saved_badge.pack_forget()
        
        # 6. Banner dưới camera
        self.kiosk_status_title.configure(text="Phát hiện người lạ")
        self.kiosk_status_desc.configure(text="Khuôn mặt chưa được đăng ký trong hệ thống")
        if hasattr(self, 'kiosk_status_time'):
            self.kiosk_status_time.configure(text=now_short)
        
        # 7. Cho phép thử lại thủ công nếu cần; luồng bình thường vẫn hoàn toàn tự động.
        self._show_retry_actions()
        if hasattr(self, 'btn_kiosk_register'):
            self.btn_kiosk_register.pack(fill="x", padx=22, pady=(0, 18))
        self._kiosk_waiting_for_departure = True
        self.is_recognizing = False

    def _auto_reset_for_next_scan(self):
        """Tương thích ngược: mở lại quét tự động khi được mã cũ gọi tới."""
        self.kiosk_reset_timer = None
        self._kiosk_waiting_for_departure = False
        self._kiosk_face_stable_since = time.time()
        self.is_recognizing = False
        print("[KIOSK] Đã mở lại nhận diện tự động.")

    def _reset_for_next_scan(self):
        """
        Nút bấm thủ công "Quét lại" khi nhận diện thất bại:
        1. Hủy auto-reset timer nếu đang chạy.
        2. Ẩn nút bấm.
        3. Dọn dẹp avatar, đưa text về trạng thái chờ 'Chờ nhận diện'.
        4. Mở khóa cờ self.is_recognizing = False để camera quét người mới.
        """
        if getattr(self, 'kiosk_reset_timer', None):
            try:
                self.after_cancel(self.kiosk_reset_timer)
            except Exception:
                pass
            self.kiosk_reset_timer = None

        self._kiosk_waiting_for_departure = False
        self._kiosk_face_stable_since = time.time()
        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
        if hasattr(self, 'btn_kiosk_register'):
            self.btn_kiosk_register.pack_forget()
            
        self.set_idle_state()

    def set_idle_state(self):
        """Dọn dẹp avatar, đưa text về lại trạng thái chờ mặc định và mở khóa nhận diện khuôn mặt mới."""
        print("[ATTENDANCE] set_idle_state start")
        if getattr(self, 'kiosk_reset_timer', None):
            try:
                self.after_cancel(self.kiosk_reset_timer)
            except Exception:
                pass
            self.kiosk_reset_timer = None

        if hasattr(self, 'btn_scan_next'):
            self.btn_scan_next.pack_forget()
        if hasattr(self, 'btn_kiosk_register'):
            self.btn_kiosk_register.pack_forget()

        self.is_recognizing = False
        self._kiosk_waiting_for_departure = False
        self._kiosk_idle_reset_pending = False
        self._kiosk_face_stable_since = None
        self._kiosk_unknown_attempts = 0
        self._last_recognized_id = None
        self._show_auto_scan_status("●  Tự động nhận diện đang hoạt động")

        # Ẩn warning box, hiện greeting card
        if hasattr(self, 'kiosk_warning_box'):
            self.kiosk_warning_box.pack_forget()
        if hasattr(self, 'kiosk_greeting_card'):
            self.kiosk_greeting_card.pack(fill="x")

        # Tránh re-configure thừa thãi nếu UI vốn đã ở trạng thái IDLE
        if getattr(self, '_is_kiosk_ui_idle', False):
            print("[ATTENDANCE] idle state applied")
            print("[ATTENDANCE] set_idle_state end")
            return
            
        self.kiosk_res_banner_frame.configure(fg_color="transparent")
        self.kiosk_res_icon.configure(
            text="⏱", fg_color=("#F1F5F9", "#1E293B"), text_color=CTK_TEXT_DIM
        )
        self.kiosk_res_title.configure(text="Chờ nhận diện", text_color=CTK_TEXT)
        self.kiosk_res_sub.configure(text="Hệ thống tự động ghi nhận khi có nhân viên", text_color=CTK_TEXT_DIM)
        
        self.kiosk_name_label.configure(text="Chưa có lượt quét", text_color=CTK_TEXT)
        if hasattr(self, 'kiosk_id_title'):
            self.kiosk_id_title.configure(text="Mã NV: ")
        self.kiosk_id_badge.configure(text="---", text_color=CTK_TEXT_DIM)
        self.kiosk_role_label.configure(text="💼  Chức vụ: ---")
        self.kiosk_dept_label.configure(text="🏢  Phòng ban: ---")
        
        self._set_kiosk_avatar(None, None, border_color="#E2E8F0", size=(80, 80))
        
        self.kiosk_greeting_card.configure(fg_color="transparent")
        if hasattr(self, 'kiosk_greeting_title'):
            self.kiosk_greeting_title.configure(text="Vui lòng đứng thẳng, nhìn vào camera để điểm danh.", text_color=CTK_TEXT)
        self.kiosk_greeting_text.configure(
            text="Chúc bạn một ngày làm việc hiệu quả!",
            text_color=CTK_TEXT_DIM
        )
        
        self.kiosk_time_label.configure(text="Chờ quét...")
        if hasattr(self, 'kiosk_saved_badge'):
            self.kiosk_saved_badge.pack_forget()
        
        self.kiosk_status_title.configure(text="Sẵn sàng quét khuôn mặt")
        self.kiosk_status_desc.configure(text="Vui lòng đứng thẳng, nhìn vào camera (cự ly 0.5m – 1.2m)")
        if hasattr(self, 'kiosk_status_time'):
            self.kiosk_status_time.configure(text="")
        
        self._is_kiosk_ui_idle = True
        print("[KIOSK] Đã mở khóa nhận diện (self.is_recognizing = False) - Sẵn sàng quét người tiếp theo.")
        print("[ATTENDANCE] idle state applied")
        print("[ATTENDANCE] set_idle_state end")

    def _reset_kiosk_ui(self):
        """Hỗ trợ tương thích ngược cho set_idle_state."""
        self.set_idle_state()

    def _set_kiosk_avatar(self, img_path, emp_id=None, border_color="#E2E8F0", size=(80, 80)):
        """Tải và hiển thị ảnh đại diện tròn của nhân viên (kích thước chuẩn mockup 80x80)."""
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
            circ_img = make_circular_avatar(pil_img, size=size, border_color=border_color, border_width=2)
            ctk_img = ctk.CTkImage(light_image=circ_img, dark_image=circ_img, size=size)
        else:
            light_border = "#E2E8F0" if border_color in ["#cbd5e1", "#E5E7EB", "#E2E8F0"] else border_color
            dark_border = "#334155" if border_color in ["#cbd5e1", "#E5E7EB", "#E2E8F0"] else border_color
            light_avatar = create_default_avatar(size=size, bg_color="#F8FAFC", border_color=light_border)
            dark_avatar = create_default_avatar(size=size, bg_color="#1E293B", border_color=dark_border)
            ctk_img = ctk.CTkImage(light_image=light_avatar, dark_image=dark_avatar, size=size)
            
        if ctk_img is not None:
            self.kiosk_avatar_label.configure(image=ctk_img, text="")
            self.kiosk_avatar_label.image = ctk_img
