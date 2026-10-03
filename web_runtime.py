"""Runtime headless cho giao diện web, tái sử dụng nguyên pipeline AI hiện có."""

from __future__ import annotations

import pickle
import re
import threading
import time
import tempfile
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from config import (
    DATA_FACES_DIR,
    ENROLLMENT_EMBEDDING_SAMPLES,
    ENROLLMENT_MIN_SAMPLES,
    KIOSK_ATTENDANCE_COOLDOWN_SECONDS,
)
from mixins.attendance import AttendanceMixin
from mixins.camera import CameraMixin
from mixins.core import CoreMixin
from mixins.enrollment import EnrollmentMixin


class WebFaceCheckRuntime(AttendanceMixin, CameraMixin, EnrollmentMixin, CoreMixin):
    """Cầu nối trạng thái; quyết định AI vẫn chạy trong các mixin gốc."""

    def __init__(self, load_ai: bool = True):
        self._lock = threading.RLock()
        self._event_version = 0
        self._started = False
        self._load_ai_requested = load_ai

        self.camera_cap = None
        self.camera_running = False
        self.current_frame = None
        self.latest_processed_frame = None
        self.camera_thread = None
        self.current_fps = 0.0
        self.current_conf = 0.0
        self.current_page = "attendance"
        self._current_frame_id = 0
        self._last_cached_frame_id = -1
        self._last_cached_jpeg = None

        self.kiosk_running = False
        self.is_recognizing = False
        self.kiosk_latest_frame = None
        self.kiosk_latest_pil = None
        self.kiosk_fps = 0.0
        self.kiosk_face_count = 0
        self.kiosk_thread = None
        self._kiosk_thread_token = 0
        self._kiosk_waiting_for_departure = False
        self._kiosk_idle_reset_pending = False
        self._kiosk_face_stable_since = None
        self._kiosk_face_absent_since = None
        self._kiosk_active_face_area = None
        self._kiosk_unknown_attempts = 0
        self._kiosk_strict_candidate_id = None
        self._kiosk_strict_confirmations = 0
        self._kiosk_strict_attempts = 0
        self._kiosk_strict_started_at = None
        self._kiosk_strict_distances = []
        self._kiosk_strict_margins = []
        self._attendance_cooldowns = {}
        self._is_kiosk_ui_idle = True

        self.enrollment_running = False
        self.is_face_valid = False
        self.ai_status_text = "Dua mat vao khung hinh"
        self.prev_status = None
        self.enrollment_face_samples = deque(maxlen=ENROLLMENT_EMBEDDING_SAMPLES)
        self._last_enrollment_sample_at = 0.0
        self._enrollment_valid_since = None
        self.enrollment_capture_ready = False
        self.enrollment_captured_samples = []
        self.enrollment_captured_frame = None
        self.enrollment_captured_face = None
        self._enrollment_preview_shown = False
        self._enrollment_waiting_for_face_leave = False
        self._enrollment_saving = False

        self.face_model = None
        self.face_detector = None
        self.device = "cpu"
        self.shape_rect = None
        self.constraint_box = None
        self.ekyc_mask = None
        self.frame_width = 640
        self.frame_height = 480
        self.embeddings_cache = None
        self.attendance_history = []
        self._attendance_history_version = 0

        self.recognition_state = self._idle_payload()
        self.enrollment_result = {
            "state": "idle",
            "title": "Chờ khuôn mặt hợp lệ",
            "message": "Giữ yên khuôn mặt trong 3 giây để hệ thống tự động quét.",
        }
        self.recognition_test_result = {
            "version": 0, "state": "idle", "result": None, "error": None
        }

        Path(DATA_FACES_DIR).mkdir(parents=True, exist_ok=True)
        Path("images").mkdir(parents=True, exist_ok=True)
        Path("database/images").mkdir(parents=True, exist_ok=True)
        Path("data").mkdir(parents=True, exist_ok=True)
        self._load_attendance_history()

    @staticmethod
    def _idle_payload():
        return {
            "state": "idle",
            "title": "Chờ nhận diện",
            "message": "Hệ thống tự động ghi nhận khi có nhân viên",
            "employee": None,
            "timestamp": None,
            "metrics": None,
        }

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        if self._load_ai_requested:
            self._load_ai_models()
            self._start_camera()
        else:
            self.embeddings_cache = self._read_profiles()
        self._publish()

    def shutdown(self):
        self.kiosk_running = False
        self.enrollment_running = False
        self.camera_running = False
        for worker in (self.kiosk_thread, self.camera_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=0.8)
        if self.camera_cap is not None:
            self.camera_cap.release()
            self.camera_cap = None
        if self.face_detector is not None:
            try:
                self.face_detector.close()
            except Exception:
                pass

    def after(self, _delay_ms, callback, *args):
        """Tương thích callback Tk; web runtime không có main-loop widget."""
        callback(*args)

    def _safe_after(self, _delay_ms, callback, *args):
        callback(*args)

    def _publish(self):
        with self._lock:
            self._event_version += 1

    @property
    def event_version(self):
        with self._lock:
            return self._event_version

    def snapshot(self):
        with self._lock:
            return {
                "version": self._event_version,
                "mode": self.current_page,
                "camera": {
                    "running": bool(self.camera_running),
                    "fps": round(float(
                        self.kiosk_fps if self.current_page == "attendance"
                        else self.current_fps
                    ), 1),
                    "confidence": round(float(self.current_conf), 3),
                    "faces": int(self.kiosk_face_count),
                    "device": self.device,
                },
                "recognition": dict(self.recognition_state),
                "recognition_test": dict(self.recognition_test_result),
                "enrollment": {
                    **self.enrollment_result,
                    "capture_ready": bool(self.enrollment_capture_ready),
                    "saving": bool(self._enrollment_saving),
                    "status_text": self.ai_status_text,
                },
                "history_version": self._attendance_history_version,
            }

    def switch_mode(self, mode: str):
        if mode not in {"attendance", "add_employee", "idle"}:
            return self.snapshot()
        with self._lock:
            if self.current_page == mode and (
                (mode == "attendance" and self.kiosk_running)
                or (mode == "add_employee" and self.enrollment_running)
                or (mode == "idle" and not self.kiosk_running and not self.enrollment_running)
            ):
                return self.snapshot()
            self.kiosk_running = False
            self.enrollment_running = False
        for worker in (self.kiosk_thread, self.camera_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=0.5)
        self.current_page = mode
        if self.camera_running and mode != "idle":
            if mode == "attendance":
                self._start_kiosk_worker()
            else:
                self.enrollment_running = True
                self.camera_thread = threading.Thread(
                    target=self._camera_loop, daemon=True
                )
                self.camera_thread.start()
        self._publish()
        return self.snapshot()

    def camera_jpeg_frame(self):
        """Trả về (frame_id, jpeg_bytes) có bộ nhớ đệm, chỉ nén ảnh khi có khung hình mới."""
        frame = None
        if self.current_page == "attendance":
            if self.kiosk_latest_frame is not None:
                frame = self.kiosk_latest_frame
            elif self.kiosk_latest_pil is not None:
                frame = cv2.cvtColor(np.asarray(self.kiosk_latest_pil), cv2.COLOR_RGB2BGR)
        elif self.latest_processed_frame is not None:
            frame = self.latest_processed_frame
        if frame is None:
            return 0, None

        frame_id = getattr(self, "_current_frame_id", 0)
        cached_id = getattr(self, "_last_cached_frame_id", -1)
        cached_bytes = getattr(self, "_last_cached_jpeg", None)

        if frame_id == cached_id and cached_bytes is not None:
            return frame_id, cached_bytes

        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            cached_bytes = encoded.tobytes()
            self._last_cached_frame_id = frame_id
            self._last_cached_jpeg = cached_bytes
            return frame_id, cached_bytes
        return frame_id, None

    def camera_jpeg(self):
        _, bytes_data = self.camera_jpeg_frame()
        return bytes_data

    def _update_kiosk_frame(self):
        """The browser consumes frames from ``camera_jpeg``; no Tk repaint loop."""

    def _update_frame(self):
        """The browser consumes frames from ``camera_jpeg``; no Tk repaint loop."""

    # ---------- Callback nhận diện: chỉ chuyển state sang JSON ----------
    def _on_kiosk_recognizing_started(self):
        self.recognition_state = {
            **self._idle_payload(), "state": "recognizing",
            "title": "Đang nhận diện khuôn mặt",
            "message": "Đang đối chiếu dữ liệu nhân sự...",
        }
        self._publish()

    def _show_multiple_faces_notice(self):
        if self.is_recognizing or self._kiosk_waiting_for_departure:
            return
        self.recognition_state = {
            **self._idle_payload(), "state": "validating",
            "title": "Chưa xác định người gần nhất",
            "message": "Người cần điểm danh vui lòng tiến gần camera hơn",
        }
        self._publish()

    def _on_kiosk_face_quality_rejected(self, message):
        if self.is_recognizing or self._kiosk_waiting_for_departure:
            return
        self._kiosk_unknown_attempts = 0
        self.recognition_state = {
            **self._idle_payload(), "state": "validating",
            "title": "Căn chỉnh khuôn mặt", "message": message,
        }
        self._publish()

    def _on_kiosk_unknown_confirmation_pending(self, attempt):
        self.recognition_state = {
            **self._idle_payload(), "state": "recognizing",
            "title": "Đang xác minh lại",
            "message": f"Lần kiểm tra {attempt}/2 · Vui lòng giữ nguyên khuôn mặt",
        }
        self._publish()

    def _on_kiosk_strict_confirmation_pending(self, votes, attempts, distance, margin):
        self.recognition_state = {
            **self._idle_payload(), "state": "strict_confirmation",
            "title": "Đang xác minh danh tính",
            "message": f"Đạt ngưỡng an toàn · {votes}/3 frame, lượt {attempts}/5",
            "metrics": {"distance": distance, "margin": margin},
        }
        self._publish()

    def _add_attendance_record(self, record):
        with self._lock:
            self.attendance_history.insert(0, record)
            del self.attendance_history[100:]
            self._attendance_history_version += 1
            self._save_attendance_history()
            self._event_version += 1

    def _on_kiosk_recognition_success(self, emp_info, distance):
        now = datetime.now()
        now_display = now.strftime("%H:%M:%S · %d/%m/%Y")
        now_value = now.strftime("%H:%M:%S %d/%m/%Y")
        emp_id = emp_info.get("id", "NV---")
        previous = self._attendance_cooldowns.get(emp_id)
        duplicate = bool(
            previous and time.time() - previous[0] < KIOSK_ATTENDANCE_COOLDOWN_SECONDS
        )
        employee = {
            "id": emp_id,
            "name": emp_info.get("name", "Nhân viên"),
            "role": emp_info.get("role", "Nhân viên"),
            "department": emp_info.get("department") or emp_info.get("dept") or "Phòng IT",
            "image_url": f"/api/employees/{emp_id}/image",
        }
        if not duplicate:
            self._attendance_cooldowns[emp_id] = (time.time(), now_display)
            self._add_attendance_record({
                "time": now_value, "name": employee["name"], "id": emp_id,
                "role": employee["role"], "dept": employee["department"],
                "status": "Thành công",
            })
        self.recognition_state = {
            "state": "duplicate" if duplicate else "success",
            "title": "Đã điểm danh trước đó" if duplicate else "Điểm danh thành công",
            "message": (
                f"Lần gần nhất: {previous[1]}" if duplicate
                else f"Thời gian vào: {now_display}"
            ),
            "employee": employee, "timestamp": now_display,
            "metrics": {"distance": round(float(distance), 4)},
        }
        self._kiosk_waiting_for_departure = True
        self.is_recognizing = False
        self._is_kiosk_ui_idle = False
        self._publish()

    def _on_kiosk_recognition_ambiguous(self, best_distance, second_distance, margin):
        now_value = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
        self._add_attendance_record({
            "time": now_value, "name": "Chưa xác định", "id": "—",
            "role": "—", "dept": "—", "status": "Thất bại",
            "reason": "Không đủ độ tin cậy",
            "distance": round(float(best_distance), 4),
            "second_distance": round(float(second_distance), 4),
            "margin": round(float(margin), 4),
        })
        self.recognition_state = {
            **self._idle_payload(), "state": "ambiguous",
            "title": "Không thể xác minh danh tính",
            "message": "Không có hồ sơ nào đủ độ tin cậy",
            "timestamp": now_value,
            "metrics": {"distance": best_distance, "margin": margin},
        }
        self._kiosk_waiting_for_departure = True
        self.is_recognizing = False
        self._is_kiosk_ui_idle = False
        self._publish()

    def _on_kiosk_recognition_failed(self, distance):
        now_value = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
        self._add_attendance_record({
            "time": now_value, "name": "Người lạ / Khách", "id": "—",
            "role": "Chưa đăng ký", "dept": "—", "status": "Thất bại",
            "distance": round(float(distance), 4) if distance is not None else None,
        })
        self.recognition_state = {
            **self._idle_payload(), "state": "unknown",
            "title": "Chưa nhận diện được",
            "message": "Khuôn mặt chưa có trong hệ thống",
            "timestamp": now_value,
        }
        self._kiosk_waiting_for_departure = True
        self.is_recognizing = False
        self._is_kiosk_ui_idle = False
        self._publish()

    def set_idle_state(self):
        self.is_recognizing = False
        self._kiosk_waiting_for_departure = False
        self._kiosk_idle_reset_pending = False
        self._kiosk_active_face_area = None
        self._kiosk_face_stable_since = None
        self._kiosk_unknown_attempts = 0
        self._clear_strict_confirmation()
        self.recognition_state = self._idle_payload()
        self._is_kiosk_ui_idle = True
        self._publish()

    def retry_recognition(self):
        self.set_idle_state()
        self._kiosk_face_stable_since = time.time()
        return self.snapshot()

    def test_recognition_image(self, image_bytes, suffix=".jpg", display_name="Ảnh kiểm thử"):
        """Forward a temporary upload to the existing static-image AI worker."""
        if not image_bytes:
            raise ValueError("Tệp ảnh trống")
        if len(image_bytes) > 12 * 1024 * 1024:
            raise ValueError("Ảnh kiểm thử không được lớn hơn 12 MB")
        allowed = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        suffix = suffix.lower() if suffix.lower() in allowed else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as file:
            file.write(image_bytes)
            temporary_path = Path(file.name)
        version = int(self.recognition_test_result.get("version", 0)) + 1
        self.recognition_test_result = {
            "version": version, "state": "processing", "result": None,
            "error": None, "display_name": display_name,
        }
        self._publish()

        def run_existing_worker():
            try:
                self._worker_recognition_test_image(str(temporary_path))
            finally:
                temporary_path.unlink(missing_ok=True)

        threading.Thread(target=run_existing_worker, daemon=True).start()
        return self.snapshot()

    def _show_recognition_test_result(self, result, error):
        version = int(self.recognition_test_result.get("version", 0))
        safe_result = None
        if result:
            best = result.get("best_profile") or {}
            second = result.get("second_profile") or {}
            second_distance = result.get("second_distance")
            safe_result = {
                "file": self.recognition_test_result.get("display_name") or result.get("file"),
                "conclusion": result.get("conclusion"),
                "best": {"id": best.get("id"), "name": best.get("name")},
                "best_distance": round(float(result.get("best_distance", 0)), 4),
                "second": {"id": second.get("id"), "name": second.get("name")},
                "second_distance": (
                    round(float(second_distance), 4)
                    if second_distance is not None and second_distance != float("inf")
                    else None
                ),
                "margin": round(float(result.get("margin", 0)), 4),
            }
        self.recognition_test_result = {
            "version": version,
            "state": "error" if error else "complete",
            "result": safe_result,
            "error": error,
        }
        self._publish()

    # ---------- Enrollment adapter: worker trích vector vẫn là worker gốc ----------
    def save_enrollment(self, payload):
        if self._enrollment_saving:
            raise ValueError("Hệ thống đang lưu một hồ sơ khác")
        name = str(payload.get("name", "")).strip()
        emp_id = str(payload.get("id", "")).strip()
        role = str(payload.get("role", "")).strip()
        department = str(payload.get("department", "")).strip()
        if not all((name, emp_id, role, department)):
            raise ValueError("Vui lòng nhập đầy đủ thông tin nhân viên")
        if re.search(r'[\s<>:"/\\|?*@]', emp_id):
            raise ValueError("Mã nhân viên không được có khoảng trắng hoặc ký tự đặc biệt")
        duplicate = self._find_existing_employee_id(emp_id)
        if duplicate:
            raise ValueError(f"Mã nhân viên {duplicate} đã tồn tại")
        if not self.camera_running:
            raise ValueError("Camera chưa sẵn sàng")
        if not self.enrollment_capture_ready:
            raise ValueError("Hãy giữ khuôn mặt hợp lệ trong 3 giây trước khi lưu")
        samples = [sample.copy() for sample in self.enrollment_captured_samples[-ENROLLMENT_EMBEDDING_SAMPLES:]]
        if len(samples) < ENROLLMENT_MIN_SAMPLES:
            raise ValueError("Dữ liệu quét khuôn mặt chưa đầy đủ")
        frame = self.enrollment_captured_frame
        if frame is None:
            raise ValueError("Không tìm thấy ảnh khuôn mặt đã quét")
        self._enrollment_saving = True
        self.enrollment_result = {
            "state": "saving", "title": "Đang lưu khuôn mặt",
            "message": "Đang trích xuất vector ArcFace...",
        }
        self._publish()
        threading.Thread(
            target=self._ai_worker_save_face,
            args=(samples, frame.copy(), name, emp_id, role, department),
            daemon=True,
        ).start()
        return self.snapshot()

    def _finish_enrollment(self, success, name="", emp_id="", error_msg=""):
        self._enrollment_saving = False
        if success:
            self._reset_enrollment_scan(clear_preview=False, wait_for_face_leave=True)
            self.enrollment_result = {
                "state": "success", "title": "Lưu khuôn mặt thành công",
                "message": f"{name} ({emp_id}) đã được thêm vào hệ thống.",
            }
        else:
            self.enrollment_result = {
                "state": "error", "title": "Không thể lưu khuôn mặt",
                "message": error_msg or "Vui lòng thử lại.",
            }
        self._publish()

    def reset_enrollment(self):
        self._reset_enrollment_scan(clear_preview=False)
        self.enrollment_result = {
            "state": "idle", "title": "Chờ khuôn mặt hợp lệ",
            "message": "Giữ yên khuôn mặt trong 3 giây để hệ thống tự động quét.",
        }
        self._publish()
        return self.snapshot()

    # ---------- Dữ liệu quản trị ----------
    def _read_profiles(self):
        path = Path("data/embeddings.pkl")
        if not path.exists():
            return {}
        with path.open("rb") as file:
            return pickle.load(file)

    def _write_profiles(self, profiles):
        path = Path("data/embeddings.pkl")
        temporary = path.with_suffix(".pkl.tmp")
        with temporary.open("wb") as file:
            pickle.dump(profiles, file)
        temporary.replace(path)
        self.embeddings_cache = profiles

    def _profile_image(self, emp_id, profile=None):
        candidates = []
        if profile and profile.get("image_path"):
            candidates.append(Path(profile["image_path"]))
        for folder in (Path("database/images"), Path("images"), Path(DATA_FACES_DIR)):
            candidates.extend(folder.glob(f"{emp_id}@*"))
            candidates.extend(folder.glob(f"{emp_id}_*"))
        return next((path for path in candidates if path.exists()), None)

    def list_employees(self, query="", page=1, page_size=10):
        profiles = self.embeddings_cache
        if profiles is None:
            profiles = self._read_profiles()
            self.embeddings_cache = profiles
        all_total = len(profiles)
        enabled_total = sum(
            bool(profile.get("recognition_enabled", True))
            for profile in profiles.values()
        )
        query = query.strip().casefold()
        records = []
        for key, profile in profiles.items():
            emp_id = str(profile.get("id") or key)
            name = str(profile.get("name") or "Nhân viên")
            if query and query not in emp_id.casefold() and query not in name.casefold():
                continue
            records.append({
                "id": emp_id, "name": name,
                "role": profile.get("role") or "Nhân viên",
                "department": profile.get("department") or profile.get("dept") or "Phòng IT",
                "recognition_enabled": bool(profile.get("recognition_enabled", True)),
                "image_url": f"/api/employees/{emp_id}/image",
                "timestamp": profile.get("timestamp"),
            })
        records.sort(key=lambda item: item["id"].casefold())
        total = len(records)
        page_size = max(1, min(int(page_size), 100))
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        start = (page - 1) * page_size
        return {
            "items": records[start:start + page_size],
            "total": total,
            "all_total": all_total,
            "enabled_total": enabled_total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }

    def employee_image(self, emp_id):
        profiles = self._read_profiles()
        key = next((k for k in profiles if str(k).casefold() == emp_id.casefold()), None)
        return self._profile_image(emp_id, profiles.get(key) if key is not None else None)

    @staticmethod
    def _clean_field(value, compact=False):
        value = re.sub(r'[<>:"/\\|?*@]', "", str(value).strip())
        value = re.sub(r"\s+", " ", value)
        return value.replace(" ", "") if compact else value

    def update_employee(self, emp_id, payload):
        with self._lock:
            profiles = self._read_profiles()
            key = next((k for k in profiles if str(k).casefold() == emp_id.casefold()), None)
            if key is None:
                raise KeyError("Không tìm thấy nhân viên")
            new_id = self._clean_field(payload.get("id", emp_id), compact=True)
            name = self._clean_field(payload.get("name", ""))
            role = self._clean_field(payload.get("role", ""))
            department = self._clean_field(payload.get("department", ""))
            if not all((new_id, name, role, department)):
                raise ValueError("Vui lòng nhập đầy đủ thông tin")
            duplicate = next((k for k in profiles if str(k).casefold() == new_id.casefold() and k != key), None)
            if duplicate is not None:
                raise ValueError(f"Mã nhân viên {new_id} đã tồn tại")
            profile = dict(profiles.pop(key))
            timestamp = profile.get("timestamp") or datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{new_id}@{role.replace(' ', '_')}@{department.replace(' ', '_')}@{name.replace(' ', '_')}_{timestamp}.jpg"
            renames = []
            if new_id != emp_id or any(profile.get(k) != v for k, v in (("name", name), ("role", role), ("department", department))):
                for folder in (Path(DATA_FACES_DIR), Path("images"), Path("database/images")):
                    source = next(iter(folder.glob(f"{emp_id}@*")), None)
                    if source and source.exists():
                        target = folder / filename
                        if target != source:
                            if target.exists():
                                raise ValueError(f"Tệp khuôn mặt đích đã tồn tại: {target.name}")
                            renames.append((source, target))
            completed = []
            try:
                for source, target in renames:
                    source.rename(target)
                    completed.append((source, target))
            except Exception:
                for source, target in reversed(completed):
                    if target.exists() and not source.exists():
                        target.rename(source)
                raise
            profile.update({"id": new_id, "name": name, "role": role, "department": department, "image_path": str(Path(DATA_FACES_DIR) / filename)})
            profiles[new_id] = profile
            self._write_profiles(profiles)
        self._publish()
        return self.list_employees(query=new_id, page=1, page_size=1)["items"][0]

    def toggle_employee(self, emp_id):
        with self._lock:
            profiles = self._read_profiles()
            key = next((k for k in profiles if str(k).casefold() == emp_id.casefold()), None)
            if key is None:
                raise KeyError("Không tìm thấy nhân viên")
            profiles[key]["recognition_enabled"] = not bool(profiles[key].get("recognition_enabled", True))
            self._write_profiles(profiles)
            value = profiles[key]["recognition_enabled"]
        self._publish()
        return {"id": emp_id, "recognition_enabled": value}

    def delete_employee(self, emp_id):
        with self._lock:
            profiles = self._read_profiles()
            key = next((k for k in profiles if str(k).casefold() == emp_id.casefold()), None)
            if key is None:
                raise KeyError("Không tìm thấy nhân viên")
            profiles.pop(key)
            for folder in (Path(DATA_FACES_DIR), Path("images"), Path("database/images")):
                for image in list(folder.glob(f"{emp_id}@*")) + list(folder.glob(f"{emp_id}_*")):
                    image.unlink(missing_ok=True)
            self._write_profiles(profiles)
        self._publish()

    def history_page(self, page=1, page_size=10, status="", query=""):
        rows = list(self.attendance_history)
        if status:
            rows = [row for row in rows if status.casefold() in str(row.get("status", "")).casefold()]
        if query:
            needle = query.casefold()
            rows = [row for row in rows if needle in str(row.get("name", "")).casefold() or needle in str(row.get("id", "")).casefold()]
        total = len(rows)
        page_size = max(1, min(int(page_size), 100))
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        start = (page - 1) * page_size
        return {"items": rows[start:start + page_size], "total": total, "page": page, "page_size": page_size, "pages": pages}

    def dashboard(self):
        today = datetime.now().strftime("%d/%m/%Y")
        today_rows = [row for row in self.attendance_history if today in str(row.get("time", ""))]
        success = sum("Thành công" in str(row.get("status", "")) for row in today_rows)
        failed = len(today_rows) - success
        profiles = self.embeddings_cache
        if profiles is None:
            profiles = self._read_profiles()
            self.embeddings_cache = profiles
        start = datetime.now().date() - timedelta(days=datetime.now().weekday())
        week = []
        names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
        for offset, label in enumerate(names):
            date = start + timedelta(days=offset)
            token = date.strftime("%d/%m/%Y")
            count = sum(token in str(row.get("time", "")) and "Thành công" in str(row.get("status", "")) for row in self.attendance_history)
            week.append({"label": label, "date": date.strftime("%d/%m"), "value": count})
        return {
            "employees": len(profiles), "today_total": len(today_rows),
            "today_success": success, "today_failed": failed,
            "success_rate": round(success / len(today_rows) * 100) if today_rows else 0,
            "week": week, "recent": self.attendance_history[:5],
        }
