"""
==============================================================
DASHBOARD MIXIN
Trang tổng quan hệ thống (Enterprise Dashboard):
- 4 card thống kê: Kiosk, Người đăng ký, Lượt điểm danh, Tỷ lệ chuyên cần
- Biểu đồ điểm danh 7 ngày (Canvas Bar Chart)
- Timeline hoạt động gần đây (5 bản ghi mới nhất)
- Lối tắt thao tác nhanh (4 nút chức năng liên kết)
- Tỷ lệ điểm danh thành công (Canvas Donut Chart)
- Tự động cập nhật số liệu thống kê thời gian thực
==============================================================
"""

from pathlib import Path
from datetime import datetime, timedelta
import tkinter as tk
import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_TEXT, CTK_TEXT_DIM, CTK_SIDEBAR_HOVER,
    CTK_BG_MAIN, DATA_FACES_DIR,
)


class DashboardMixin:
    """Mixin quản lý giao diện bảng điều khiển và số liệu thống kê tổng quan."""

    # ══════════════════════════════════════════════════════════
    # DATA HELPERS
    # ══════════════════════════════════════════════════════════

    def _successful_attendance_count(self):
        """Không tính lượt xác thực thất bại là lượt điểm danh."""
        return sum(
            1 for record in self.attendance_history
            if "Thành công" in record.get("status", "")
        )

    def _today_attendance_stats(self):
        """Thống kê điểm danh trong ngày hôm nay: (success, fail, total, rate%)."""
        today_str = datetime.now().strftime("%d/%m/%Y")
        success = fail = 0
        for rec in self.attendance_history:
            if today_str in rec.get("time", ""):
                if "Thành công" in rec.get("status", ""):
                    success += 1
                else:
                    fail += 1
        total = success + fail
        rate = round(success / total * 100) if total > 0 else 0
        return success, fail, total, rate

    def _last_7_days_data(self):
        """Trả về [(tên_thứ, ngày/tháng, số_lượt), ...] cho 7 ngày gần nhất."""
        today = datetime.now().date()
        day_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
        result = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            d_full = d.strftime("%d/%m/%Y")
            count = sum(
                1 for rec in self.attendance_history
                if d_full in rec.get("time", "") and "Thành công" in rec.get("status", "")
            )
            result.append((day_names[d.weekday()], d.strftime("%d/%m"), count))
        return result

    def _relative_time_str(self, time_str):
        """Chuyển timestamp thành thời gian tương đối ('2 phút trước')."""
        try:
            dt = datetime.strptime(time_str, "%H:%M:%S %d/%m/%Y")
            secs = max(0, int((datetime.now() - dt).total_seconds()))
            if secs < 60:
                return "Vừa xong"
            mins = secs // 60
            if mins < 60:
                return f"{mins} phút trước"
            hrs = mins // 60
            if hrs < 24:
                return f"{hrs} giờ trước"
            return f"{hrs // 24} ngày trước"
        except Exception:
            return ""

    # ══════════════════════════════════════════════════════════
    # MAIN PAGE BUILDER
    # ══════════════════════════════════════════════════════════

    def _build_dashboard_page(self):
        """Xây dựng trang Dashboard tổng quan hệ thống."""
        self.dashboard_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)

        scroll = ctk.CTkScrollableFrame(
            self.dashboard_frame, fg_color="transparent",
            scrollbar_button_color=CTK_ACCENT,
            scrollbar_button_hover_color=CTK_TEXT_DIM,
        )
        scroll.pack(fill="both", expand=True)

        # ── ROW 1: 4 Stat Cards ──────────────────────────
        self._build_dash_stat_cards(scroll)

        # ── ROW 2: Biểu đồ 7 ngày + Hoạt động gần đây ──
        row2 = ctk.CTkFrame(scroll, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 12))
        row2.grid_columnconfigure(0, weight=3)
        row2.grid_columnconfigure(1, weight=2)
        row2.grid_rowconfigure(0, weight=1)
        self._build_bar_chart_card(row2)
        self._build_activity_timeline(row2)

        # ── ROW 3: Lối tắt nhanh + Donut chart ──────────
        row3 = ctk.CTkFrame(scroll, fg_color="transparent")
        row3.pack(fill="x")
        row3.grid_columnconfigure(0, weight=3)
        row3.grid_columnconfigure(1, weight=2)
        row3.grid_rowconfigure(0, weight=1)
        self._build_quick_actions_card(row3)
        self._build_donut_chart_card(row3)

    # ══════════════════════════════════════════════════════════
    # ROW 1: STAT CARDS
    # ══════════════════════════════════════════════════════════

    def _build_dash_stat_cards(self, parent):
        """4 card thống kê chính trên hàng đầu tiên."""
        stats = ctk.CTkFrame(parent, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 12))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dstat")

        total_db = len(list(Path(DATA_FACES_DIR).glob("*.jpg"))) if Path(DATA_FACES_DIR).exists() else 0
        att_count = self._successful_attendance_count()
        _, _, _, today_rate = self._today_attendance_stats()

        def card(col, icon, ibg, ic, title, val, sub, show_dot=False):
            c = ctk.CTkFrame(
                stats, fg_color=CTK_CARD, corner_radius=12,
                border_width=1, border_color=CTK_ACCENT,
            )
            c.grid(
                row=0, column=col, sticky="nsew",
                padx=(0 if col == 0 else 6, 0 if col == 3 else 6),
            )

            wrap = ctk.CTkFrame(c, fg_color="transparent")
            wrap.pack(fill="both", expand=True, padx=14, pady=14)

            # Icon vòng tròn
            ib = ctk.CTkFrame(wrap, width=44, height=44, corner_radius=22, fg_color=ibg)
            ib.pack(side="left", padx=(0, 12))
            ib.pack_propagate(False)
            ctk.CTkLabel(
                ib, text=icon, font=ctk.CTkFont(size=18), text_color=ic,
            ).place(relx=0.5, rely=0.5, anchor="center")

            # Cột text
            tb = ctk.CTkFrame(wrap, fg_color="transparent")
            tb.pack(side="left", fill="both", expand=True)

            ctk.CTkLabel(
                tb, text=title, font=ctk.CTkFont(size=11),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x")

            # Hàng giá trị (+ chấm xanh tùy chọn)
            vr = ctk.CTkFrame(tb, fg_color="transparent")
            vr.pack(fill="x", pady=(2, 2))
            lv = ctk.CTkLabel(
                vr, text=val,
                font=ctk.CTkFont(size=20, weight="bold"),
                text_color=CTK_TEXT, anchor="w",
            )
            lv.pack(side="left")
            if show_dot:
                ctk.CTkFrame(
                    vr, width=10, height=10, corner_radius=5,
                    fg_color=("#10b981", "#22c55e"),
                ).pack(side="left", padx=(8, 0), pady=(4, 0))

            ctk.CTkLabel(
                tb, text=sub, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x")

            return lv

        self.lbl_dash_kiosk = card(
            0, "🖥", ("#dcfce7", "#065f46"), ("#10b981", "#34d399"),
            "Kiosk Điểm danh", "Hoạt động", "Sẵn sàng phục vụ", show_dot=True,
        )
        self.lbl_dash_users = card(
            1, "👥", ("#dbeafe", "#1e3a5f"), ("#3b82f6", "#60a5fa"),
            "Người đăng ký", f"{total_db} hồ sơ", "Đã đăng ký trong hệ thống",
        )
        self.lbl_dash_att = card(
            2, "🕒", ("#fef3c7", "#78350f"), ("#f59e0b", "#fbbf24"),
            "Lượt điểm danh", f"{att_count} lượt", "Trong phiên làm việc",
        )
        self.lbl_dash_rate = card(
            3, "📊", ("#ede9fe", "#3b0764"), ("#8b5cf6", "#a78bfa"),
            "Tỷ lệ chuyên cần", f"{today_rate}%", "Trong ngày hôm nay",
        )

    # ══════════════════════════════════════════════════════════
    # ROW 2 LEFT: BAR CHART – BIỂU ĐỒ ĐIỂM DANH 7 NGÀY
    # ══════════════════════════════════════════════════════════

    def _build_bar_chart_card(self, parent):
        """Card chứa biểu đồ cột điểm danh 7 ngày gần nhất."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 4))

        h_left = ctk.CTkFrame(hdr, fg_color="transparent")
        h_left.pack(side="left", fill="x", expand=True)

        title_row = ctk.CTkFrame(h_left, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="📊", font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            title_row, text="Biểu đồ điểm danh 7 ngày",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            h_left, text="Thống kê số lượt điểm danh mỗi ngày trong tuần",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Badge khoảng thời gian
        period = ctk.CTkFrame(
            hdr, fg_color=("#f1f5f9", "#111c2e"),
            corner_radius=6, border_width=1, border_color=CTK_ACCENT,
        )
        period.pack(side="right")
        ctk.CTkLabel(
            period, text="📅  7 ngày qua  ▾",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
        ).pack(padx=10, pady=4)

        # ── Canvas biểu đồ ──
        is_dark = ctk.get_appearance_mode().lower() == "dark"
        canvas_bg = "#1E293B" if is_dark else "#FFFFFF"

        self._bar_chart_canvas = tk.Canvas(
            card, height=230, bg=canvas_bg, highlightthickness=0, bd=0,
        )
        self._bar_chart_canvas.pack(fill="x", padx=16, pady=(8, 16))
        self._bar_chart_canvas.bind(
            "<Configure>", lambda e: self._schedule_bar_chart_redraw(),
        )

    def _schedule_bar_chart_redraw(self):
        """Debounce redraw khi Canvas thay đổi kích thước."""
        if hasattr(self, '_bcr_id'):
            self.after_cancel(self._bcr_id)
        self._bcr_id = self.after(80, self._draw_bar_chart)

    def _draw_bar_chart(self):
        """Vẽ biểu đồ cột điểm danh 7 ngày trên Canvas."""
        if not hasattr(self, '_bar_chart_canvas'):
            return
        canvas = self._bar_chart_canvas
        try:
            if not canvas.winfo_exists():
                return
        except Exception:
            return

        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 80 or h < 80:
            return

        # Theme colors
        dk = ctk.get_appearance_mode().lower() == "dark"
        bg      = "#1E293B" if dk else "#FFFFFF"
        grid_c  = "#2d3a4e" if dk else "#F1F5F9"
        text_c  = "#F8FAFC" if dk else "#1F2937"
        dim_c   = "#94A3B8" if dk else "#9CA3AF"
        bar_c   = "#3B82F6"
        bar_bg  = "#1a2744" if dk else "#EFF6FF"
        canvas.configure(bg=bg)

        data = self._last_7_days_data()
        mx = max((d[2] for d in data), default=0)
        if mx == 0:
            mx = 10

        # Vùng vẽ (margins)
        ml, mr, mt, mb = 45, 15, 15, 50
        cw = w - ml - mr
        ch = h - mt - mb

        # Trục Y: làm tròn lên bội 10
        y_max = max(10, ((mx // 10) + 1) * 10)

        # Đường kẻ ngang + nhãn trục Y
        n_grid = 5
        for i in range(n_grid + 1):
            y_val = y_max * i / n_grid
            y_pos = mt + ch - ch * i / n_grid
            canvas.create_line(ml, y_pos, w - mr, y_pos, fill=grid_c, width=1)
            canvas.create_text(
                ml - 8, y_pos, text=str(int(y_val)),
                font=("Segoe UI", 9), fill=dim_c, anchor="e",
            )

        # Các cột dữ liệu
        n = len(data)
        spacing = cw / n
        bar_w = max(20, min(spacing * 0.45, 50))

        for idx, (day_name, date_str, count) in enumerate(data):
            cx = ml + spacing * idx + spacing / 2
            x1 = cx - bar_w / 2
            x2 = cx + bar_w / 2
            r = min(bar_w / 2, 6)

            # Cột nền (toàn chiều cao)
            canvas.create_rectangle(
                x1, mt + r, x2, mt + ch, fill=bar_bg, outline="",
            )
            canvas.create_oval(
                x1, mt, x2, mt + r * 2, fill=bar_bg, outline="",
            )

            # Cột dữ liệu
            if count > 0:
                bar_h = (count / y_max) * ch
                y_top = mt + ch - bar_h
                canvas.create_rectangle(
                    x1, y_top + r, x2, mt + ch, fill=bar_c, outline="",
                )
                canvas.create_oval(
                    x1, y_top, x2, y_top + r * 2, fill=bar_c, outline="",
                )
                # Giá trị trên đỉnh cột
                canvas.create_text(
                    cx, y_top - 8, text=str(count),
                    font=("Segoe UI", 9, "bold"), fill=text_c,
                )

            # Nhãn trục X: tên thứ + ngày/tháng
            canvas.create_text(
                cx, mt + ch + 14, text=day_name,
                font=("Segoe UI", 9, "bold"), fill=text_c,
            )
            canvas.create_text(
                cx, mt + ch + 30, text=date_str,
                font=("Segoe UI", 8), fill=dim_c,
            )

    # ══════════════════════════════════════════════════════════
    # ROW 2 RIGHT: HOẠT ĐỘNG GẦN ĐÂY (ACTIVITY TIMELINE)
    # ══════════════════════════════════════════════════════════

    def _build_activity_timeline(self, parent):
        """Card timeline 5 bản ghi hoạt động gần nhất."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 4))

        h_left = ctk.CTkFrame(hdr, fg_color="transparent")
        h_left.pack(side="left", fill="x", expand=True)

        title_row = ctk.CTkFrame(h_left, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="🔔", font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            title_row, text="Hoạt động gần đây",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            h_left, text="5 bản ghi mới nhất trong hệ thống",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Nút "Xem lịch sử >"
        ctk.CTkButton(
            hdr, text="Xem lịch sử  ▸",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=28, corner_radius=6,
            fg_color="transparent", hover_color=CTK_SIDEBAR_HOVER,
            text_color=("#2563eb", "#60a5fa"),
            border_width=1, border_color=CTK_ACCENT,
            command=lambda: self._navigate("history"),
        ).pack(side="right")

        # ── Container các entry ──
        self._timeline_container = ctk.CTkFrame(card, fg_color="transparent")
        self._timeline_container.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        self._render_timeline_entries()

    def _render_timeline_entries(self):
        """Render 5 bản ghi điểm danh gần nhất dưới dạng timeline feed."""
        ct = self._timeline_container
        if not ct.winfo_exists():
            return
        for child in ct.winfo_children():
            child.destroy()

        entries = self.attendance_history[:5]

        if not entries:
            ctk.CTkLabel(
                ct, text="Chưa có hoạt động nào được ghi nhận",
                font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM,
            ).pack(fill="both", expand=True, pady=40)
            return

        for i, rec in enumerate(entries):
            is_success = "Thành công" in rec.get("status", "")
            name = rec.get("name", "Không rõ")
            time_rel = self._relative_time_str(rec.get("time", ""))

            if is_success:
                desc = "Điểm danh thành công tại Kiosk"
                dot_c = ("#10b981", "#22c55e")
                badge_txt = "Thành công"
                badge_bg = ("#dcfce7", "#065f46")
                badge_tc = ("#16a34a", "#22c55e")
            else:
                desc = "Không nhận diện được khuôn mặt"
                dot_c = ("#ef4444", "#f87171")
                badge_txt = "Thất bại"
                badge_bg = ("#fef2f2", "#450a0a")
                badge_tc = ("#dc2626", "#f87171")

            row = ctk.CTkFrame(ct, fg_color="transparent")
            row.pack(fill="x", pady=(0, 2))

            # Bên trái: chấm tròn + tên + mô tả
            left_f = ctk.CTkFrame(row, fg_color="transparent")
            left_f.pack(side="left", fill="both", expand=True)

            name_row = ctk.CTkFrame(left_f, fg_color="transparent")
            name_row.pack(fill="x")

            ctk.CTkFrame(
                name_row, width=8, height=8, corner_radius=4, fg_color=dot_c,
            ).pack(side="left", padx=(0, 8), pady=(6, 0))

            ctk.CTkLabel(
                name_row, text=name,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=CTK_TEXT, anchor="w",
            ).pack(side="left")

            ctk.CTkLabel(
                left_f, text=desc, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x", padx=(16, 0))

            # Bên phải: thời gian tương đối + badge trạng thái
            right_f = ctk.CTkFrame(row, fg_color="transparent")
            right_f.pack(side="right")

            ctk.CTkLabel(
                right_f, text=time_rel, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM,
            ).pack(side="left", padx=(0, 8))

            ctk.CTkLabel(
                right_f, text=badge_txt,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=badge_tc, fg_color=badge_bg,
                corner_radius=4, width=70, height=24,
            ).pack(side="left")

            # Đường phân cách (trừ entry cuối)
            if i < len(entries) - 1:
                ctk.CTkFrame(
                    ct, fg_color=CTK_ACCENT, height=1,
                ).pack(fill="x", pady=4)

    # ══════════════════════════════════════════════════════════
    # ROW 3 LEFT: LỐI TẮT THAO TÁC NHANH
    # ══════════════════════════════════════════════════════════

    def _build_quick_actions_card(self, parent):
        """Card chứa 4 nút lối tắt chức năng chính."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 12))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="⚡", font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            title_row, text="Lối tắt thao tác nhanh",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            hdr, text="Truy cập các chức năng chính của hệ thống",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # ── 4 nút hành động ──
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        btn_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="qa")

        actions = [
            # (col, icon, title, subtitle, fg_color, hover, text_color, is_outline, command)
            (0, "🖥", "Vào Kiosk\nĐiểm danh", "Bắt đầu điểm danh ngay",
             ("#2563eb", "#2563eb"), ("#1d4ed8", "#1d4ed8"), "#FFFFFF",
             False, lambda: self._navigate("attendance")),
            (1, "➕", "Đăng ký mới", "Thêm người dùng",
             ("#10b981", "#059669"), ("#047857", "#047857"), "#FFFFFF",
             False, lambda: self._navigate("add_employee")),
            (2, "👥", "Quản lý\nNgười đăng ký", "Xem và chỉnh sửa",
             ("#f1f5f9", "#111c2e"), CTK_SIDEBAR_HOVER, None,
             True, lambda: self._navigate("database")),
            (3, "📋", "Xem lịch sử", "Tra cứu ra vào",
             ("#f1f5f9", "#111c2e"), CTK_SIDEBAR_HOVER, None,
             True, lambda: self._navigate("history")),
        ]

        for col, icon, title, subtitle, fg, hover, tx, is_outline, cmd in actions:
            f = ctk.CTkFrame(
                btn_row, fg_color=fg, corner_radius=10, cursor="hand2",
            )
            if is_outline:
                f.configure(border_width=1, border_color=CTK_ACCENT)

            f.grid(
                row=0, column=col, sticky="nsew",
                padx=(0 if col == 0 else 4, 0 if col == 3 else 4),
            )

            inner = ctk.CTkFrame(f, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=12, pady=12)

            t_color = tx if tx else CTK_TEXT
            s_color = tx if tx else CTK_TEXT_DIM

            icon_lbl = ctk.CTkLabel(
                inner, text=icon, font=ctk.CTkFont(size=16), text_color=t_color,
            )
            icon_lbl.pack(anchor="w")

            title_lbl = ctk.CTkLabel(
                inner, text=title,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=t_color, anchor="w",
                wraplength=130, justify="left",
            )
            title_lbl.pack(fill="x", pady=(4, 2))

            bot = ctk.CTkFrame(inner, fg_color="transparent")
            bot.pack(fill="x")
            sub_lbl = ctk.CTkLabel(
                bot, text=subtitle, font=ctk.CTkFont(size=9),
                text_color=s_color, anchor="w",
            )
            sub_lbl.pack(side="left", fill="x", expand=True)
            arrow_lbl = ctk.CTkLabel(
                bot, text="▸", font=ctk.CTkFont(size=14, weight="bold"),
                text_color=s_color,
            )
            arrow_lbl.pack(side="right")

            # Gán sự kiện click cho tất cả widget con
            click_cb = lambda e, c=cmd: c()
            for widget in [f, inner, icon_lbl, title_lbl, bot, sub_lbl, arrow_lbl]:
                widget.bind("<Button-1>", click_cb)

    # ══════════════════════════════════════════════════════════
    # ROW 3 RIGHT: DONUT CHART – TỶ LỆ ĐIỂM DANH THÀNH CÔNG
    # ══════════════════════════════════════════════════════════

    def _build_donut_chart_card(self, parent):
        """Card biểu đồ tròn tỷ lệ điểm danh thành công."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 4))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="🎯", font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            title_row, text="Tỷ lệ điểm danh thành công",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            hdr, text="Thống kê kết quả điểm danh trong ngày",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # ── Nội dung: Canvas (donut) + Legend (labels) ──
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        is_dark = ctk.get_appearance_mode().lower() == "dark"
        canvas_bg = "#1E293B" if is_dark else "#FFFFFF"

        self._donut_canvas = tk.Canvas(
            content, width=140, height=140,
            bg=canvas_bg, highlightthickness=0, bd=0,
        )
        self._donut_canvas.pack(side="left", padx=(10, 10))

        self._donut_legend = ctk.CTkFrame(content, fg_color="transparent")
        self._donut_legend.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self._donut_canvas.bind(
            "<Configure>", lambda e: self._schedule_donut_redraw(),
        )
        # Vẽ lần đầu sau khi layout sẵn sàng
        self.after(200, self._draw_donut_chart)

    def _schedule_donut_redraw(self):
        """Debounce redraw khi Donut Canvas thay đổi kích thước."""
        if hasattr(self, '_dcr_id'):
            self.after_cancel(self._dcr_id)
        self._dcr_id = self.after(80, self._draw_donut_chart)

    def _draw_donut_chart(self):
        """Vẽ biểu đồ donut tỷ lệ thành công/thất bại và cập nhật legend."""
        if not hasattr(self, '_donut_canvas'):
            return
        canvas = self._donut_canvas
        try:
            if not canvas.winfo_exists():
                return
        except Exception:
            return

        canvas.delete("all")
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw < 40 or ch < 40:
            return

        # Theme
        dk = ctk.get_appearance_mode().lower() == "dark"
        bg     = "#1E293B" if dk else "#FFFFFF"
        txt_c  = "#F8FAFC" if dk else "#1F2937"
        dim_c  = "#94A3B8" if dk else "#6B7280"
        ok_c   = "#3B82F6"
        fail_c = "#EF4444"
        empty_c = "#334155" if dk else "#E5E7EB"
        canvas.configure(bg=bg)

        success, fail, total, rate = self._today_attendance_stats()

        # Tọa độ donut
        cx, cy = cw / 2, ch / 2
        outer_r = min(cw, ch) / 2 - 8
        inner_r = outer_r * 0.65

        if total > 0:
            s_angle = 360 * success / total
        else:
            s_angle = 0

        # Vẽ cung thành công
        if total > 0:
            canvas.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=90, extent=-s_angle, fill=ok_c, outline="",
            )
            # Vẽ cung thất bại
            if fail > 0:
                canvas.create_arc(
                    cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                    start=90 - s_angle, extent=-(360 - s_angle),
                    fill=fail_c, outline="",
                )
        else:
            # Không có dữ liệu -> vòng xám
            canvas.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=0, extent=360, fill=empty_c, outline="",
            )

        # Lỗ giữa (tạo hiệu ứng donut)
        canvas.create_oval(
            cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
            fill=bg, outline="",
        )

        # Text trung tâm
        canvas.create_text(
            cx, cy - 10, text=f"{rate}%",
            font=("Segoe UI", 20, "bold"), fill=txt_c,
        )
        canvas.create_text(
            cx, cy + 14, text="Thành công",
            font=("Segoe UI", 9), fill=dim_c,
        )

        # ── Cập nhật Legend (CTk labels) ──
        legend = self._donut_legend
        if not legend.winfo_exists():
            return
        for child in legend.winfo_children():
            child.destroy()

        fail_rate = 100 - rate if total > 0 else 0

        # Thành công
        s_row = ctk.CTkFrame(legend, fg_color="transparent")
        s_row.pack(fill="x", pady=(10, 0))
        ctk.CTkFrame(
            s_row, width=10, height=10, corner_radius=5, fg_color=ok_c,
        ).pack(side="left", padx=(0, 8), pady=(3, 0))
        ctk.CTkLabel(
            s_row, text="Điểm danh thành công",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            s_row, text=f"{rate}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_TEXT, anchor="e",
        ).pack(side="right")

        ctk.CTkLabel(
            legend, text=f"{success} lượt",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=(18, 0), pady=(0, 10))

        # Thất bại
        f_row = ctk.CTkFrame(legend, fg_color="transparent")
        f_row.pack(fill="x")
        ctk.CTkFrame(
            f_row, width=10, height=10, corner_radius=5, fg_color=fail_c,
        ).pack(side="left", padx=(0, 8), pady=(3, 0))
        ctk.CTkLabel(
            f_row, text="Chưa thành công",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            f_row, text=f"{fail_rate}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_TEXT, anchor="e",
        ).pack(side="right")

        ctk.CTkLabel(
            legend, text=f"{fail} lượt",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=(18, 0))

    # ══════════════════════════════════════════════════════════
    # REFRESH – CẬP NHẬT SỐ LIỆU DASHBOARD
    # ══════════════════════════════════════════════════════════

    def _reload_dashboard_stats(self):
        """Cập nhật lại toàn bộ số liệu trên Dashboard khi chuyển tab."""
        # Stat cards
        if hasattr(self, 'lbl_dash_att'):
            self.lbl_dash_att.configure(
                text=f"{self._successful_attendance_count()} lượt",
            )
        try:
            total_db = len(list(Path(DATA_FACES_DIR).glob("*.jpg")))
            if hasattr(self, 'lbl_dash_users'):
                self.lbl_dash_users.configure(text=f"{total_db} hồ sơ")
        except Exception:
            pass
        if hasattr(self, 'lbl_dash_rate'):
            _, _, _, rate = self._today_attendance_stats()
            self.lbl_dash_rate.configure(text=f"{rate}%")

        # Charts
        if hasattr(self, '_bar_chart_canvas'):
            self._draw_bar_chart()
        if hasattr(self, '_donut_canvas'):
            self._draw_donut_chart()

        # Timeline
        if hasattr(self, '_timeline_container'):
            try:
                if self._timeline_container.winfo_exists():
                    self._render_timeline_entries()
            except Exception:
                pass
