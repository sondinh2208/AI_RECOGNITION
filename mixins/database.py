"""
==============================================================
DATABASE MIXIN
Quản lý cơ sở dữ liệu nhân viên / người đăng ký:
- Thống kê tổng số lượng nhân viên, trạng thái đăng ký
- Bảng danh sách cuộn với ảnh đại diện, họ tên, mã NV, chức vụ
- Tìm kiếm theo thời gian thực (Search filter)
- Xóa hồ sơ nhân viên
==============================================================
"""

import os
import re
import pickle
import threading
from pathlib import Path
from PIL import Image
import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_PRIMARY, CTK_TEXT, CTK_TEXT_DIM,
    CTK_DANGER, CTK_SIDEBAR_HOVER, DATA_FACES_DIR,
)
from .ui_helpers import render_pagination_controls


class DatabaseMixin:
    """Mixin quản lý cơ sở dữ liệu hồ sơ nhân sự và người đăng ký."""

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
            width=280, height=38, corner_radius=8,
            border_color=CTK_ACCENT, fg_color=("#f8fafc", "#080e1a"),
            text_color=CTK_TEXT, placeholder_text_color=("#94a3b8", "#64748b")
        )
        self.search_entry.pack(side="left", padx=(0, 15))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)
        
        btn_add_new = ctk.CTkButton(
            btn_frame, text="+ Đăng ký mới",
            font=ctk.CTkFont(size=14, weight="bold"), height=38,
            corner_radius=8, fg_color=("#2563eb", "#2563eb"), hover_color=("#1d4ed8", "#1d4ed8"), text_color=("#ffffff", "#ffffff"),
            command=lambda: self._navigate("add_employee")
        )
        btn_add_new.pack(side="left")

        # --- Stats Cards Section ---
        stats_frame = ctk.CTkFrame(self.database_frame, fg_color="transparent")
        stats_frame.pack(fill="x", padx=25, pady=(0, 20))
        stats_frame.grid_columnconfigure((0,1,2,3), weight=1, uniform="card")
        
        # Helper to create card
        def create_stat_card(parent, col, icon, title, value, subtext, icon_color="#3b82f6"):
            card = ctk.CTkFrame(parent, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
            card.grid(row=0, column=col, sticky="nsew", padx=8 if col > 0 else (0, 8))
            
            icon_box = ctk.CTkFrame(card, width=45, height=45, corner_radius=10, fg_color=("#f1f5f9", "#101d30"))
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
        table_container = ctk.CTkFrame(self.database_frame, fg_color=CTK_CARD, corner_radius=12, border_width=1, border_color=CTK_ACCENT)
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
        self.db_page = 1
        self.db_page_size = 10
        self.db_all_records = []
        self.db_footer = ctk.CTkFrame(table_container, fg_color="transparent")
        self.db_footer.pack(fill="x", padx=20, pady=(10, 15))
        
        # Render Header trước để khung bảng hiển thị ngay lập tức (0ms)
        self._render_table_header()

    def _render_table_header(self):
        thead = ctk.CTkFrame(self.db_scroll, fg_color=("#f1f5f9", "#0b111e"), height=45, corner_radius=8, border_width=1, border_color=CTK_ACCENT)
        thead.pack(fill="x", padx=5, pady=(0, 6))
        
        headers = ["", "Ảnh", "Mã NV", "Họ và tên", "Chức vụ", "Trạng thái", "Hành động"]
        weights = [3, 5, 8, 24, 16, 14, 10]
        
        for i, w in enumerate(weights):
            thead.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        for i, text in enumerate(headers):
            if i == 0:
                chk = ctk.CTkCheckBox(thead, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color=CTK_ACCENT, fg_color="#2563EB")
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
        """Đọc file từ background thread và nạp vào UI mượt mà không block UI Thread."""
        if not hasattr(self, 'db_scroll') or not self.db_scroll.winfo_exists():
            return
            
        if not hasattr(self, '_db_load_gen'):
            self._db_load_gen = 0
        self._db_load_gen += 1
        current_gen = self._db_load_gen
        
        threading.Thread(
            target=self._worker_read_database_files,
            args=(query, current_gen),
            daemon=True
        ).start()

    def _worker_read_database_files(self, query, gen):
        """Worker thread: Quét đĩa, trích xuất metadata và preload/resize ảnh (chạy ngoài UI)."""
        data_dir = Path(DATA_FACES_DIR)
        image_files = list(data_dir.glob("*.jpg")) if data_dir.exists() else []
        total_files = len(image_files)
        
        records = []
        for img_path in image_files:
            filename = img_path.stem
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
                
            pil_img = None
            try:
                pil_img = Image.open(img_path).resize((35, 35))
            except Exception:
                pass
                
            records.append({
                "img_path": img_path,
                "pil_img": pil_img,
                "id": emp_id,
                "name": emp_name,
                "role": emp_role,
                "status": "Đã đăng ký"
            })
            
        self._safe_after(0, self._apply_database_results, records, total_files, gen)

    def _apply_database_results(self, records, total_files, gen):
        """Main UI Thread: Cập nhật stats và render các dòng dữ liệu với phân trang."""
        if getattr(self, '_db_load_gen', 0) != gen:
            return
        if not hasattr(self, 'db_scroll') or not self.db_scroll.winfo_exists():
            return
            
        if hasattr(self, 'lbl_total_emp'):
            self.lbl_total_emp.configure(text=str(total_files))
            self.lbl_registered.configure(text=str(total_files))
            self.lbl_pending.configure(text="0")
            self.lbl_new.configure(text=str(total_files))
            
        self.db_all_records = records
        self._render_database_page()
        self.db_data_loaded = True

    def _render_database_page(self):
        """Render các dòng thuộc trang hiện tại để tối ưu hiệu năng 0ms."""
        if not hasattr(self, 'db_scroll') or not self.db_scroll.winfo_exists():
            return
            
        for widget in self.db_scroll.winfo_children():
            widget.destroy()
            
        self._render_table_header()
        
        records = getattr(self, 'db_all_records', [])
        total_items = len(records)
        page_size = getattr(self, 'db_page_size', 10)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if not hasattr(self, 'db_page') or self.db_page > total_pages:
            self.db_page = 1
        elif self.db_page < 1:
            self.db_page = 1
            
        start_idx = (self.db_page - 1) * page_size
        end_idx = min(start_idx + page_size, total_items)
        page_records = records[start_idx:end_idx]
        
        for row_idx, emp_data in enumerate(page_records):
            self._render_employee_row(emp_data, row_idx)
            
        if hasattr(self, 'db_footer'):
            render_pagination_controls(
                parent=self.db_footer,
                current_page=self.db_page,
                total_pages=total_pages,
                on_page_change=self._on_db_page_change,
                page_size=self.db_page_size,
                page_size_options=["10", "20", "50"],
                on_size_change=self._on_db_size_change,
            )

    def _on_db_page_change(self, page_num):
        self.db_page = page_num
        self._render_database_page()

    def _on_db_size_change(self, new_size):
        self.db_page_size = new_size
        self.db_page = 1
        self._render_database_page()

    def _render_employee_row(self, emp_data, row_index):
        bg_color = ("#ffffff", "#0d1522") if row_index % 2 == 0 else ("#f8fafc", "#111c2e") 
        
        row_frame = ctk.CTkFrame(self.db_scroll, fg_color=bg_color, corner_radius=6)
        row_frame.pack(fill="x", pady=2, padx=5)
        
        weights = [3, 5, 8, 24, 16, 14, 10]
        for i, w in enumerate(weights):
            row_frame.grid_columnconfigure(i, weight=w, uniform="table_col")
            
        # 0. Checkbox
        checkbox = ctk.CTkCheckBox(row_frame, text="", width=24, checkbox_width=18, checkbox_height=18, corner_radius=4, border_width=1.5, border_color=CTK_ACCENT, fg_color="#2563EB")
        checkbox.grid(row=0, column=0, pady=8, padx=(10, 0), sticky="w")
        
        # 1. Ảnh
        try:
            pil_img = emp_data.get("pil_img")
            if pil_img is None:
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
        
        btn_view = ctk.CTkButton(action_frame, text="👁", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#3b82f6", "#60a5fa"), hover_color=CTK_SIDEBAR_HOVER)
        btn_view.pack(side="left", padx=2)
        
        btn_edit = ctk.CTkButton(action_frame, text="📝", width=30, height=28, fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#6b7280", "#cbd5e1"), hover_color=CTK_SIDEBAR_HOVER)
        btn_edit.pack(side="left", padx=2)
        
        btn_del = ctk.CTkButton(
            action_frame, text="🗑", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#ef4444", "#fca5a5"), hover_color=("#fee2e2", "#7f1d1d"),
            command=lambda: self._delete_single_employee(emp_data, row_frame)
        )
        btn_del.pack(side="left", padx=2)

    def _delete_single_employee(self, emp_info, row_frame=None):
        """
        Xóa hoàn toàn hồ sơ nhân viên:
        - Xóa ảnh trên đĩa ở tất cả các thư mục: data/faces, database/images, images.
        - Xóa vector đặc trưng khỏi file data/embeddings.pkl.
        - Đồng bộ ngay lập tức bộ nhớ RAM self.embeddings_cache.
        - Reset màn hình điểm danh nếu đang hiển thị người này.
        - Tải lại bảng danh sách và cập nhật số liệu thống kê.
        """
        emp_id = None
        img_path = None
        emp_name = "Nhân viên"
        
        if isinstance(emp_info, dict):
            emp_id = emp_info.get("id")
            img_path = emp_info.get("img_path")
            emp_name = emp_info.get("name", "Nhân viên")
        elif isinstance(emp_info, (str, Path)):
            p = Path(emp_info)
            if p.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                img_path = p
                stem = p.stem
                emp_id = stem.split("@")[0] if "@" in stem else stem.split("_")[0]
            else:
                emp_id = str(emp_info)
        
        print(f"[DB] Bắt đầu xóa nhân viên: ID={emp_id} ({emp_name})")
        
        # 1. Xóa tất cả các file ảnh liên quan ở các thư mục
        target_folders = [DATA_FACES_DIR, "database/images", "images"]
        for fld in target_folders:
            f_dir = Path(fld)
            if f_dir.exists() and emp_id and emp_id != "Unknown":
                for f in f_dir.glob(f"{emp_id}*"):
                    try:
                        f.unlink(missing_ok=True)
                        print(f"[DB] Đã xóa file ảnh: {f}")
                    except Exception as fe:
                        print(f"[DB] Lỗi xóa file ảnh {f}: {fe}")
                        
        if img_path:
            p_img = Path(img_path)
            if p_img.exists():
                try:
                    p_img.unlink(missing_ok=True)
                    print(f"[DB] Đã xóa ảnh gốc: {p_img}")
                except Exception as fe:
                    print(f"[DB] Lỗi xóa ảnh gốc {p_img}: {fe}")

        # 2. Xóa vector embedding khỏi data/embeddings.pkl
        emb_file = Path("data/embeddings.pkl")
        if emb_file.exists() and emp_id:
            try:
                with open(emb_file, "rb") as f:
                    embeddings_data = pickle.load(f)
                
                modified = False
                for k in list(embeddings_data.keys()):
                    if str(k).strip().lower() == str(emp_id).strip().lower():
                        del embeddings_data[k]
                        modified = True
                        print(f"[DB] Đã xóa vector của {k} ({emp_name}) khỏi data/embeddings.pkl")
                
                if modified:
                    with open(emb_file, "wb") as f:
                        pickle.dump(embeddings_data, f)
            except Exception as pe:
                print(f"[DB] Lỗi cập nhật embeddings.pkl: {pe}")

        # 3. Đồng bộ lại bộ nhớ RAM self.embeddings_cache ngay lập tức
        if hasattr(self, '_reload_embeddings_cache'):
            self._reload_embeddings_cache()

        # 4. Nếu Kiosk đang nhận diện người này, reset ngay về Idle
        if hasattr(self, 'current_page') and self.current_page == "attendance":
            if hasattr(self, 'set_idle_state'):
                self._is_kiosk_ui_idle = False
                self.set_idle_state()

        # 5. Tải lại danh sách để tự động cập nhật số liệu trên thẻ Stats và Bảng
        query = self.search_entry.get().lower() if hasattr(self, 'search_entry') else ""
        self._load_database_to_scrollable(query)
        if hasattr(self, '_reload_dashboard_stats'):
            self._reload_dashboard_stats()
