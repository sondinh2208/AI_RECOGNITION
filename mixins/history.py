"""
==============================================================
HISTORY MIXIN
Quản lý trang Lịch sử ra vào & điểm danh toàn diện:
- Xem bảng tổng hợp danh sách các lần điểm danh của nhân sự
- Hiển thị thời gian, họ tên, mã NV, chức vụ, phòng ban, trạng thái
- Nút chuyển nhanh sang Kiosk Điểm danh
==============================================================
"""

import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_TEXT, CTK_TEXT_DIM, CTK_SUCCESS,
    CTK_SIDEBAR_HOVER, CTK_BG_MAIN,
)


class HistoryMixin:
    """Mixin quản lý giao diện và danh sách toàn bộ lịch sử điểm danh."""

    def _build_history_page(self):
        """Trang xem toàn bộ lịch sử điểm danh nhân sự."""
        self.history_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)
        
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
        if not hasattr(self, 'history_full_scroll') or not self.history_full_scroll.winfo_exists():
            return
        for w in self.history_full_scroll.winfo_children():
            w.destroy()
            
        if not self.attendance_history:
            empty_box = ctk.CTkFrame(self.history_full_scroll, fg_color="transparent")
            empty_box.pack(fill="both", expand=True, pady=40)
            ctk.CTkLabel(
                empty_box, text="Chưa có lượt điểm danh nào được ghi nhận trong hệ thống",
                font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM
            ).pack()
            return
            
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
            
            is_success = "Thành công" in rec.get("status", "")
            badge_fg = ("#ecfdf5", "#064e3b") if is_success else ("#fee2e2", "#7f1d1d")
            badge_tx = CTK_SUCCESS if is_success else ("#ef4444", "#fca5a5")
            icon = "● " if is_success else "✕ "
            badge = ctk.CTkLabel(
                row, text=icon + rec.get("status", "Thành công"),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=badge_tx, fg_color=badge_fg,
                corner_radius=12, width=100, height=24
            )
            badge.grid(row=0, column=5, padx=12, pady=10, sticky="w")
