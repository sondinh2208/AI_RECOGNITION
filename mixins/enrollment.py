"""Pipeline đăng ký khuôn mặt dùng chung cho web runtime."""

import cv2
import pickle
import re
import numpy as np
from datetime import datetime
from pathlib import Path

from config import (
    DATA_FACES_DIR, DEEPFACE_MODEL_NAME,
    ENROLLMENT_MIN_SAMPLES,
)
from ai_engine import align_face_crop


class EnrollmentMixin:
    """Kiểm tra trùng lặp và lưu hồ sơ khuôn mặt."""

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

    def _reset_enrollment_scan(self, clear_preview=True, wait_for_face_leave=False):
        """Bỏ snapshot hiện tại và đưa camera về trạng thái tự quét lượt mới."""
        self.enrollment_capture_ready = False
        self.enrollment_captured_samples = []
        self.enrollment_captured_frame = None
        self.enrollment_captured_face = None
        self._enrollment_valid_since = None
        self._enrollment_waiting_for_face_leave = wait_for_face_leave
        self.enrollment_face_samples.clear()
        self._last_enrollment_sample_at = 0.0
        self.is_face_valid = False
        self.ai_status_text = "Dua mat vao khung hinh"

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
            for sample in face_samples:
                aligned_sample, _tilt_angle = align_face_crop(sample, detector)
                reps = DeepFace.represent(
                    img_path=aligned_sample,
                    model_name=DEEPFACE_MODEL_NAME,
                    detector_backend="skip",
                    enforce_detection=False,
                )
                if reps and reps[0].get("embedding") is not None:
                    embedding_vectors.append(reps[0]["embedding"])

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

            # --- 2. Chỉ lưu một ảnh chuẩn trong data/faces ---
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
            Path("data").mkdir(parents=True, exist_ok=True)
            filepath_main = Path(DATA_FACES_DIR) / filename

            # Lưu ảnh an toàn hỗ trợ ký tự tiếng Việt (Unicode) trên Windows
            try:
                cv2.imencode('.jpg', full_frame)[1].tofile(str(filepath_main))
            except Exception:
                cv2.imwrite(str(filepath_main), full_frame)
            
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
