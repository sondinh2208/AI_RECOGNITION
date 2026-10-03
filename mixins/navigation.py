"""
==============================================================
NAVIGATION MIXIN
Thanh điều hướng bên trái (Sidebar), Header Bar phía trên,
quản lý chuyển tab/trang và đồng hồ số thời gian thực.
==============================================================
"""

import threading
from datetime import datetime
import customtkinter as ctk

from config import (
    CTK_BG_DARK, CTK_BG_MAIN, CTK_ACCENT, CTK_PRIMARY, CTK_SUCCESS, CTK_TEXT,
    CTK_TEXT_DIM, CTK_SIDEBAR_HOVER, CTK_BTN_ACTIVE, CTK_BTN_ACTIVE_TEXT, ADMIN_SIDEBAR_WIDTH,
)


class NavigationMixin:
    """Mixin quản lý điều hướng, sidebar, header và chuyển đổi tab."""

    def _toggle_appearance_mode(self):
        """Chuyển đổi linh hoạt giữa giao diện Sáng (Light) và Tối (Dark)."""
        cur = ctk.get_appearance_mode().lower()
        new_mode = "Dark" if cur == "light" else "Light"
        ctk.set_appearance_mode(new_mode)
        if hasattr(self, '_theme_toggle_btn'):
            self._theme_toggle_btn.configure(
                text="☀️ Giao diện sáng" if new_mode == "Dark" else "🌙 Giao diện tối"
            )

    def _build_sidebar(self):
        """Xây dựng thanh sidebar bên trái khớp 100% bản thiết kế doanh nghiệp."""
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
        
        # --- Logo FaceCheck Header (Professional badge + text) ---
        logo_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_container.pack(fill="x", padx=16, pady=(20, 18))
        
        # Badge chuyên nghiệp: khung tròn gradient-style với viền mỏng
        badge_outer = ctk.CTkFrame(
            logo_container, width=42, height=42, corner_radius=12,
            fg_color=("#DBEAFE", "#1E3A5F"),
        )
        badge_outer.pack(side="left", padx=(0, 11))
        badge_outer.pack_propagate(False)
        
        badge_inner = ctk.CTkFrame(
            badge_outer, width=34, height=34, corner_radius=9,
            fg_color=("#2563EB", "#3B82F6"),
        )
        badge_inner.place(relx=0.5, rely=0.5, anchor="center")
        badge_inner.pack_propagate(False)
        
        ctk.CTkLabel(
            badge_inner, text="⬡",
            font=ctk.CTkFont(family="Segoe UI Symbol", size=18, weight="bold"),
            text_color="#FFFFFF",
        ).place(relx=0.5, rely=0.5, anchor="center")
        
        logo_text_frame = ctk.CTkFrame(logo_container, fg_color="transparent")
        logo_text_frame.pack(side="left", fill="both", expand=True)
        
        ctk.CTkLabel(
            logo_text_frame, text="FaceCheck",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(fill="x")
        
        ctk.CTkLabel(
            logo_text_frame, text="Hệ thống quản lý chấm công",
            font=ctk.CTkFont(size=10),
            text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x")
        
        # --- Danh mục điều hướng chuẩn Mockup ---
        self.nav_buttons = {}
        self.nav_rows = {}
        nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav_container.pack(fill="x", padx=9)
        self._nav_active_indicator = ctk.CTkFrame(
            nav_container, width=3, height=32, corner_radius=2,
            fg_color=CTK_BTN_ACTIVE,
        )
        self._nav_container = nav_container
        nav_items = [
            ("dashboard",     "📊  Bảng điều khiển"),
            ("add_employee",  "👤  Quét khuôn mặt"),
            ("attendance",    "📍  Nhận diện điểm danh"),
            ("database",      "📁  Người đăng ký"),
            ("history",       "📋  Lịch sử ra vào"),
        ]
        
        for page_id, label in nav_items:
            nav_row = ctk.CTkFrame(nav_container, height=46, fg_color="transparent")
            nav_row.pack(fill="x")
            nav_row.pack_propagate(False)
            btn = ctk.CTkButton(
                nav_row, text=label,
                font=ctk.CTkFont(size=13), height=40, anchor="w",
                corner_radius=6, fg_color="transparent",
                text_color=CTK_TEXT, hover_color=CTK_SIDEBAR_HOVER,
                command=lambda pid=page_id: self._navigate(pid),
            )
            btn.pack(fill="x", padx=(7, 3), pady=3)
            self.nav_buttons[page_id] = btn
            self.nav_rows[page_id] = nav_row
        
        self._highlight_nav(self.current_page)
        self.after_idle(lambda: self._move_nav_indicator(self.current_page, animate=False))
        
        # --- Spacer giãn cách ---
        ctk.CTkFrame(self.sidebar, fg_color="transparent").pack(fill="both", expand=True)
        
        # --- Footer Widget Trạng thái hệ thống (Khớp chuẩn góc dưới bên trái mockup) ---
        status_box = ctk.CTkFrame(
            self.sidebar, corner_radius=10,
            fg_color=("#F9FAFB", "#0F172A"), border_width=1, border_color=CTK_ACCENT
        )
        status_box.pack(fill="x", padx=14, pady=(0, 6))
        
        # Header box: icon + tiêu đề
        s_hdr = ctk.CTkFrame(status_box, fg_color="transparent")
        s_hdr.pack(fill="x", padx=10, pady=(8, 4))
        
        s_ib = ctk.CTkFrame(s_hdr, width=26, height=26, corner_radius=6, fg_color=("#DCFCE7", "#064E3B"))
        s_ib.pack(side="left", padx=(0, 8))
        s_ib.pack_propagate(False)
        ctk.CTkLabel(s_ib, text="🖥", font=ctk.CTkFont(size=11), text_color="#10B981").place(relx=0.5, rely=0.5, anchor="center")
        
        s_title_f = ctk.CTkFrame(s_hdr, fg_color="transparent")
        s_title_f.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(s_title_f, text="Trạng thái vận hành", font=ctk.CTkFont(size=11, weight="bold"), text_color=CTK_TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(s_title_f, text="Kết nối ổn định", font=ctk.CTkFont(size=9), text_color=CTK_TEXT_DIM, anchor="w").pack(fill="x")
        
        # Divider
        ctk.CTkFrame(status_box, height=1, fg_color=CTK_ACCENT).pack(fill="x", padx=8, pady=(2, 4))
        
        # 3 Rows: Camera, Thiết bị, Dữ liệu
        items = [
            ("Camera", "Hoạt động"),
            ("Thiết bị", "Bình thường"),
            ("Dữ liệu", "Đồng bộ tốt"),
        ]
        for label, val in items:
            row = ctk.CTkFrame(status_box, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=1)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w").pack(side="left")
            val_f = ctk.CTkFrame(row, fg_color="transparent")
            val_f.pack(side="right")
            ctk.CTkLabel(val_f, text="● ", font=ctk.CTkFont(size=7, weight="bold"), text_color="#10B981").pack(side="left")
            ctk.CTkLabel(val_f, text=val, font=ctk.CTkFont(size=10, weight="bold"), text_color=CTK_TEXT).pack(side="left")
        
        ctk.CTkFrame(status_box, height=4, fg_color="transparent").pack()
        
        # Version label
        ctk.CTkLabel(
            self.sidebar, text="v1.0.0",
            font=ctk.CTkFont(size=10), text_color=CTK_TEXT_DIM, anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 8))
        
    def _highlight_nav(self, active_page_id):
        """Đổi màu nút đang active trên sidebar theo chuẩn mockup (Image 2: Nền Cyan, chữ trắng đậm)."""
        for pid, btn in self.nav_buttons.items():
            if pid == active_page_id:
                btn.configure(
                    fg_color=("#EAF4FF", "#0C4A6E"),
                    hover_color=("#DCEEFF", "#075985"),
                    text_color=("#0369A1", "#7DD3FC"),
                    font=ctk.CTkFont(size=13, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=CTK_SIDEBAR_HOVER,
                    text_color=("#4B5563", "#CBD5E1"),
                    font=ctk.CTkFont(size=13),
                )

    def _move_nav_indicator(self, page_id, animate=True):
        """Trượt vạch active đến tab mới mà không chặn mainloop."""
        indicator = getattr(self, '_nav_active_indicator', None)
        row = getattr(self, 'nav_rows', {}).get(page_id)
        nav_container = getattr(self, '_nav_container', None)
        if indicator is None or row is None or not row.winfo_exists():
            return
        if nav_container is None or not nav_container.winfo_exists():
            return

        # Tính vị trí chính xác bằng tọa độ tuyệt đối rooty rồi quy về tương đối
        # Đảm bảo geometry đã sẵn sàng
        row.update_idletasks()
        nav_container.update_idletasks()

        container_y = nav_container.winfo_rooty()
        row_y = row.winfo_rooty()
        row_h = row.winfo_height()
        indicator_h = 32
        target_y = (row_y - container_y) + max(0, (row_h - indicator_h) // 2)

        current_y = indicator.winfo_y() if indicator.winfo_manager() else target_y
        animation_id = getattr(self, '_nav_indicator_animation_id', 0) + 1
        self._nav_indicator_animation_id = animation_id

        if not animate or current_y == target_y:
            indicator.place(x=0, y=target_y)
            indicator.lift()
            return

        steps = 9

        def _step(index=1):
            if animation_id != getattr(self, '_nav_indicator_animation_id', None):
                return
            progress = min(1.0, index / steps)
            eased = 1 - (1 - progress) ** 3
            y = round(current_y + (target_y - current_y) * eased)
            indicator.place(x=0, y=y)
            indicator.lift()
            if index < steps:
                self.after(14, lambda: _step(index + 1))

        _step()

    def _show_navigation_progress(self):
        """Hiển thị vạch tiến trình mảnh trong lúc đổi trang."""
        line = getattr(self, '_navigation_progress_line', None)
        if line is None or not line.winfo_exists():
            return
        animation_id = getattr(self, '_navigation_progress_animation_id', 0) + 1
        self._navigation_progress_animation_id = animation_id
        line.place(x=0, y=0, relwidth=0.10)
        line.lift()
        widths = (0.28, 0.52, 0.72, 0.86)

        def _step(index=0):
            if animation_id != getattr(self, '_navigation_progress_animation_id', None):
                return
            if index < len(widths):
                line.place_configure(relwidth=widths[index])
                self.after(22, lambda: _step(index + 1))

        self.after(16, _step)

    def _finish_navigation_progress(self):
        line = getattr(self, '_navigation_progress_line', None)
        if line is None or not line.winfo_exists():
            self._navigation_transition_busy = False
            return
        animation_id = getattr(self, '_navigation_progress_animation_id', 0)
        line.place_configure(relwidth=1.0)
        line.lift()

        def _hide():
            if animation_id == getattr(self, '_navigation_progress_animation_id', None):
                line.place_forget()
            self._navigation_transition_busy = False

        self.after(55, _hide)

    def _navigate(self, page_id):
        """Xử lý chuyển trang."""
        if (page_id == getattr(self, '_active_visible_page', None)
                or getattr(self, '_navigation_transition_busy', False)):
            return
        self._navigation_transition_busy = True
        self.current_page = page_id
        self._highlight_nav(page_id)
        self._move_nav_indicator(page_id)
        self._show_navigation_progress()
        print(f"[NAV] Chuyển đến trang: {page_id}")
        # Nhường cho Tk một nhịp vẽ phản hồi click trước khi đổi trang.
        def _perform_navigation():
            try:
                self._show_page(page_id)
            finally:
                self._finish_navigation_progress()

        self.after(18, _perform_navigation)

    def _get_or_create_page(self, page_id):
        """
        Page Cache & Lazy Initialization (Create Once):
        Chỉ tạo trang đúng 1 lần duy nhất khi người dùng mở tab.
        Sau khi tạo xong, lưu vào cache self.page_frames để tái sử dụng tức thì.
        """
        if not hasattr(self, 'page_frames'):
            self.page_frames = {}
            
        if page_id in self.page_frames:
            return self.page_frames[page_id]
            
        if page_id == "attendance":
            self._build_attendance_page()
            self.page_frames["attendance"] = self.attendance_frame
        elif page_id == "add_employee":
            self.main_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)
            self.main_frame.grid_columnconfigure(0, weight=1)
            self.main_frame.grid_columnconfigure(1, weight=0)
            self.main_frame.grid_rowconfigure(0, weight=1)
            self._build_form_column()
            self._build_camera_column()
            self.page_frames["add_employee"] = self.main_frame
        elif page_id == "database":
            self.database_frame = ctk.CTkFrame(self.pages_container, fg_color=CTK_BG_MAIN)
            self._build_database_page()
            self.page_frames["database"] = self.database_frame
        elif page_id == "history":
            self._build_history_page()
            self.page_frames["history"] = self.history_frame
        elif page_id == "dashboard":
            self._build_dashboard_page()
            self.page_frames["dashboard"] = self.dashboard_frame
            
        return self.page_frames.get(page_id)

    def _show_page(self, page_id):
        """
        Hiển thị frame được chọn từ Page Cache mượt mà trong 0ms:
        - Ẩn ngay trang cũ nếu có layout header khác biệt để chống bóng mờ/chồng lấn text.
        - update_idletasks() vẽ tức thì bề mặt màu chuẩn, triệt tiêu 100% khung đen.
        """
        self.current_page = page_id
        
        # 1. Điều chỉnh hiển thị Header Bar
        if hasattr(self, 'header_bar'):
            if page_id in ["database", "add_employee"]:
                self.header_bar.grid_remove()
            else:
                self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
                if hasattr(self, 'header_title_label'):
                    if page_id == "attendance":
                        self.header_title_label.configure(text="Nhận diện điểm danh")
                        self.header_sub_label.configure(text="Quét khuôn mặt để ghi nhận thời gian vào/ra")
                    elif page_id == "history":
                        self.header_title_label.configure(text="Lịch sử ra vào")
                        self.header_sub_label.configure(text="Xem toàn bộ dữ liệu ra vào của nhân sự")
                    elif page_id == "dashboard":
                        self.header_title_label.configure(text="Bảng điều khiển")
                        self.header_sub_label.configure(text="Tổng quan hoạt động điểm danh và quản lý nhân sự")
                    else:
                        self.header_title_label.configure(text="Cài đặt hệ thống")
                        self.header_sub_label.configure(text="Cấu hình hệ thống và tham số nhận diện")

        # Ghi nhận trạng thái cache trang và trang hiện tại trước khi chuyển
        is_already_cached = hasattr(self, 'page_frames') and (page_id in self.page_frames)
        old_pid = getattr(self, '_active_visible_page', None)

        # Dừng worker của trang cũ ngay khi click, không chờ callback deferred.
        if old_pid == "attendance" and page_id != "attendance":
            self.kiosk_running = False
            self._kiosk_thread_token = getattr(self, '_kiosk_thread_token', 0) + 1
        elif old_pid == "add_employee" and page_id != "add_employee":
            self.enrollment_running = False

        if page_id == "attendance":
            print("[NAV] Attendance click")
            print("[NAV] Attendance show start")

        # 2. Lấy trang từ Page Cache hoặc Lazy Init lần đầu
        target_page = self._get_or_create_page(page_id)
        
        if page_id == "attendance":
            if is_already_cached:
                print("[ATTENDANCE] page SHOW")
            else:
                print("[ATTENDANCE] page CREATE")

        # 3. Chuyển trang dứt khoát không để lọt bóng trang cũ
        if target_page is not None:
            if page_id == "attendance":
                print("[NAV] before attendance grid")
                print("[NAV] Attendance page grid")

            # Ẩn trang cũ ngay lập tức để không bị chồng chéo tiêu đề
            if old_pid and old_pid != page_id:
                old_frame = self.page_frames.get(old_pid)
                if old_frame:
                    old_frame.grid_remove()
            else:
                for pid, frame in self.page_frames.items():
                    if pid != page_id:
                        frame.grid_remove()

            target_page.grid(row=0, column=0, sticky="nswe")
            target_page.lift()

            if page_id == "attendance":
                print("[NAV] after attendance grid")
                print("[NAV] attendance grid complete")
                if hasattr(self, '_render_recent_attendance_table'):
                    self._render_recent_attendance_table()
                if hasattr(self, 'set_idle_state'):
                    self._is_kiosk_ui_idle = False
                    self.set_idle_state()
                if getattr(self, 'kiosk_latest_pil', None) is None and hasattr(self, '_show_kiosk_cam_loading'):
                    self._show_kiosk_cam_loading("Đang khởi động camera...")
            elif page_id == "history":
                if hasattr(self, '_reload_full_history'):
                    self._reload_full_history()

        self._active_visible_page = page_id

        # 4. Hậu kỳ non-blocking
        if page_id == "attendance":
            print("[ATTENDANCE] page visible")
            self._first_valid_frame_logged = False
            print("[NAV] Attendance show end")
            
            # Giai đoạn 2: Lắng nghe after_idle để khởi động camera worker
            def _on_attendance_idle_repaint():
                print("[NAV] attendance after_idle reached")
                print("[NAV] Attendance deferred start")
                self._on_attendance_deferred_start()

            self.after_idle(_on_attendance_idle_repaint)
        else:
            self._safe_after(25, lambda pid=page_id: self._on_page_switched_deferred(pid))

    def _on_attendance_deferred_start(self):
        """Khởi động camera và xử lý nghiệp vụ Kiosk sau khi Tkinter đã paint xong hoàn toàn giao diện Attendance."""
        if self.current_page != "attendance":
            return
            
        print("[ATTENDANCE] deferred initialization start")
        self.enrollment_running = False
        self.kiosk_running = True
        
        print("[ATTENDANCE] camera start requested")
        if self.camera_cap is None:
            self._start_camera()
        else:
            if hasattr(self, '_start_kiosk_worker'):
                self._start_kiosk_worker()
            else:
                self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                self.kiosk_thread.start()
            self._update_kiosk_frame()

    def _on_page_switched_deferred(self, page_id):
        """Giai đoạn 2 của Render-First cho các tab khác."""
        if self.current_page != page_id:
            return
            
        # Nạp dữ liệu database ở background nếu cần
        if page_id == "database" and hasattr(self, "search_entry"):
            if not getattr(self, "db_data_loaded", False):
                self._load_database_to_scrollable(self.search_entry.get().lower())
        elif page_id == "dashboard" and hasattr(self, "_reload_dashboard_stats"):
            self._reload_dashboard_stats()
        # Điều phối luồng Camera Non-blocking
        if page_id == "attendance":
            self._on_attendance_deferred_start()
        elif page_id == "add_employee":
            self.kiosk_running = False
            self.enrollment_running = True
            self.kiosk_latest_frame = None
            self.kiosk_latest_pil = None
            self._first_valid_frame_logged = False
            self._kiosk_loading_state_visible = False
            
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
            self.kiosk_latest_frame = None
            self.kiosk_latest_pil = None
            self._first_valid_frame_logged = False
            self._kiosk_loading_state_visible = False

    def _build_main_area(self):
        """Xây dựng khu vực chính gồm Header Bar và Container các Trang."""
        self.main_container = ctk.CTkFrame(self, fg_color=CTK_BG_MAIN)
        self.main_container.grid(row=0, column=1, sticky="nswe", padx=18, pady=(16, 16))
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=0)  # Top App Bar
        self.main_container.grid_rowconfigure(1, weight=1)  # Pages Container
        
        # ==========================================
        # TOP APP BAR (Tiêu đề, Đồng hồ, Profile Admin)
        # ==========================================
        self.header_bar = ctk.CTkFrame(self.main_container, fg_color=CTK_BG_MAIN, height=56)
        self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.header_bar.grid_columnconfigure(0, weight=1)
        self.header_bar.grid_columnconfigure(1, weight=0)
        
        # Left: Title + Subtitle (Clean Enterprise Header)
        header_left = ctk.CTkFrame(self.header_bar, fg_color="transparent")
        header_left.grid(row=0, column=0, sticky="w")
        
        self.header_title_label = ctk.CTkLabel(
            header_left, text="Nhận diện điểm danh",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CTK_TEXT, anchor="w"
        )
        self.header_title_label.pack(anchor="w")
        
        self.header_sub_label = ctk.CTkLabel(
            header_left, text="Quét khuôn mặt để ghi nhận thời gian vào/ra",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w"
        )
        self.header_sub_label.pack(anchor="w", pady=(2, 0))
        
        # Right: Action Button (+ Đăng ký mới) + Date + Clock
        header_right = ctk.CTkFrame(self.header_bar, fg_color="transparent")
        header_right.grid(row=0, column=1, sticky="e")
        
        # Nút "+ Đăng ký mới" trực tiếp trên Header Bar
        self.btn_header_register = ctk.CTkButton(
            header_right,
            text="+ Đăng ký mới",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36,
            corner_radius=8,
            fg_color=("#2563EB", "#2563EB"),
            hover_color=("#1D4ED8", "#1D4ED8"),
            text_color="#FFFFFF",
            command=lambda: self._navigate("add_employee"),
        )
        self.btn_header_register.pack(side="left", padx=(0, 16))
        
        self.header_date_label = ctk.CTkLabel(
            header_right, text="Thứ Năm, 01/10/2026",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="e"
        )
        self.header_date_label.pack(side="left", padx=(0, 14))

        # Ngăn cách ngày và giờ bằng một đường mảnh, tránh cảm giác như ô nhập liệu.
        clock_divider = ctk.CTkFrame(
            header_right, width=1, height=22, corner_radius=0,
            fg_color=CTK_ACCENT,
        )
        clock_divider.pack(side="left", padx=(0, 12))

        clock_pill = ctk.CTkFrame(
            header_right, height=34, corner_radius=9,
            fg_color=("#EAF2FF", "#172554"),
        )
        clock_pill.pack(side="left")

        ctk.CTkLabel(
            clock_pill, text="◷",
            font=ctk.CTkFont(family="Segoe UI Symbol", size=15, weight="bold"),
            text_color=("#2563EB", "#60A5FA"), width=18,
        ).pack(side="left", padx=(9, 3), pady=5)

        self.header_clock_label = ctk.CTkLabel(
            clock_pill, text="10:05:57",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=("#0F172A", "#F8FAFC"),
        )
        self.header_clock_label.pack(side="left", padx=(0, 10), pady=5)
        
        # ==========================================
        # PAGES CONTAINER & PAGE CACHE
        # ==========================================
        self.pages_container = ctk.CTkFrame(self.main_container, fg_color=CTK_BG_MAIN)
        self.pages_container.grid(row=1, column=0, sticky="nswe")
        self.pages_container.grid_columnconfigure(0, weight=1)
        self.pages_container.grid_rowconfigure(0, weight=1)

        # Phản hồi thị giác nhẹ hơn fade/slide toàn trang và không làm camera giật.
        self._navigation_progress_line = ctk.CTkFrame(
            self.pages_container, height=2, corner_radius=0,
            fg_color=CTK_BTN_ACTIVE,
        )
        self._navigation_transition_busy = False
        
        # Cache các trang (Create Once)
        self.page_frames = {}
        
        # Lazy Initialization: Dựng trước trang Kiosk Điểm danh để ứng dụng khởi động tức thì
        self._get_or_create_page("attendance")
        self._show_page("attendance")
        
        # Pre-warm từng trang theo nhịp riêng để không khóa UI hàng trăm ms một lần.
        pages_to_prewarm = ["database", "history", "dashboard", "add_employee"]

        def _prewarm_next_page(index=0):
            if index >= len(pages_to_prewarm):
                return
            pid = pages_to_prewarm[index]
            if pid not in self.page_frames:
                self._get_or_create_page(pid)
            self.after(350, lambda: _prewarm_next_page(index + 1))

        self.after(900, _prewarm_next_page)

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
