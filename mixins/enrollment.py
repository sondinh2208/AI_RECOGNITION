"""
==============================================================
ENROLLMENT MIXIN
Chức năng đăng ký nhân viên mới:
- Form nhập liệu thông tin cá nhân (Họ tên, Mã NV, Chức vụ/Phòng ban)
- Cột hiển thị camera trực tiếp và Telemetry Metrics của hệ thống
- Quy trình 1-click capture và trích xuất vector khuôn mặt DeepFace
- Xử lý anti-spam và lưu trữ ảnh + vector đa thư mục
==============================================================
"""

import cv2
import time
import threading
import pickle
import re
import numpy as np
from tkinter import messagebox
from datetime import datetime
from pathlib import Path
from PIL import Image
import customtkinter as ctk

from config import (
    CTK_CARD, CTK_ACCENT, CTK_PRIMARY, CTK_TEXT, CTK_TEXT_DIM,
    CTK_SUCCESS, CTK_WARNING, CTK_DANGER, CTK_SIDEBAR_HOVER,
    ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT, DATA_FACES_DIR, DEEPFACE_MODEL_NAME,
    ENROLLMENT_EMBEDDING_SAMPLES, ENROLLMENT_MIN_SAMPLES,
    FACE_CROP_PADDING_RATIO,
)
from ai_engine import detect_faces, align_face_crop


