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
    CTK_BG_DARK, CTK_ACCENT, CTK_PRIMARY, CTK_SUCCESS, CTK_TEXT,
    CTK_TEXT_DIM, CTK_SIDEBAR_HOVER, CTK_BTN_ACTIVE, ADMIN_SIDEBAR_WIDTH,
)


class NavigationMixin:
    """Mixin quản lý điều hướng, sidebar, header và chuyển đổi tab."""

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
            self.main_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
            self.main_frame.grid_columnconfigure(0, weight=1)
            self.main_frame.grid_columnconfigure(1, weight=0)
            self.main_frame.grid_rowconfigure(0, weight=1)
            self._build_form_column()
            self._build_camera_column()
            self.page_frames["add_employee"] = self.main_frame
        elif page_id == "database":
            self.database_frame = ctk.CTkFrame(self.pages_container, fg_color="transparent")
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
        - Sử dụng Stacked Render-First (Grid + Lift trước, ẩn trang cũ sau qua after_idle).
        - Loại bỏ 100% hiện tượng flash/black void khi chuyển tab.
        - Không block UI Thread.
        - Tách bạch rõ 3 mốc: GRID -> PAINT (after_idle) -> CAMERA START.
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

        # Ghi nhận trạng thái cache trang và trang hiện tại trước khi chuyển
        is_already_cached = hasattr(self, 'page_frames') and (page_id in self.page_frames)
        old_pid = getattr(self, '_active_visible_page', None)

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

        # 3. Stacked Transition: Grid trang mới TRƯỚC và LIFT lên trên cùng
        if target_page is not None:
            if page_id == "attendance":
                print("[NAV] before attendance grid")
                print("[NAV] Attendance page grid")

            target_page.grid(row=0, column=0, sticky="nswe")
            target_page.lift()

            if page_id == "attendance":
                print("[NAV] after attendance grid")
                print("[NAV] attendance grid complete")

            # Ẩn trang cũ một cách êm ái trên idle tick kế tiếp (sau khi trang mới đã vẽ phủ lên trên)
            # Giúp triệt tiêu hoàn toàn khoảng hở màu đen (Black Void) do container transparent
            if old_pid and old_pid != page_id:
                old_frame = self.page_frames.get(old_pid)
                if old_frame:
                    def hide_old_frame(f=old_frame, pid=old_pid):
                        if self.current_page != pid:
                            f.grid_remove()
                    self.after_idle(hide_old_frame)
            else:
                for pid, frame in self.page_frames.items():
                    if pid != page_id:
                        frame.grid_remove()

        self._active_visible_page = page_id

        # 4. RENDER-FIRST: Nhường quyền ngay lập tức cho Tkinter Event Loop để repaint
        #    khung giao diện hoàn chỉnh trước, sau đó mới kích hoạt các tác vụ hậu kỳ.
        if page_id == "attendance":
            print("[NAV] Attendance show end")
            
            # Giai đoạn 2: Lắng nghe after_idle để biết chính xác thời điểm Tkinter hoàn thành repaint
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
        
        if hasattr(self, 'set_idle_state'):
            self.set_idle_state()
            
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
        # PAGES CONTAINER & PAGE CACHE
        # ==========================================
        self.pages_container = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.pages_container.grid(row=1, column=0, sticky="nswe")
        self.pages_container.grid_columnconfigure(0, weight=1)
        self.pages_container.grid_rowconfigure(0, weight=1)
        
        # Cache các trang (Create Once)
        self.page_frames = {}
        
        # Lazy Initialization: Chỉ dựng trước trang Kiosk Điểm danh để ứng dụng khởi động tức thì
        self._get_or_create_page("attendance")
        self._show_page("attendance")

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
