"""
==============================================================
DASHBOARD MIXIN - ENTERPRISE HR & ATTENDANCE DASHBOARD
Trang tổng quan hệ thống FaceCheck:
- 4 card thống kê kèm mini sparklines (Kiosk, Người đăng ký, Lượt điểm danh, Tỷ lệ chuyên cần)
- Biểu đồ điểm danh 7 ngày (Canvas Bar Chart với gradient và nhãn)
- Timeline hoạt động gần đây (5 bản ghi mới nhất với avatar và status badge)
- Lối tắt thao tác nhanh (4 nút chức năng điều hướng trực tiếp)
- Tỷ lệ điểm danh thành công (Canvas Donut Chart kèm Legend chi tiết)
- Tự động cập nhật thời gian thực từ database và attendance_history
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
            1 for record in getattr(self, "attendance_history", [])
            if "Thành công" in record.get("status", "")
        )

    def _today_attendance_stats(self):
        """Thống kê điểm danh trong ngày hôm nay: (success, fail, total, rate%)."""
        today_str = datetime.now().strftime("%d/%m/%Y")
        success = fail = 0
        for rec in getattr(self, "attendance_history", []):
            if today_str in rec.get("time", ""):
                if "Thành công" in rec.get("status", ""):
                    success += 1
                else:
                    fail += 1
        total = success + fail
        rate = round(success / total * 100) if total > 0 else 0
        return success, fail, total, rate

    def _last_7_days_data(self):
        """Trả về [(tên_thứ, ngày/tháng, số_lượt), ...] cho tuần hiện tại (Thứ 2 -> Chủ nhật)."""
        today = datetime.now().date()
        # Bắt đầu từ Thứ Hai của tuần hiện tại
        monday = today - timedelta(days=today.weekday())
        day_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
        result = []
        for i in range(7):
            d = monday + timedelta(days=i)
            d_full = d.strftime("%d/%m/%Y")
            count = sum(
                1 for rec in getattr(self, "attendance_history", [])
                if d_full in rec.get("time", "") and "Thành công" in rec.get("status", "")
            )
            result.append((day_names[i], d.strftime("%d/%m"), count))
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
        """Xây dựng trang Dashboard tổng quan hệ thống khớp 100% bản thiết kế."""
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
        row2.grid_columnconfigure(0, weight=58)
        row2.grid_columnconfigure(1, weight=42)
        row2.grid_rowconfigure(0, weight=1)
        self._build_bar_chart_card(row2)
        self._build_activity_timeline(row2)

        # ── ROW 3: Lối tắt nhanh + Donut chart ──────────
        row3 = ctk.CTkFrame(scroll, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 12))
        row3.grid_columnconfigure(0, weight=58)
        row3.grid_columnconfigure(1, weight=42)
        row3.grid_rowconfigure(0, weight=1)
        self._build_quick_actions_card(row3)
        self._build_donut_chart_card(row3)

    # ══════════════════════════════════════════════════════════
    # ROW 1: STAT CARDS (4 cards với mini sparkline graphics)
    # ══════════════════════════════════════════════════════════

    def _build_dash_stat_cards(self, parent):
        """4 card thống kê chính trên hàng đầu tiên với đồ họa sparkline."""
        stats = ctk.CTkFrame(parent, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 12))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dstat")

        total_db = len(list(Path(DATA_FACES_DIR).glob("*.jpg"))) if Path(DATA_FACES_DIR).exists() else 0
        att_count = self._successful_attendance_count()
        _, _, _, today_rate = self._today_attendance_stats()
        is_dark = ctk.get_appearance_mode().lower() == "dark"

        def _draw_sparkline(c_parent, stype):
            bg = "#1E293B" if is_dark else "#FFFFFF"
            canv = tk.Canvas(c_parent, width=64, height=30, bg=bg, highlightthickness=0, bd=0)
            canv.pack(side="right", padx=(4, 0), pady=(4, 0))
            if stype == "kiosk_wave":
                # Smooth green wave
                pts = [(3, 22), (16, 25), (28, 13), (40, 19), (52, 7), (61, 11)]
                canv.create_line(pts, fill="#10B981", width=2, smooth=True)
            elif stype == "user_bars":
                # 4 Blue ascending bars
                bars = [(8, 18), (20, 13), (32, 7), (44, 2)]
                for bx, btop in bars:
                    canv.create_rectangle(bx, btop, bx + 6, 26, fill="#93C5FD", outline="")
            elif stype == "att_bars":
                # 5 Orange bars
                bars = [(4, 16), (15, 9), (26, 17), (37, 5), (48, 11)]
                for bx, btop in bars:
                    canv.create_rectangle(bx, btop, bx + 5, 26, fill="#FCD34D", outline="")
            elif stype == "rate_wave":
                # Purple smooth wave
                pts = [(3, 20), (15, 24), (28, 10), (41, 16), (53, 5), (61, 9)]
                canv.create_line(pts, fill="#A855F7", width=2, smooth=True)
            return canv

        def card(col, icon, ibg, ic, title, val, sub, stype, show_dot=False):
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

            # Icon hộp vuông bo tròn
            ib = ctk.CTkFrame(wrap, width=44, height=44, corner_radius=10, fg_color=ibg)
            ib.pack(side="left", padx=(0, 12))
            ib.pack_propagate(False)
            ctk.CTkLabel(
                ib, text=icon, font=ctk.CTkFont(size=18), text_color=ic,
            ).place(relx=0.5, rely=0.5, anchor="center")

            # Cột nội dung text
            tb = ctk.CTkFrame(wrap, fg_color="transparent")
            tb.pack(side="left", fill="both", expand=True)

            ctk.CTkLabel(
                tb, text=title, font=ctk.CTkFont(size=11),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x")

            # Hàng giá trị (+ chấm xanh trạng thái tùy chọn)
            vr = ctk.CTkFrame(tb, fg_color="transparent")
            vr.pack(fill="x", pady=(2, 2))
            lv = ctk.CTkLabel(
                vr, text=val,
                font=ctk.CTkFont(size=19, weight="bold"),
                text_color=CTK_TEXT, anchor="w",
            )
            lv.pack(side="left")
            if show_dot:
                ctk.CTkFrame(
                    vr, width=9, height=9, corner_radius=5,
                    fg_color=("#10b981", "#22c55e"),
                ).pack(side="left", padx=(8, 0), pady=(3, 0))

            ctk.CTkLabel(
                tb, text=sub, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x")

            # Mini sparkline canvas góc phải
            _draw_sparkline(wrap, stype)

            return lv

        self.lbl_dash_kiosk = card(
            0, "🖥", ("#DCFCE7", "#064E3B"), ("#10B981", "#34D399"),
            "Kiosk Điểm danh", "Hoạt động", "Sẵn sàng phục vụ", "kiosk_wave", show_dot=True,
        )
        self.lbl_dash_users = card(
            1, "👥", ("#DBEAFE", "#1E3A5F"), ("#3B82F6", "#60A5FA"),
            "Người đăng ký", f"{total_db} hồ sơ", "Đã đăng ký trong hệ thống", "user_bars",
        )
        self.lbl_dash_att = card(
            2, "🕒", ("#FEF3C7", "#78350F"), ("#F59E0B", "#FBBF24"),
            "Lượt điểm danh", f"{att_count} lượt", "Trong phiên làm việc", "att_bars",
        )
        self.lbl_dash_rate = card(
            3, "👤", ("#EDE9FE", "#3B0764"), ("#8B5CF6", "#A78BFA"),
            "Tỷ lệ chuyên cần", f"{today_rate}%", "Trong ngày hôm nay", "rate_wave",
        )

    # ══════════════════════════════════════════════════════════
    # ROW 2 LEFT: BAR CHART – BIỂU ĐỒ ĐIỂM DANH 7 NGÀY
    # ══════════════════════════════════════════════════════════

    def _build_bar_chart_card(self, parent):
        """Card chứa biểu đồ cột điểm danh 7 ngày trong tuần."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(14, 4))

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

        # Badge khoảng thời gian góc phải
        period = ctk.CTkFrame(
            hdr, fg_color=("#F8FAFC", "#0F172A"),
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
            card, height=220, bg=canvas_bg, highlightthickness=0, bd=0,
        )
        self._bar_chart_canvas.pack(fill="both", expand=True, padx=16, pady=(6, 14))
        self._bar_chart_canvas.bind(
            "<Configure>", lambda e: self._schedule_bar_chart_redraw(),
        )

    def _schedule_bar_chart_redraw(self):
        """Debounce redraw khi Canvas thay đổi kích thước."""
        if hasattr(self, '_bcr_id'):
            self.after_cancel(self._bcr_id)
        self._bcr_id = self.after(60, self._draw_bar_chart)

    def _draw_bar_chart(self):
        """Vẽ biểu đồ cột điểm danh 7 ngày trên Canvas với các cột bo góc thẩm mỹ."""
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

        dk = ctk.get_appearance_mode().lower() == "dark"
        bg     = "#1E293B" if dk else "#FFFFFF"
        grid_c = "#2D3A4E" if dk else "#F1F5F9"
        text_c = "#F8FAFC" if dk else "#1F2937"
        dim_c  = "#94A3B8" if dk else "#9CA3AF"
        bar_body_c = "#3B82F6"
        bar_cap_c  = "#60A5FA"
        canvas.configure(bg=bg)

        data = self._last_7_days_data()
        mx = max((d[2] for d in data), default=0)
        # Làm tròn lên bội số của 10, tối thiểu 50 để khớp chuẩn mockup Y-axis (0, 10, 20, 30, 40, 50)
        y_max = max(50, ((mx // 10) + 1) * 10)

        # Margins vẽ
        ml, mr, mt, mb = 40, 16, 20, 46
        cw = w - ml - mr
        ch = h - mt - mb

        # Đường kẻ ngang và nhãn trục Y
        n_grid = 5
        for i in range(n_grid + 1):
            y_val = int(y_max * i / n_grid)
            y_pos = mt + ch - ch * i / n_grid
            canvas.create_line(ml, y_pos, w - mr, y_pos, fill=grid_c, width=1)
            canvas.create_text(
                ml - 8, y_pos, text=str(y_val),
                font=("Segoe UI", 9), fill=dim_c, anchor="e",
            )

        # Vẽ 7 cột
        n = len(data)
        spacing = cw / n
        bar_w = max(24, min(spacing * 0.48, 48))

        for idx, (day_name, date_str, count) in enumerate(data):
            cx = ml + spacing * idx + spacing / 2
            x1 = cx - bar_w / 2
            x2 = cx + bar_w / 2

            if count > 0:
                bar_h = max(10, (count / y_max) * ch)
                y_top = mt + ch - bar_h
                r = min(bar_w / 2, 5)

                # Vẽ thân cột
                canvas.create_rectangle(
                    x1, y_top + r, x2, mt + ch, fill=bar_body_c, outline="",
                )
                # Vẽ nắp vòm bo góc trên đỉnh cột
                canvas.create_oval(
                    x1, y_top, x2, y_top + r * 2, fill=bar_cap_c, outline="",
                )
                # Giá trị đếm trên đỉnh cột
                canvas.create_text(
                    cx, y_top - 9, text=str(count),
                    font=("Segoe UI", 9, "bold"), fill=text_c,
                )

            # Nhãn trục X: Tên thứ + Ngày/tháng
            canvas.create_text(
                cx, mt + ch + 12, text=day_name,
                font=("Segoe UI", 9, "bold"), fill=text_c,
            )
            canvas.create_text(
                cx, mt + ch + 27, text=date_str,
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
        hdr.pack(fill="x", padx=18, pady=(14, 4))

        h_left = ctk.CTkFrame(hdr, fg_color="transparent")
        h_left.pack(side="left", fill="x", expand=True)

        title_row = ctk.CTkFrame(h_left, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="🕒", font=ctk.CTkFont(size=14),
            text_color=("#2563EB", "#60A5FA"),
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

        # Nút "Xem lịch sử →"
        ctk.CTkButton(
            hdr, text="Xem lịch sử  →",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=28, corner_radius=6,
            fg_color=("#EFF6FF", "#1E293B"),
            hover_color=CTK_SIDEBAR_HOVER,
            text_color=("#2563EB", "#60A5FA"),
            border_width=1, border_color=CTK_ACCENT,
            command=lambda: self._navigate("history"),
        ).pack(side="right")

        # Container các entry
        self._timeline_container = ctk.CTkFrame(card, fg_color="transparent")
        self._timeline_container.pack(fill="both", expand=True, padx=16, pady=(6, 12))

        self._render_timeline_entries()

    def _render_timeline_entries(self):
        """Render 5 bản ghi điểm danh gần nhất với Avatar + Status Badge chuẩn mockup."""
        ct = self._timeline_container
        if not ct.winfo_exists():
            return
        for child in ct.winfo_children():
            child.destroy()

        entries = getattr(self, "attendance_history", [])[:5]

        if not entries:
            ctk.CTkLabel(
                ct, text="Chưa có hoạt động nào được ghi nhận",
                font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM,
            ).pack(fill="both", expand=True, pady=40)
            return

        for i, rec in enumerate(entries):
            st = rec.get("status", "")
            is_success = "Thành công" in st
            is_reg = "Đăng ký" in st or "Mới" in st
            name = rec.get("name", "Người dùng")
            time_rel = self._relative_time_str(rec.get("time", ""))

            if is_success:
                desc = "Điểm danh thành công tại Kiosk A"
                dot_c = ("#10B981", "#22C55E")
                av_bg = ("#DCFCE7", "#064E3B")
                av_ic = ("#16A34A", "#4ADE80")
                badge_txt = "Thành công"
                badge_bg = ("#DCFCE7", "#064E3B")
                badge_tc = ("#16A34A", "#4ADE80")
            elif is_reg:
                desc = "Đăng ký khuôn mặt thành công"
                dot_c = ("#3B82F6", "#60A5FA")
                av_bg = ("#DBEAFE", "#1E3A5F")
                av_ic = ("#2563EB", "#60A5FA")
                badge_txt = "Đăng ký"
                badge_bg = ("#DBEAFE", "#1E3A5F")
                badge_tc = ("#2563EB", "#60A5FA")
            else:
                desc = "Không nhận diện được khuôn mặt"
                dot_c = ("#EF4444", "#F87171")
                av_bg = ("#FEE2E2", "#450A0A")
                av_ic = ("#DC2626", "#F87171")
                badge_txt = "Thất bại"
                badge_bg = ("#FEE2E2", "#450A0A")
                badge_tc = ("#DC2626", "#F87171")

            row = ctk.CTkFrame(ct, fg_color="transparent")
            row.pack(fill="x", pady=(2, 2))

            # Bên trái: Chấm dot + Avatar tròn + Tên & Mô tả
            left_f = ctk.CTkFrame(row, fg_color="transparent")
            left_f.pack(side="left", fill="both", expand=True)

            # Chấm timeline dot
            ctk.CTkFrame(
                left_f, width=7, height=7, corner_radius=4, fg_color=dot_c,
            ).pack(side="left", padx=(0, 8), pady=(6, 0))

            # Avatar tròn icon người dùng
            av = ctk.CTkFrame(left_f, width=30, height=30, corner_radius=15, fg_color=av_bg)
            av.pack(side="left", padx=(0, 9))
            av.pack_propagate(False)
            ctk.CTkLabel(
                av, text="👤", font=ctk.CTkFont(size=13), text_color=av_ic,
            ).place(relx=0.5, rely=0.5, anchor="center")

            # Cột text: Tên & Mô tả hoạt động
            name_box = ctk.CTkFrame(left_f, fg_color="transparent")
            name_box.pack(side="left", fill="both", expand=True)

            ctk.CTkLabel(
                name_box, text=name,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=CTK_TEXT, anchor="w",
            ).pack(fill="x")

            ctk.CTkLabel(
                name_box, text=desc, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM, anchor="w",
            ).pack(fill="x", pady=(1, 0))

            # Bên phải: Thời gian tương đối + Badge trạng thái
            right_f = ctk.CTkFrame(row, fg_color="transparent")
            right_f.pack(side="right")

            ctk.CTkLabel(
                right_f, text=time_rel, font=ctk.CTkFont(size=10),
                text_color=CTK_TEXT_DIM,
            ).pack(side="left", padx=(0, 10))

            ctk.CTkLabel(
                right_f, text=badge_txt,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=badge_tc, fg_color=badge_bg,
                corner_radius=6, width=74, height=24,
            ).pack(side="left")

            # Đường phân cách mảnh giữa các entry
            if i < len(entries) - 1:
                ctk.CTkFrame(
                    ct, fg_color=CTK_ACCENT, height=1,
                ).pack(fill="x", pady=(3, 3))

    # ══════════════════════════════════════════════════════════
    # ROW 3 LEFT: LỐI TẮT THAO TÁC NHANH
    # ══════════════════════════════════════════════════════════

    def _build_quick_actions_card(self, parent):
        """Card chứa 4 nút lối tắt thao tác nhanh chuẩn phong cách mockup."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(14, 10))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="⊞", font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#2563EB", "#60A5FA"),
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

        # ── 4 Thẻ Lối Tắt ──
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 14))
        btn_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="qa")

        def make_action(col, bg_col, hover_col, is_outline, cmd, content_builder):
            f = ctk.CTkFrame(
                btn_row, fg_color=bg_col, corner_radius=10, cursor="hand2",
            )
            if is_outline:
                f.configure(border_width=1, border_color=CTK_ACCENT)
            f.grid(
                row=0, column=col, sticky="nsew",
                padx=(0 if col == 0 else 4, 0 if col == 3 else 4),
            )

            inner = ctk.CTkFrame(f, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=10, pady=10)

            all_widgets = [f, inner]
            content_builder(inner, all_widgets)

            click_cb = lambda e, c=cmd: c()
            for w in all_widgets:
                w.bind("<Button-1>", click_cb)

        # --- Nút 1: Vào Kiosk Điểm danh (Solid Blue #2563EB) ---
        def build_card_kiosk(inner, widgets):
            top_bar = ctk.CTkFrame(inner, fg_color="transparent")
            top_bar.pack(fill="x")
            widgets.append(top_bar)

            ic = ctk.CTkLabel(top_bar, text="⛶", font=ctk.CTkFont(size=16), text_color="#FFFFFF")
            ic.pack(side="left")
            widgets.append(ic)

            arr_bg = ctk.CTkFrame(top_bar, width=22, height=22, corner_radius=11, fg_color=("#1D4ED8", "#1D4ED8"))
            arr_bg.pack(side="right")
            arr_bg.pack_propagate(False)
            widgets.append(arr_bg)
            arr = ctk.CTkLabel(arr_bg, text="→", font=ctk.CTkFont(size=12, weight="bold"), text_color="#FFFFFF")
            arr.place(relx=0.5, rely=0.5, anchor="center")
            widgets.append(arr)

            t1 = ctk.CTkLabel(
                inner, text="Vào Kiosk\nĐiểm danh",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#FFFFFF", anchor="w", justify="left",
            )
            t1.pack(fill="x", pady=(6, 2))
            widgets.append(t1)

            t2 = ctk.CTkLabel(
                inner, text="Bắt đầu điểm danh ngay",
                font=ctk.CTkFont(size=9), text_color="#DBEAFE", anchor="w",
            )
            t2.pack(fill="x")
            widgets.append(t2)

        make_action(0, ("#2563EB", "#2563EB"), ("#1D4ED8", "#1D4ED8"), False,
                    lambda: self._navigate("attendance"), build_card_kiosk)

        # --- Nút 2: Đăng ký mới (Solid Green #10B981) ---
        def build_card_reg(inner, widgets):
            top_bar = ctk.CTkFrame(inner, fg_color="transparent")
            top_bar.pack(fill="x")
            widgets.append(top_bar)

            ic = ctk.CTkLabel(top_bar, text="➕", font=ctk.CTkFont(size=15), text_color="#FFFFFF")
            ic.pack(side="left")
            widgets.append(ic)

            t1 = ctk.CTkLabel(
                inner, text="Đăng ký mới",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF", anchor="w",
            )
            t1.pack(fill="x", pady=(12, 2))
            widgets.append(t1)

            t2 = ctk.CTkLabel(
                inner, text="Thêm người dùng",
                font=ctk.CTkFont(size=9), text_color="#D1FAE5", anchor="w",
            )
            t2.pack(fill="x")
            widgets.append(t2)

        make_action(1, ("#10B981", "#059669"), ("#059669", "#047857"), False,
                    lambda: self._navigate("add_employee"), build_card_reg)

        # --- Nút 3: Quản lý Người đăng ký (Surface White + Border) ---
        def build_card_users(inner, widgets):
            h_row = ctk.CTkFrame(inner, fg_color="transparent")
            h_row.pack(fill="both", expand=True)
            widgets.append(h_row)

            # Icon hộp vuông xanh dương nhạt
            ib = ctk.CTkFrame(h_row, width=32, height=32, corner_radius=8, fg_color=("#EFF6FF", "#1E3A5F"))
            ib.pack(side="left", padx=(0, 8))
            ib.pack_propagate(False)
            widgets.append(ib)
            ic = ctk.CTkLabel(ib, text="👥", font=ctk.CTkFont(size=13), text_color=("#2563EB", "#60A5FA"))
            ic.place(relx=0.5, rely=0.5, anchor="center")
            widgets.append(ic)

            txt_f = ctk.CTkFrame(h_row, fg_color="transparent")
            txt_f.pack(side="left", fill="both", expand=True)
            widgets.append(txt_f)

            sub = ctk.CTkLabel(txt_f, text="Quản lý", font=ctk.CTkFont(size=9), text_color=CTK_TEXT_DIM, anchor="w")
            sub.pack(fill="x")
            widgets.append(sub)

            tit = ctk.CTkLabel(txt_f, text="Người đăng ký", font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w")
            tit.pack(fill="x")
            widgets.append(tit)

            desc = ctk.CTkLabel(txt_f, text="Xem và chỉnh sửa", font=ctk.CTkFont(size=9), text_color=CTK_TEXT_DIM, anchor="w")
            desc.pack(fill="x")
            widgets.append(desc)

            chv = ctk.CTkLabel(h_row, text="›", font=ctk.CTkFont(size=16), text_color=CTK_TEXT_DIM)
            chv.pack(side="right")
            widgets.append(chv)

        make_action(2, ("#FFFFFF", "#1E293B"), CTK_SIDEBAR_HOVER, True,
                    lambda: self._navigate("database"), build_card_users)

        # --- Nút 4: Xem lịch sử (Surface White + Border) ---
        def build_card_hist(inner, widgets):
            h_row = ctk.CTkFrame(inner, fg_color="transparent")
            h_row.pack(fill="both", expand=True)
            widgets.append(h_row)

            # Icon hộp vuông xanh dương nhạt
            ib = ctk.CTkFrame(h_row, width=32, height=32, corner_radius=8, fg_color=("#EFF6FF", "#1E3A5F"))
            ib.pack(side="left", padx=(0, 8))
            ib.pack_propagate(False)
            widgets.append(ib)
            ic = ctk.CTkLabel(ib, text="📋", font=ctk.CTkFont(size=13), text_color=("#2563EB", "#60A5FA"))
            ic.place(relx=0.5, rely=0.5, anchor="center")
            widgets.append(ic)

            txt_f = ctk.CTkFrame(h_row, fg_color="transparent")
            txt_f.pack(side="left", fill="both", expand=True)
            widgets.append(txt_f)

            tit = ctk.CTkLabel(txt_f, text="Xem lịch sử", font=ctk.CTkFont(size=12, weight="bold"), text_color=CTK_TEXT, anchor="w")
            tit.pack(fill="x", pady=(2, 0))
            widgets.append(tit)

            desc = ctk.CTkLabel(txt_f, text="Tra cứu ra vào", font=ctk.CTkFont(size=9), text_color=CTK_TEXT_DIM, anchor="w")
            desc.pack(fill="x", pady=(2, 0))
            widgets.append(desc)

            chv = ctk.CTkLabel(h_row, text="›", font=ctk.CTkFont(size=16), text_color=CTK_TEXT_DIM)
            chv.pack(side="right")
            widgets.append(chv)

        make_action(3, ("#FFFFFF", "#1E293B"), CTK_SIDEBAR_HOVER, True,
                    lambda: self._navigate("history"), build_card_hist)

    # ══════════════════════════════════════════════════════════
    # ROW 3 RIGHT: DONUT CHART – TỶ LỆ ĐIỂM DANH THÀNH CÔNG
    # ══════════════════════════════════════════════════════════

    def _build_donut_chart_card(self, parent):
        """Card biểu đồ tròn tỷ lệ điểm danh thành công kèm Legend chi tiết."""
        card = ctk.CTkFrame(
            parent, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT,
        )
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(14, 4))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text="🎯", font=ctk.CTkFont(size=14),
            text_color=("#2563EB", "#60A5FA"),
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

        # ── Nội dung: Donut Canvas + Legend ──
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=(6, 14))

        is_dark = ctk.get_appearance_mode().lower() == "dark"
        canvas_bg = "#1E293B" if is_dark else "#FFFFFF"

        self._donut_canvas = tk.Canvas(
            content, width=128, height=128,
            bg=canvas_bg, highlightthickness=0, bd=0,
        )
        self._donut_canvas.pack(side="left", padx=(6, 12))

        self._donut_legend = ctk.CTkFrame(content, fg_color="transparent")
        self._donut_legend.pack(side="left", fill="both", expand=True)

        self._donut_canvas.bind(
            "<Configure>", lambda e: self._schedule_donut_redraw(),
        )
        self.after(150, self._draw_donut_chart)

    def _schedule_donut_redraw(self):
        """Debounce redraw khi Donut Canvas thay đổi kích thước."""
        if hasattr(self, '_dcr_id'):
            self.after_cancel(self._dcr_id)
        self._dcr_id = self.after(60, self._draw_donut_chart)

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

        dk = ctk.get_appearance_mode().lower() == "dark"
        bg      = "#1E293B" if dk else "#FFFFFF"
        txt_c   = "#F8FAFC" if dk else "#1F2937"
        dim_c   = "#94A3B8" if dk else "#6B7280"
        ok_c    = "#2563EB"
        fail_c  = "#EF4444"
        empty_c = "#334155" if dk else "#E5E7EB"
        canvas.configure(bg=bg)

        success, fail, total, rate = self._today_attendance_stats()

        cx, cy = cw / 2, ch / 2
        outer_r = min(cw, ch) / 2 - 6
        inner_r = outer_r * 0.68

        if total > 0:
            s_angle = 360 * success / total
        else:
            s_angle = 0

        # Vẽ vòng cung
        if total > 0:
            canvas.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=90, extent=-s_angle, fill=ok_c, outline="",
            )
            if fail > 0:
                canvas.create_arc(
                    cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                    start=90 - s_angle, extent=-(360 - s_angle),
                    fill=fail_c, outline="",
                )
        else:
            canvas.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=0, extent=360, fill=empty_c, outline="",
            )

        # Lỗ giữa của Donut
        canvas.create_oval(
            cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r,
            fill=bg, outline="",
        )

        # Số % ở tâm
        canvas.create_text(
            cx, cy - 8, text=f"{rate}%",
            font=("Segoe UI", 16, "bold"), fill=txt_c,
        )
        canvas.create_text(
            cx, cy + 12, text="Thành công",
            font=("Segoe UI", 9), fill=dim_c,
        )

        # ── Cập nhật Legend ──
        legend = self._donut_legend
        if not legend.winfo_exists():
            return
        for child in legend.winfo_children():
            child.destroy()

        fail_rate = 100 - rate if total > 0 else 0

        # Mục 1: Thành công
        s_row = ctk.CTkFrame(legend, fg_color="transparent")
        s_row.pack(fill="x", pady=(10, 0))
        ctk.CTkFrame(
            s_row, width=8, height=8, corner_radius=4, fg_color=ok_c,
        ).pack(side="left", padx=(0, 8), pady=(4, 0))
        ctk.CTkLabel(
            s_row, text="Điểm danh thành công",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            s_row, text=f"{rate}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_TEXT, anchor="e",
        ).pack(side="right")

        ctk.CTkLabel(
            legend, text=f"{success} lượt",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=(16, 0), pady=(0, 8))

        # Mục 2: Thất bại
        f_row = ctk.CTkFrame(legend, fg_color="transparent")
        f_row.pack(fill="x")
        ctk.CTkFrame(
            f_row, width=8, height=8, corner_radius=4, fg_color=fail_c,
        ).pack(side="left", padx=(0, 8), pady=(4, 0))
        ctk.CTkLabel(
            f_row, text="Chưa thành công",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            f_row, text=f"{fail_rate}%",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=CTK_TEXT, anchor="e",
        ).pack(side="right")

        ctk.CTkLabel(
            legend, text=f"{fail} lượt",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", padx=(16, 0))

    # ══════════════════════════════════════════════════════════
    # REFRESH – CẬP NHẬT SỐ LIỆU DASHBOARD
    # ══════════════════════════════════════════════════════════

    def _reload_dashboard_stats(self):
        """Cập nhật lại toàn bộ số liệu trên Dashboard khi chuyển tab hoặc có bản ghi mới."""
        # Cập nhật Stat Cards
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

        # Cập nhật Charts
        if hasattr(self, '_bar_chart_canvas'):
            self._draw_bar_chart()
        if hasattr(self, '_donut_canvas'):
            self._draw_donut_chart()

        # Cập nhật Timeline
        if hasattr(self, '_timeline_container'):
            try:
                if self._timeline_container.winfo_exists():
                    self._render_timeline_entries()
            except Exception:
                pass