class EnrollmentMixin:
    """Mixin quản lý nghiệp vụ đăng ký nhân viên và thu thập dữ liệu khuôn mặt."""

    @staticmethod
    def _find_employee_id_in_profiles(profiles, emp_id):
        """Trả về mã đã tồn tại, so sánh không phân biệt hoa/thường."""
        if not isinstance(profiles, dict):
            return None
        normalized_id = str(emp_id).strip().casefold()
        for key, profile in profiles.items():
            stored_id = profile.get("id", key) if isinstance(profile, dict) else key
            if (
                str(key).strip().casefold() == normalized_id
                or str(stored_id).strip().casefold() == normalized_id
            ):
                return str(stored_id).strip() or str(key).strip()
        return None

    def _find_existing_employee_id(self, emp_id):
        """Kiểm tra mã trong dữ liệu trên đĩa và cache hiện tại."""
        embeddings_file = Path("data/embeddings.pkl")
        if embeddings_file.exists():
            with open(embeddings_file, "rb") as file:
                profiles = pickle.load(file)
            duplicate = self._find_employee_id_in_profiles(profiles, emp_id)
            if duplicate:
                return duplicate

        return self._find_employee_id_in_profiles(
            getattr(self, "embeddings_cache", {}), emp_id
        )

    def _build_form_column(self):
        """Xây dựng form đăng ký nhân viên mới."""
        form_card = ctk.CTkFrame(
            self.main_frame, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        form_card.grid(row=0, column=0, sticky="nswe", padx=(0, 14))
        
        # --- Header ---
        header = ctk.CTkFrame(form_card, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 10))
        
        header_row = ctk.CTkFrame(header, fg_color="transparent")
        header_row.pack(fill="x")
        
        ctk.CTkLabel(
            header_row, text="ĐĂNG KÝ KHUÔN MẶT MỚI",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=CTK_PRIMARY, anchor="w",
        ).pack(side="left")
        
        ctk.CTkButton(
            header_row, text="📁 Người đăng ký",
            font=ctk.CTkFont(size=12, weight="bold"), height=30, corner_radius=6,
            fg_color=("#f1f5f9", "#111c2e"), hover_color=CTK_SIDEBAR_HOVER,
            text_color=CTK_TEXT, border_width=1, border_color=CTK_ACCENT,
            command=lambda: self._navigate("database")
        ).pack(side="right")
        
        ctk.CTkLabel(
            header, text="Nhập thông tin nhân viên và chụp ảnh khuôn mặt qua Camera AI",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        ).pack(fill="x", pady=(4, 0))
        
        # --- Fields Frame ---
        fields = ctk.CTkFrame(form_card, fg_color="transparent")
        fields.pack(fill="x", padx=24, pady=(12, 10))
        
        # Helper: Input có icon bên trái
        def create_icon_input(parent, label_text, icon_symbol, placeholder):
            ctk.CTkLabel(
                parent, text=label_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=CTK_TEXT_DIM, anchor="w"
            ).pack(fill="x", pady=(0, 6))
            
            input_box = ctk.CTkFrame(
                parent, height=42, corner_radius=8,
                border_width=1, border_color=CTK_ACCENT,
                fg_color=("#f8fafc", "#080e1a")
            )
            input_box.pack(fill="x", pady=(0, 16))
            input_box.pack_propagate(False)
            
            icon_lbl = ctk.CTkLabel(
                input_box, text=icon_symbol,
                font=ctk.CTkFont(size=14), text_color=("#475569", "#94a3b8"),
                width=34
            )
            icon_lbl.pack(side="left", padx=(8, 0))
            
            entry = ctk.CTkEntry(
                input_box, placeholder_text=placeholder,
                font=ctk.CTkFont(size=13),
                fg_color="transparent", border_width=0,
                text_color=CTK_TEXT,
                placeholder_text_color=("#94a3b8", "#64748b")
            )
            entry.pack(side="left", fill="both", expand=True, padx=(4, 10))
            return entry
            
        # 1. Họ và tên
        self.entry_name = create_icon_input(
            fields, "HỌ VÀ TÊN NHÂN VIÊN", "👤", "Nguyễn Văn A"
        )
        
        # 2. Mã nhân viên
        self.entry_id = create_icon_input(
            fields, "MÃ NHÂN VIÊN", "🪪", "NV001"
        )
        
        # 3. Chức vụ
        self.entry_role = create_icon_input(
            fields, "CHỨC VỤ", "💼", "Kỹ sư phần mềm"
        )

        # 4. Phòng ban
        self.entry_department = create_icon_input(
            fields, "PHÒNG BAN", "🏢", "Phòng IT"
        )
        
        # --- Buttons Frame ---
        btn_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(6, 12))
        
        self.btn_capture = ctk.CTkButton(
            btn_frame, text="📸   QUÉT VÀ LƯU KHUÔN MẶT",
            font=ctk.CTkFont(size=13, weight="bold"), height=46,
            corner_radius=8,
            fg_color=("#2563eb", "#2563eb"),
            hover_color=("#1d4ed8", "#1d4ed8"),
            text_color=("#ffffff", "#ffffff"),
            command=self._start_enrollment_process,
        )
        self.btn_capture.pack(fill="x", pady=(0, 6))
        
        self.progress_bar = ctk.CTkProgressBar(
            btn_frame, mode="indeterminate",
            progress_color=("#2563eb", "#38bdf8"),
            fg_color=("#e2e8f0", "#1e293b"),
            height=6, corner_radius=3
        )
        self.progress_bar.pack_forget()

        # --- Alert / Guideline Box ---
        alert_box = ctk.CTkFrame(
            form_card, height=44, corner_radius=8,
            border_width=1, border_color=CTK_ACCENT,
            fg_color=("#f1f5f9", "#080e1a")
        )
        alert_box.pack(fill="x", padx=24, pady=(0, 10))
        alert_box.pack_propagate(False)
        
        ctk.CTkLabel(
            alert_box,
            text="ⓘ  Hãy đảm bảo khuôn mặt nằm chính giữa khung hình và đủ ánh sáng.",
            font=ctk.CTkFont(size=11), text_color=CTK_TEXT_DIM,
            anchor="w"
        ).pack(side="left", padx=14)

        # --- Status & Preview ---
        self.status_label = ctk.CTkLabel(
            form_card, text="",
            font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w",
        )
        self.status_label.pack(fill="x", padx=24, pady=(0, 6))
        
        self.preview_frame = ctk.CTkFrame(form_card, fg_color="transparent")
        self.preview_frame.pack(fill="x", padx=24, pady=(0, 10))
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="", width=120, height=90)

    def _build_camera_column(self):
        """Xây dựng vùng hiển thị camera trực tiếp & thông tin hệ thống."""
        right_container = ctk.CTkFrame(
            self.main_frame, fg_color="transparent",
            width=ADMIN_CAMERA_WIDTH + 36,
        )
        right_container.grid(row=0, column=1, sticky="nswe")
        right_container.grid_propagate(False)
        
        # ==========================================
        # CARD 1: CAMERA TRỰC TIẾP
        # ==========================================
        cam_card = ctk.CTkFrame(
            right_container, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        cam_card.pack(fill="x", pady=(0, 12))
        
        # Header
        cam_header = ctk.CTkFrame(cam_card, fg_color="transparent")
        cam_header.pack(fill="x", padx=18, pady=(16, 10))
        
        title_row = ctk.CTkFrame(cam_header, fg_color="transparent")
        title_row.pack(fill="x")
        
        ctk.CTkLabel(
            title_row, text="🎥  CAMERA TRỰC TIẾP",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")
        
        self.cam_status_dot = ctk.CTkLabel(
            title_row, text="● LIVE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CTK_SUCCESS, anchor="e",
        )
        self.cam_status_dot.pack(side="right")
        
        # Camera Feed Box
        self.camera_label = ctk.CTkLabel(
            cam_card, text="Đang khởi tạo camera...",
            font=ctk.CTkFont(size=13), text_color=CTK_TEXT_DIM,
            width=ADMIN_CAMERA_WIDTH, height=ADMIN_CAMERA_HEIGHT,
            fg_color=("#e2e8f0", "#080e1a"), corner_radius=8,
        )
        self.camera_label.pack(padx=18, pady=(0, 8))
        
        # Live sub-status line
        self.cam_live_status = ctk.CTkLabel(
            cam_card, text="● ĐANG CHỜ KHUÔN MẶT",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=CTK_SUCCESS, anchor="w"
        )
        self.cam_live_status.pack(fill="x", padx=18, pady=(0, 10))
        
        # Control buttons row
        cam_btn_frame = ctk.CTkFrame(cam_card, fg_color="transparent")
        cam_btn_frame.pack(fill="x", padx=18, pady=(0, 14))
        
        self.btn_toggle_cam = ctk.CTkButton(
            cam_btn_frame, text="⏸  Tạm dừng",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            fg_color=("#f1f5f9", "#080e1a"), hover_color=CTK_SIDEBAR_HOVER,
            border_width=1, border_color=CTK_ACCENT,
            text_color=CTK_TEXT,
            command=self._toggle_camera,
        )
        self.btn_toggle_cam.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        self.btn_restart_cam = ctk.CTkButton(
            cam_btn_frame, text="🔄  Khởi động lại",
            font=ctk.CTkFont(size=12, weight="bold"), height=34, corner_radius=8,
            fg_color=("#f1f5f9", "#080e1a"), hover_color=CTK_SIDEBAR_HOVER,
            border_width=1, border_color=CTK_ACCENT,
            text_color=CTK_TEXT,
            command=self._restart_camera,
        )
        self.btn_restart_cam.pack(side="right", expand=True, fill="x", padx=(5, 0))
        
        # ==========================================
        # CARD 2: THÔNG TIN HỆ THỐNG
        # ==========================================
        sys_card = ctk.CTkFrame(
            right_container, fg_color=CTK_CARD, corner_radius=12,
            border_width=1, border_color=CTK_ACCENT
        )
        sys_card.pack(fill="both", expand=True)
        
        # Header
        sys_header = ctk.CTkFrame(sys_card, fg_color="transparent")
        sys_header.pack(fill="x", padx=18, pady=(14, 8))
        
        ctk.CTkLabel(
            sys_header, text="⏳  THÔNG TIN HỆ THỐNG",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=CTK_TEXT, anchor="w",
        ).pack(side="left")
        
        # Helper: Tạo dòng thông tin hệ thống
        def create_telemetry_row(parent, icon, title, initial_val, is_highlight=False):
            row = ctk.CTkFrame(parent, fg_color="transparent", height=26)
            row.pack(fill="x", padx=18, pady=3)
            row.pack_propagate(False)
            
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left")
            ctk.CTkLabel(
                left, text=f"{icon}  {title}",
                font=ctk.CTkFont(size=12), text_color=CTK_TEXT_DIM, anchor="w"
            ).pack(side="left")
            
            val_lbl = ctk.CTkLabel(
                row, text=initial_val,
                font=ctk.CTkFont(size=12, weight="bold" if is_highlight else "normal"),
                text_color=CTK_SUCCESS if is_highlight else CTK_TEXT,
                anchor="e"
            )
            val_lbl.pack(side="right")
            return val_lbl
            
        sys_rows = ctk.CTkFrame(sys_card, fg_color="transparent")
        sys_rows.pack(fill="x", pady=(0, 14))
        
        self.lbl_stat_model = create_telemetry_row(sys_rows, "🤖", "Model", f"YOLOv8 + {DEEPFACE_MODEL_NAME}")
        self.lbl_stat_conf = create_telemetry_row(sys_rows, "🎯", "Độ tin cậy (Face)", "0.00")
        self.lbl_stat_fps = create_telemetry_row(sys_rows, "⏱", "FPS", "30")
        self.lbl_stat_status = create_telemetry_row(sys_rows, "🩺", "Trạng thái", "● Hoạt động tốt", is_highlight=True)
        
        device_text = "NVIDIA GPU" if getattr(self, 'device', 'cpu') in ['cuda', '0'] else "CPU"
        self.lbl_stat_device = create_telemetry_row(sys_rows, "🖥", "Thiết bị", device_text)

    def _start_enrollment_process(self):
        """
        Quy trình 1-click Quét và Lưu khuôn mặt (Main Thread):
        1. Kiểm tra Họ tên và Mã NV hợp lệ.
        2. Kiểm tra trạng thái AI (self.is_face_valid == True).
        3. Cắt (crop) khuôn mặt tại frame hiện tại và hiển thị preview ngay.
        4. Kích hoạt hiệu ứng loading (thanh tiến trình indeterminate, disable nút).
        5. Đẩy việc trích xuất Vector AI (DeepFace) và lưu trữ sang Background Thread.
        """
        # --- ANTI-SPAM LOGIC ---
        if not hasattr(self, 'spam_count'):
            self.spam_count = 0
            self.last_capture_time = 0
            self.is_capture_locked = False
            
        if self.is_capture_locked:
            return
            
        current_time = time.time()
        # Nếu khoảng cách bấm > 3 giây, reset bộ đếm spam
        if current_time - self.last_capture_time > 3.0:
            self.spam_count = 0
            
        self.spam_count += 1
        self.last_capture_time = current_time
        
        if self.spam_count >= 5:
            self.is_capture_locked = True
            self.btn_capture.configure(state="disabled")
            self._set_status("⏳ CẢNH BÁO: Bấm quá nhanh! Nút quét khóa 5 giây.", CTK_WARNING)
            
            def unlock_capture():
                self.is_capture_locked = False
                self.spam_count = 0
                self.btn_capture.configure(state="normal")
                self._set_status("✅ Đã mở khóa nút quét. Bạn có thể tiếp tục.", CTK_SUCCESS)
                
            self.after(5000, unlock_capture)
            return
        # --- END ANTI-SPAM ---

        # 1. Kiểm tra thông tin Form
        name = self.entry_name.get().strip()
        emp_id = self.entry_id.get().strip()
        role = self.entry_role.get().strip()
        department = self.entry_department.get().strip()
        
        if not name:
            self._set_status("❌ Vui lòng nhập HỌ VÀ TÊN!", CTK_DANGER)
            return
        if not emp_id:
            self._set_status("❌ Vui lòng nhập MÃ NHÂN VIÊN!", CTK_DANGER)
            return
        if re.search(r'[\s<>:"/\\|?*@]', emp_id):
            self._set_status("❌ MÃ NHÂN VIÊN không được có khoảng trắng hoặc ký tự đặc biệt!", CTK_DANGER)
            return
        try:
            duplicate_id = self._find_existing_employee_id(emp_id)
        except Exception as exc:
            self._set_status(
                "❌ Không thể kiểm tra dữ liệu nhân viên. Vui lòng thử lại!",
                CTK_DANGER,
            )
            messagebox.showerror(
                "Không thể kiểm tra mã nhân viên",
                f"Không thể đọc dữ liệu đăng ký:\n{exc}",
                parent=self,
            )
            return
        if duplicate_id:
            warning = f"Mã nhân viên {duplicate_id} đã tồn tại. Vui lòng sử dụng mã khác."
            self._set_status(f"❌ {warning}", CTK_DANGER)
            messagebox.showwarning(
                "Mã nhân viên đã tồn tại",
                warning,
                parent=self,
            )
            self.entry_id.focus_set()
            self.entry_id.select_range(0, "end")
            return
        if not role:
            self._set_status("❌ Vui lòng nhập CHỨC VỤ!", CTK_DANGER)
            return
        if not department:
            self._set_status("❌ Vui lòng nhập PHÒNG BAN!", CTK_DANGER)
            return
        if self.current_frame is None or not self.camera_running:
            self._set_status("❌ Camera chưa sẵn sàng hoặc đang tạm dừng!", CTK_DANGER)
            return
            
        # 2. Kiểm tra khuôn mặt hợp lệ (Khung Xanh lá)
        if not getattr(self, 'is_face_valid', False):
            self._set_status("❌ KHUÔN MẶT CHƯA HỢP LỆ: Vui lòng đưa mặt vào đúng vị trí khung xanh!", CTK_DANGER)
            return

        buffered_samples = [
            sample.copy() for sample in list(
                getattr(self, 'enrollment_face_samples', [])
            )[-ENROLLMENT_EMBEDDING_SAMPLES:]
        ]
        if len(buffered_samples) < ENROLLMENT_MIN_SAMPLES:
            self._set_status(
                f"⏳ Vui lòng giữ khuôn mặt trong khung thêm một chút "
                f"({len(buffered_samples)}/{ENROLLMENT_MIN_SAMPLES} mẫu).",
                CTK_WARNING,
            )
            return
        
        frame = self.current_frame.copy()
        fw, fh = self.frame_width, self.frame_height
        
        # 3. Cắt (crop) lấy khuôn mặt ngay tại frame hiện tại
        faces = detect_faces(self.face_model, frame, self.device)
        face_crop = frame
        if len(faces) > 0:
            fx1, fy1, fx2, fy2, _ = faces[0]
            pad_x = int((fx2 - fx1) * FACE_CROP_PADDING_RATIO)
            pad_y = int((fy2 - fy1) * FACE_CROP_PADDING_RATIO)
            cx1 = max(0, fx1 - pad_x)
            cy1 = max(0, fy1 - pad_y)
            cx2 = min(fw, fx2 + pad_x)
            cy2 = min(fh, fy2 + pad_y)
            if cx2 > cx1 and cy2 > cy1:
                face_crop = frame[cy1:cy2, cx1:cx2]
        
        # Hiển thị preview ngay lập tức
        preview_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        preview_pil = Image.fromarray(preview_rgb).resize((160, 120), Image.LANCZOS)
        preview_ctk = ctk.CTkImage(light_image=preview_pil, dark_image=preview_pil, size=(160, 120))
        self.preview_label.configure(image=preview_ctk, text="")
        self.preview_label.image = preview_ctk
        self.preview_label.pack(pady=(5, 0))
        
        # 4. Kích hoạt Loading UI
        self.btn_capture.configure(state="disabled", text="⏳ Đang trích xuất Vector AI...")
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.start()
        self._set_status("⏳ Đang trích xuất Vector AI và lưu dữ liệu...", CTK_WARNING)
        
        # 5. Khởi chạy AI Thread chạy ngầm (Non-blocking UI)
        threading.Thread(
            target=self._ai_worker_save_face,
            args=(buffered_samples, frame, name, emp_id, role, department),
            daemon=True
        ).start()

    def _ai_worker_save_face(self, face_samples, full_frame, name, emp_id, role, department):
        """
        Background Thread: Trích xuất Vector khuôn mặt qua DeepFace và lưu file.
        Không thao tác trực tiếp với UI ở đây.
        """
        try:
            from deepface import DeepFace
            
            # --- 1. Tự động căn chỉnh xoay thẳng mặt (Face Alignment) ---
            detector = getattr(self, 'face_detector', None)
            embedding_vectors = []
            aligned_samples = []
            for sample in face_samples:
                aligned_sample, tilt_angle = align_face_crop(sample, detector)
                reps = DeepFace.represent(
                    img_path=aligned_sample,
                    model_name=DEEPFACE_MODEL_NAME,
                    detector_backend="skip",
                    enforce_detection=False,
                )
                if reps and reps[0].get("embedding") is not None:
                    embedding_vectors.append(reps[0]["embedding"])
                    aligned_samples.append(aligned_sample)

            if len(embedding_vectors) < ENROLLMENT_MIN_SAMPLES:
                raise RuntimeError(
                    f"Chỉ tạo được {len(embedding_vectors)}/{ENROLLMENT_MIN_SAMPLES} "
                    "vector khuôn mặt hợp lệ"
                )

            # Giữ trường embedding dạng đơn cho mã cũ: dùng centroid đã chuẩn hóa.
            normalized_vectors = []
            for vector in embedding_vectors:
                value = np.asarray(vector, dtype=np.float32)
                norm = float(np.linalg.norm(value))
                if norm > 1e-12:
                    normalized_vectors.append(value / norm)
            centroid = np.mean(np.stack(normalized_vectors), axis=0)
            centroid /= np.linalg.norm(centroid)
            embedding_vector = centroid.tolist()

            # Ảnh hiển thị duy nhất là mẫu gần centroid nhất.
            representative_index = int(np.argmax(
                np.stack(normalized_vectors) @ centroid
            ))
            face_crop = aligned_samples[representative_index]
            
            # Đọc lại nguồn dữ liệu chuẩn ngay trước lúc ghi để không ghi đè
            # nếu mã vừa được đăng ký bởi một thao tác khác.
            embeddings_file = Path("data/embeddings.pkl")
            embeddings_data = {}
            if embeddings_file.exists():
                try:
                    with open(embeddings_file, "rb") as f:
                        embeddings_data = pickle.load(f)
                except Exception as exc:
                    raise RuntimeError(
                        f"Không thể đọc dữ liệu nhân viên hiện tại: {exc}"
                    ) from exc

            duplicate_id = self._find_employee_id_in_profiles(
                embeddings_data, emp_id
            )
            if duplicate_id:
                raise ValueError(
                    f"Mã nhân viên {duplicate_id} đã tồn tại; dữ liệu không được ghi đè"
                )

            # --- 2. Lưu ảnh ra các thư mục ---
            def safe_component(value, fallback):
                value = re.sub(r'[<>:"/\\|?*@]', "", value.strip())
                value = re.sub(r"\s+", "_", value)
                return value.strip("_") or fallback

            safe_name = safe_component(name, "Nhan_vien")
            safe_role = safe_component(role, "Nhan_vien")
            safe_department = safe_component(department, "Phong_ban")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{emp_id}@{safe_role}@{safe_department}@{safe_name}_{timestamp}.jpg"
            
            Path(DATA_FACES_DIR).mkdir(parents=True, exist_ok=True)
            Path("images").mkdir(parents=True, exist_ok=True)
            Path("database/images").mkdir(parents=True, exist_ok=True)
            Path("data").mkdir(parents=True, exist_ok=True)
            
            filepath_main = Path(DATA_FACES_DIR) / filename
            filepath_crop = Path("images") / filename
            filepath_db = Path("database/images") / filename
            
            # Lưu ảnh an toàn hỗ trợ ký tự tiếng Việt (Unicode) trên Windows
            try:
                cv2.imencode('.jpg', full_frame)[1].tofile(str(filepath_main))
                cv2.imencode('.jpg', face_crop)[1].tofile(str(filepath_crop))
                cv2.imencode('.jpg', face_crop)[1].tofile(str(filepath_db))
            except Exception:
                cv2.imwrite(str(filepath_main), full_frame)
                cv2.imwrite(str(filepath_crop), face_crop)
            
            # --- 3. Lưu Vector vào embeddings.pkl ---
            embeddings_data[emp_id] = {
                "id": emp_id,
                "name": name,
                "role": role,
                "department": department,
                "embedding": embedding_vector,
                "embeddings": embedding_vectors,
                "embedding_count": len(embedding_vectors),
                "image_path": str(filepath_main),
                "timestamp": timestamp,
                "recognition_enabled": True,
            }
            
            temporary_file = embeddings_file.with_suffix(".pkl.tmp")
            with open(temporary_file, "wb") as f:
                pickle.dump(embeddings_data, f)
            temporary_file.replace(embeddings_file)
                
            # Cập nhật ngay cache embeddings trong RAM
            self.embeddings_cache = embeddings_data
                
            print(f"[AI WORKER] Trích xuất Vector & lưu thành công: {emp_id} - {name} ({filepath_main})")
            
            # --- 4. Báo kết quả về Main Thread ---
            self.after(0, self._finish_enrollment, True, name, emp_id, "")
            
        except Exception as e:
            print(f"[AI WORKER ERROR] Lỗi xử lý AI: {e}")
            self.after(0, self._finish_enrollment, False, name, emp_id, str(e))

    def _finish_enrollment(self, success, name="", emp_id="", error_msg=""):
        """
        Main Thread: Nhận tín hiệu kết thúc từ AI Worker và hoàn tất UI.
        """
        # 1. Dừng và ẩn thanh loading
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        
        # 2. Khôi phục nút bấm
        self.btn_capture.configure(state="normal", text="📸   QUÉT VÀ LƯU KHUÔN MẶT")
        
        # 3. Cập nhật kết quả
        if success:
            self._set_status(f"✅ Quét và lưu khuôn mặt thành công: {name} ({emp_id})!", CTK_SUCCESS)
            self.enrollment_face_samples.clear()
            self._last_enrollment_sample_at = 0.0
            
            # Tự động xóa các ô nhập liệu
            self.entry_name.delete(0, "end")
            self.entry_id.delete(0, "end")
            self.entry_role.delete(0, "end")
            self.entry_department.delete(0, "end")
            
            # Cập nhật danh sách database nếu có
            if hasattr(self, '_load_database_to_scrollable') and hasattr(self, 'db_scroll'):
                query = self.search_entry.get().lower() if hasattr(self, 'search_entry') else ""
                self._load_database_to_scrollable(query)
            else:
                self.db_data_loaded = False
        else:
            self._set_status(f"❌ Lưu dữ liệu thất bại: {error_msg}", CTK_DANGER)

    def _capture_face(self):
        """Hỗ trợ tương thích ngược cho hàm chụp ảnh."""
        self._start_enrollment_process()

    def _save_employee(self):
        """Hỗ trợ tương thích ngược cho hàm lưu nhân viên."""
        self._start_enrollment_process()

    def _set_status(self, text, color=CTK_TEXT_DIM):
        """Cập nhật dòng trạng thái trên form."""
        self.status_label.configure(text=text, text_color=color)
