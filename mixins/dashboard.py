"""
==============================================================
DASHBOARD MIXIN
Trang tổng quan hệ thống:
- Các card thống kê: Trạng thái Kiosk, Người đăng ký, Lượt điểm danh, Ngưỡng AI
- Lối tắt điều hướng nhanh sang các chức năng chính
- Tự động cập nhật số liệu thống kê thời gian thực
==============================================================
"""

from pathlib import Path
import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_TEXT, CTK_TEXT_DIM, CTK_SIDEBAR_HOVER,
    CTK_BG_MAIN, DATA_FACES_DIR,
)


class DashboardMixin:
    """Mixin quản lý giao diện bảng điều khiển và số liệu thống kê tổng quan."""

    def _build_dashboard_page(self):
        """Trang tổng quan (Dashboard) hệ thống."""
        self.dashboard_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)
        
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
            
        total_db = len(list(Path(DATA_FACES_DIR).glob("*.jpg"))) if Path(DATA_FACES_DIR).exists() else 0
        self.lbl_dash_kiosk = create_dash_card(0, "🔲", "Kiosk Điểm danh", "Hoạt động", "YOLOv8 + ArcFace", "#10b981")
        self.lbl_dash_users = create_dash_card(1, "👥", "Người đăng ký", f"{total_db} hồ sơ", "Đã số hóa khuôn mặt", "#3b82f6")
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
