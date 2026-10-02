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
import tkinter as tk
from tkinter import messagebox
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
        stats_frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="card")
        
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
        self.lbl_new = create_stat_card(stats_frame, 2, "👤+", "Mới trong tháng", "0", "Đăng ký gần đây", "#6366f1")
            
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
        
        headers = ["", "Ảnh", "Mã NV", "Họ và tên", "Chức vụ", "Phòng ban", "Trạng thái", "Hành động"]
        weights = [3, 5, 8, 18, 13, 13, 13, 14]
        
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
        """Debounce tìm kiếm để không tạo một worker đọc ảnh cho mỗi phím gõ."""
        if getattr(self, '_db_search_timer', None):
            try:
                self.after_cancel(self._db_search_timer)
            except Exception:
                pass
        query = self.search_entry.get().lower()
        self._db_search_timer = self.after(
            250, lambda value=query: self._load_database_to_scrollable(value)
        )

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

        # embeddings.pkl là nguồn metadata chuẩn; tên file chỉ là fallback
        # để tiếp tục đọc được hồ sơ của phiên bản cũ.
        profiles = {}
        embeddings_file = Path("data/embeddings.pkl")
        if embeddings_file.exists():
            try:
                with open(embeddings_file, "rb") as file:
                    profiles = pickle.load(file)
            except Exception:
                profiles = {}
        
        records = []
        for img_path in image_files:
            filename = img_path.stem
            emp_id = "Unknown"
            emp_role = "Nhân viên"
            emp_department = "Phòng IT"
            emp_name = "Unknown"
            
            if "@" in filename:
                parts = filename.split("@", 3)
                emp_id = parts[0]
                if len(parts) >= 4:
                    emp_role = parts[1].replace("_", " ")
                    emp_department = parts[2].replace("_", " ")
                    emp_name = parts[3].replace("_", " ")
                elif len(parts) >= 3:
                    emp_role = parts[1].replace("_", " ")
                    emp_name = parts[2].replace("_", " ")
                elif len(parts) == 2:
                    emp_name = parts[1].replace("_", " ")
            else:
                parts = filename.split("_", 1)
                emp_id = parts[0] if len(parts) > 0 else "Unknown"
                emp_name = parts[1].replace("_", " ") if len(parts) > 1 else "Unknown"
                
            emp_name = re.sub(r'\s*\d{8}\s\d{6}.*$', '', emp_name).strip()

            profile = next(
                (
                    value for key, value in profiles.items()
                    if str(key).strip().casefold() == emp_id.strip().casefold()
                ),
                None,
            )
            if isinstance(profile, dict):
                emp_name = profile.get("name") or emp_name
                emp_role = profile.get("role") or emp_role
                emp_department = (
                    profile.get("department") or profile.get("dept") or emp_department
                )

            recognition_enabled = (
                profile.get("recognition_enabled", profile.get("source") != "LFW benchmark")
                if isinstance(profile, dict) else True
            )

            # Tương thích hồ sơ cũ từng gộp "Chức vụ - Phòng ban" trong role.
            if not (isinstance(profile, dict) and (profile.get("department") or profile.get("dept"))):
                legacy_parts = re.split(r"\s*[-–]\s*", emp_role, maxsplit=1)
                if len(legacy_parts) == 2:
                    emp_role, emp_department = legacy_parts[0], legacy_parts[1]
            
            if (query and query not in emp_id.lower() and query not in emp_name.lower()
                    and query not in emp_role.lower() and query not in emp_department.lower()):
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
                "department": emp_department,
                "recognition_enabled": bool(recognition_enabled),
                "status": "Đang nhận diện" if recognition_enabled else "Chỉ kiểm thử",
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
        
        weights = [3, 5, 8, 18, 13, 13, 13, 14]
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

        # 5. Phòng ban
        ctk.CTkLabel(
            row_frame, text=emp_data.get("department", "Phòng IT"),
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
        ).grid(row=0, column=5, sticky="w", padx=10)
        
        # 6. Trạng thái
        badge_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        badge_frame.grid(row=0, column=6, sticky="w", padx=10)
        
        status_text = emp_data["status"]
        if emp_data.get("recognition_enabled", True):
            text_col, icon = ("#16a34a", "#4ade80"), "● "
        else:
            text_col, icon = ("#b45309", "#f59e0b"), "● "
            
        badge = ctk.CTkLabel(
            badge_frame, text=icon + status_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=text_col, fg_color="transparent",
        )
        badge.pack(side="left")
        
        # 7. Hành động
        action_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        action_frame.grid(row=0, column=7, sticky="w", padx=10)
        
        btn_view = ctk.CTkButton(
            action_frame, text="👁", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1,
            border_color=CTK_ACCENT, text_color=("#3b82f6", "#60a5fa"),
            hover_color=CTK_SIDEBAR_HOVER,
            command=lambda data=emp_data: self._view_employee_image(data),
        )
        btn_view.pack(side="left", padx=2)
        
        btn_edit = ctk.CTkButton(
            action_frame, text="📝", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1,
            border_color=CTK_ACCENT, text_color=("#6b7280", "#cbd5e1"),
            hover_color=CTK_SIDEBAR_HOVER,
            command=lambda data=emp_data: self._edit_employee(data),
        )
        btn_edit.pack(side="left", padx=2)

        recognition_enabled = emp_data.get("recognition_enabled", True)
        btn_toggle = ctk.CTkButton(
            action_frame,
            text="Tắt" if recognition_enabled else "Bật",
            width=38, height=28,
            fg_color="transparent", corner_radius=4, border_width=1,
            border_color=CTK_ACCENT,
            text_color=("#b45309", "#fbbf24") if recognition_enabled else ("#15803d", "#4ade80"),
            hover_color=CTK_SIDEBAR_HOVER,
            command=lambda data=emp_data: self._toggle_employee_recognition(data),
        )
        btn_toggle.pack(side="left", padx=2)
        
        btn_del = ctk.CTkButton(
            action_frame, text="🗑", width=30, height=28,
            fg_color="transparent", corner_radius=4, border_width=1, border_color=CTK_ACCENT, text_color=("#ef4444", "#fca5a5"), hover_color=("#fee2e2", "#7f1d1d"),
            command=lambda: self._delete_single_employee(emp_data, row_frame)
        )
        btn_del.pack(side="left", padx=2)

    def _toggle_employee_recognition(self, emp_info):
        """Bật/tắt một hồ sơ trong gallery điểm danh mà không xóa dữ liệu."""
        emp_id = str(emp_info.get("id", "")).strip()
        if not emp_id:
            return

        embeddings_file = Path("data/embeddings.pkl")
        try:
            with open(embeddings_file, "rb") as file:
                embeddings_data = pickle.load(file)

            profile_key = next(
                (
                    key for key in embeddings_data
                    if str(key).strip().casefold() == emp_id.casefold()
                ),
                None,
            )
            if profile_key is None:
                raise KeyError(f"Không tìm thấy hồ sơ {emp_id}")

            profile = embeddings_data[profile_key]
            enabled = not bool(
                profile.get(
                    "recognition_enabled",
                    profile.get("source") != "LFW benchmark",
                )
            )
            embeddings_data[profile_key]["recognition_enabled"] = enabled

            temporary = embeddings_file.with_suffix(".pkl.tmp")
            with open(temporary, "wb") as file:
                pickle.dump(embeddings_data, file)
            temporary.replace(embeddings_file)

            self.embeddings_cache = embeddings_data
            emp_info["recognition_enabled"] = enabled
            emp_info["status"] = "Đang nhận diện" if enabled else "Chỉ kiểm thử"
            self._render_database_page()
        except Exception as exc:
            messagebox.showerror(
                "Không thể cập nhật",
                f"Không thể đổi trạng thái nhận diện của {emp_id}:\n{exc}",
                parent=self,
            )

    def _open_database_dialog(self, title, width, height):
        """Tạo cửa sổ con modal và đặt giữa cửa sổ chính."""
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=self._apply_appearance_mode(CTK_CARD))
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        return dialog

    def _view_employee_image(self, emp_info):
        """Mở ảnh đăng ký kích thước lớn cùng thông tin nhân viên."""
        image_path = Path(emp_info.get("img_path", ""))
        if not image_path.exists():
            messagebox.showerror(
                "Không tìm thấy ảnh",
                "Ảnh đăng ký của nhân viên không còn tồn tại.",
                parent=self,
            )
            return

        try:
            with Image.open(image_path) as source:
                preview = source.convert("RGB")
            preview.thumbnail((650, 455), Image.Resampling.LANCZOS)
        except Exception as exc:
            messagebox.showerror(
                "Không thể mở ảnh", f"Không thể đọc ảnh đăng ký:\n{exc}", parent=self
            )
            return

        dialog = self._open_database_dialog("Ảnh khuôn mặt đã đăng ký", 720, 600)
        header = ctk.CTkFrame(dialog, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 12))
        ctk.CTkLabel(
            header, text=emp_info.get("name", "Nhân viên"),
            font=ctk.CTkFont(size=20, weight="bold"), text_color=CTK_TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=(
                f"{emp_info.get('id', '—')}  ·  {emp_info.get('role', 'Nhân viên')}"
                f"  ·  {emp_info.get('department', 'Phòng IT')}"
            ),
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM,
        ).pack(anchor="w", pady=(3, 0))

        image_card = ctk.CTkFrame(
            dialog, fg_color=("#F8FAFC", "#0F172A"), corner_radius=10,
            border_width=1, border_color=CTK_ACCENT,
        )
        image_card.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        image = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
        image_label = ctk.CTkLabel(image_card, text="", image=image)
        image_label.image = image
        image_label.place(relx=0.5, rely=0.5, anchor="center")
        dialog._preview_image = image

        ctk.CTkButton(
            dialog, text="Đóng", width=100, height=34, corner_radius=7,
            fg_color=("#E2E8F0", "#1E293B"),
            hover_color=("#CBD5E1", "#334155"), text_color=CTK_TEXT,
            command=dialog.destroy,
        ).pack(pady=(0, 18))

    def _edit_employee(self, emp_info):
        """Hiển thị biểu mẫu sửa mã, tên và chức vụ của nhân viên."""
        dialog = self._open_database_dialog("Sửa thông tin nhân viên", 500, 580)
        dialog.resizable(True, True)
        dialog.minsize(430, 420)

        ctk.CTkLabel(
            dialog, text="Sửa thông tin nhân viên",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=CTK_TEXT,
        ).pack(anchor="w", padx=28, pady=(24, 4))
        ctk.CTkLabel(
            dialog, text="Thông tin mới sẽ được đồng bộ với dữ liệu nhận diện.",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
        ).pack(anchor="w", padx=28, pady=(0, 18))

        # Giữ các nút hành động luôn nhìn thấy; phần biểu mẫu ở giữa có thể cuộn
        # trên màn hình thấp hoặc khi Windows dùng display scaling lớn.
        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(side="bottom", fill="x", padx=28, pady=(10, 20))

        form_scroll = ctk.CTkScrollableFrame(
            dialog,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=("#CBD5E1", "#475569"),
            scrollbar_button_hover_color=("#94A3B8", "#64748B"),
        )
        form_scroll.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 2))

        entries = {}
        fields = (
            ("id", "Mã nhân viên", emp_info.get("id", "")),
            ("name", "Họ và tên", emp_info.get("name", "")),
            ("role", "Chức vụ", emp_info.get("role", "")),
            ("department", "Phòng ban", emp_info.get("department", "Phòng IT")),
        )
        for key, label, value in fields:
            ctk.CTkLabel(
                form_scroll, text=label,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=CTK_TEXT,
            ).pack(anchor="w", padx=8)
            entry = ctk.CTkEntry(
                form_scroll, height=38, corner_radius=7,
                border_color=CTK_ACCENT, fg_color=("#FFFFFF", "#0F172A"),
                text_color=CTK_TEXT,
            )
            entry.pack(fill="x", padx=8, pady=(5, 13))
            entry.insert(0, value)
            entries[key] = entry

        ctk.CTkButton(
            actions, text="Hủy", width=100, height=36,
            fg_color=("#E2E8F0", "#1E293B"),
            hover_color=("#CBD5E1", "#334155"), text_color=CTK_TEXT,
            command=dialog.destroy,
        ).pack(side="right")
        ctk.CTkButton(
            actions, text="Lưu thay đổi", width=130, height=36,
            fg_color=CTK_PRIMARY, hover_color=("#1D4ED8", "#1D4ED8"),
            command=lambda: self._save_employee_edits(
                emp_info,
                entries["id"].get(),
                entries["name"].get(),
                entries["role"].get(),
                entries["department"].get(),
                dialog,
            ),
        ).pack(side="right", padx=(0, 10))
        entries["name"].focus_set()

    def _save_employee_edits(
        self, emp_info, new_id, new_name, new_role, new_department, dialog
    ):
        """Đổi metadata, tên ảnh và embedding theo một giao dịch có hoàn tác."""
        def clean_component(value, remove_spaces=False):
            value = re.sub(r'[<>:"/\\|?*@]', "", value.strip())
            value = re.sub(r"\s+", " ", value)
            return value.replace(" ", "") if remove_spaces else value

        new_id = clean_component(new_id, remove_spaces=True)
        new_name = clean_component(new_name)
        new_role = clean_component(new_role)
        new_department = clean_component(new_department)
        if not new_id or not new_name or not new_role or not new_department:
            messagebox.showerror(
                "Thiếu thông tin",
                "Vui lòng nhập đầy đủ mã, họ tên, chức vụ và phòng ban.",
                parent=dialog,
            )
            return

        old_id = str(emp_info.get("id", "")).strip()
        old_path = Path(emp_info.get("img_path", ""))
        if not old_path.exists():
            messagebox.showerror(
                "Không tìm thấy dữ liệu", "Ảnh gốc của nhân viên không còn tồn tại.",
                parent=dialog,
            )
            return

        embeddings_file = Path("data/embeddings.pkl")
        embeddings_data = {}
        if embeddings_file.exists():
            try:
                with open(embeddings_file, "rb") as file:
                    embeddings_data = pickle.load(file)
            except Exception as exc:
                messagebox.showerror(
                    "Không thể đọc dữ liệu", f"Không thể đọc embeddings.pkl:\n{exc}",
                    parent=dialog,
                )
                return

        old_key = next(
            (key for key in embeddings_data if str(key).strip().casefold() == old_id.casefold()),
            None,
        )
        duplicate_key = next(
            (
                key for key in embeddings_data
                if str(key).strip().casefold() == new_id.casefold() and key != old_key
            ),
            None,
        )
        if duplicate_key is not None:
            messagebox.showerror(
                "Mã nhân viên đã tồn tại",
                f"Mã {new_id} đang thuộc một nhân viên khác.", parent=dialog,
            )
            return


        timestamp_match = re.search(r"_(\d{8}_\d{6})$", old_path.stem)
        timestamp = timestamp_match.group(1) if timestamp_match else "profile"
        safe_name = new_name.replace(" ", "_")
        safe_role = new_role.replace(" ", "_")
        safe_department = new_department.replace(" ", "_")
        new_filename = (
            f"{new_id}@{safe_role}@{safe_department}@{safe_name}_{timestamp}"
            f"{old_path.suffix.lower()}"
        )

        source_name = old_path.name
        candidates = [
            old_path,
            Path(DATA_FACES_DIR) / source_name,
            Path("images") / source_name,
            Path("database/images") / source_name,
        ]
        sources = []
        seen = set()
        for source in candidates:
            source_key = str(source.resolve()).casefold()
            if source.exists() and source_key not in seen:
                sources.append(source)
                seen.add(source_key)

        rename_pairs = [(source, source.with_name(new_filename)) for source in sources]
        for source, target in rename_pairs:
            if target.exists() and source.resolve() != target.resolve():
                messagebox.showerror(
                    "Dữ liệu đã tồn tại",
                    f"Không thể lưu vì đã có tệp {target.name}.", parent=dialog,
                )
                return

        renamed = []
        try:
            for source, target in rename_pairs:
                if source.name != target.name:
                    source.rename(target)
                    renamed.append((source, target))

            if old_key is not None:
                profile = embeddings_data.pop(old_key)
                profile.update({
                    "id": new_id,
                    "name": new_name,
                    "role": new_role,
                    "department": new_department,
                    "image_path": str(Path(DATA_FACES_DIR) / new_filename),
                })
                embeddings_data[new_id] = profile

            if embeddings_file.exists():
                temp_file = embeddings_file.with_suffix(".pkl.tmp")
                with open(temp_file, "wb") as file:
                    pickle.dump(embeddings_data, file)
                temp_file.replace(embeddings_file)
        except Exception as exc:
            for source, target in reversed(renamed):
                try:
                    if target.exists() and not source.exists():
                        target.rename(source)
                except Exception:
                    pass
            messagebox.showerror(
                "Không thể lưu thay đổi", f"Dữ liệu chưa được cập nhật:\n{exc}",
                parent=dialog,
            )
            return

        self.embeddings_cache = embeddings_data
        dialog.destroy()
        query = self.search_entry.get().lower() if hasattr(self, "search_entry") else ""
        self._load_database_to_scrollable(query)
        if hasattr(self, "_reload_dashboard_stats"):
            self._reload_dashboard_stats()
        messagebox.showinfo(
            "Đã cập nhật", f"Đã lưu thông tin của {new_name} ({new_id}).", parent=self
        )

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
