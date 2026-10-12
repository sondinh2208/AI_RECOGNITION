"""Headless face-recognition and attendance pipeline."""

import cv2
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

from camera_capture import open_camera_capture

from config import (
    DEEPFACE_MODEL_NAME, ARCFACE_THRESHOLD, ARCFACE_TOP2_MARGIN,
    ARCFACE_UNCERTAIN_THRESHOLD, ARCFACE_STRICT_CONFIRMATIONS,
    ARCFACE_HIGH_CONFIDENCE_THRESHOLD, ARCFACE_HIGH_CONFIDENCE_MARGIN,
    ARCFACE_STRICT_MAX_ATTEMPTS, ARCFACE_STRICT_WINDOW_SECONDS,
    KIOSK_MIN_FACE_WIDTH, FACE_CROP_PADDING_RATIO,
    KIOSK_PRIMARY_FACE_MIN_AREA_RATIO, KIOSK_FACE_STABLE_SECONDS,
    KIOSK_CONFIRMATION_INTERVAL_SECONDS, KIOSK_RESULT_HOLD_SECONDS,
    KIOSK_PROCESSED_FACE_TTL_SECONDS, KIOSK_PROCESSED_FACE_ABSENCE_SECONDS,
    KIOSK_UNKNOWN_CONFIRMATIONS,
    KIOSK_DETECTION_INTERVAL_SECONDS, CAMERA_STREAM_URL, CAMERA_SOURCES,
    CAMERA_HORIZONTAL_FLIP,
)
from ai_engine import (
    detect_faces, calculate_cosine_distance, draw_kiosk_face_box,
    align_face_crop, validate_kiosk_face_candidate,
)


def classify_recognition_score(distance, margin):
    """Phân loại open-set; chỉ kết quả strict mới có quyền ghi điểm danh."""
    if distance <= ARCFACE_THRESHOLD and margin >= ARCFACE_TOP2_MARGIN:
        return "strict"
    if distance <= ARCFACE_UNCERTAIN_THRESHOLD:
        return "ambiguous"
    return "unknown"

def is_high_confidence_recognition(distance, margin):
    """Cho phép xác nhận nhanh duy nhất khi Top 1 vừa gần mẫu vừa cách xa Top 2."""
    return bool(
        distance <= ARCFACE_HIGH_CONFIDENCE_THRESHOLD
        and margin >= ARCFACE_HIGH_CONFIDENCE_MARGIN
    )

class AttendanceMixin:
    """Pipeline camera v? ch?nh s?ch nh?n di?n ?i?m danh."""

    @staticmethod
    def _kiosk_box_iou(first, second):
        """Tính IoU giữa hai khung mặt để theo dõi nhẹ mà không thêm model tracker."""
        ax1, ay1, ax2, ay2 = first
        bx1, by1, bx2, by2 = second
        intersection_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        intersection_h = max(0, min(ay2, by2) - max(ay1, by1))
        intersection = intersection_w * intersection_h
        first_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        second_area = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = first_area + second_area - intersection
        return intersection / union if union > 0 else 0.0

    @classmethod
    def _kiosk_same_face_track(cls, first, second):
        """Ghép hai khung gần nhau; dung sai cho phép người dùng di chuyển nhẹ."""
        if cls._kiosk_box_iou(first, second) >= 0.20:
            return True
        ax1, ay1, ax2, ay2 = first
        bx1, by1, bx2, by2 = second
        first_w, first_h = max(1, ax2 - ax1), max(1, ay2 - ay1)
        second_w, second_h = max(1, bx2 - bx1), max(1, by2 - by1)
        first_area = first_w * first_h
        second_area = second_w * second_h
        area_ratio = second_area / first_area
        center_distance = ((ax1 + ax2 - bx1 - bx2) ** 2 + (ay1 + ay2 - by1 - by2) ** 2) ** 0.5 / 2
        scale = max(first_w, first_h, second_w, second_h)
        return 0.45 <= area_ratio <= 2.20 and center_distance <= 0.35 * scale

    def _hold_result_and_suppress_active_face(self, now=None):
        """Giữ kết quả ngắn hạn, nhưng không buộc toàn bộ camera phải trống."""
        now = time.time() if now is None else now
        active_box = getattr(self, '_kiosk_active_face_box', None)
        tracks = list(getattr(self, '_kiosk_suppressed_face_tracks', []))
        if active_box is not None:
            tracks.append({
                "box": tuple(active_box),
                "suppressed_at": now,
                "last_seen": now,
            })
        self._kiosk_suppressed_face_tracks = tracks
        self._kiosk_result_hold_until = now + KIOSK_RESULT_HOLD_SECONDS
        self._kiosk_waiting_for_departure = True

    def _filter_processed_kiosk_faces(self, faces, now=None):
        """Bỏ qua tạm thời các mặt đã xử lý và trả về hàng đợi mặt mới."""
        now = time.time() if now is None else now
        tracks = [
            dict(track)
            for track in getattr(self, '_kiosk_suppressed_face_tracks', [])
            if now - track["suppressed_at"] < KIOSK_PROCESSED_FACE_TTL_SECONDS
        ]
        matched_face_indexes = set()
        live_tracks = []
        for track in tracks:
            best_index = None
            best_iou = -1.0
            for index, face in enumerate(faces):
                if index in matched_face_indexes:
                    continue
                box = tuple(face[:4])
                if not self._kiosk_same_face_track(track["box"], box):
                    continue
                iou = self._kiosk_box_iou(track["box"], box)
                if iou > best_iou:
                    best_iou = iou
                    best_index = index
            if best_index is not None:
                track["box"] = tuple(faces[best_index][:4])
                track["last_seen"] = now
                matched_face_indexes.add(best_index)
                live_tracks.append(track)
            elif now - track["last_seen"] < KIOSK_PROCESSED_FACE_ABSENCE_SECONDS:
                live_tracks.append(track)
        self._kiosk_suppressed_face_tracks = live_tracks
        return [face for index, face in enumerate(faces) if index not in matched_face_indexes]

    def _resume_kiosk_after_result(self):
        """Mở lượt tiếp theo sau khi đủ thời gian đọc kết quả."""
        self._kiosk_waiting_for_departure = False
        self._kiosk_result_hold_until = 0.0
        self.set_idle_state()

    def _start_kiosk_worker(self):
        """Khởi động luồng đọc camera Kiosk với token bảo vệ chống xung đột luồng cũ."""
        if getattr(self, 'kiosk_running', False) and getattr(self, 'kiosk_thread', None) is not None and self.kiosk_thread.is_alive():
            return
        self._kiosk_thread_token = getattr(self, '_kiosk_thread_token', 0) + 1
        cur_token = self._kiosk_thread_token
        self.kiosk_running = True
        print("[CAMERA] worker start")
        self.kiosk_thread = threading.Thread(
            target=self._kiosk_camera_loop,
            args=(cur_token,),
            daemon=True
        )
        self.kiosk_thread.start()

    def _kiosk_camera_loop(self, token=None):
        """
        Background Thread chuyên biệt cho Kiosk Mode (Nhận diện điểm danh):
        - Đọc frame camera liên tục.
        - Phát hiện khuôn mặt bằng YOLOv8.
        - Vẽ HUD Kiosk hiện đại (Bounding box bo góc, nhãn DA XAC THUC / DANG NHAN DIEN).
        - Khi có khuôn mặt hợp lệ và không bận nhận diện -> trigger luồng phụ DeepFace ArcFace.
        """
        prev_time = time.time()
        smoothed_fps = 0.0
        first_frame_logged = False
        consecutive_read_failures = 0
        last_reconnect_attempt = 0.0
        last_detection_at = 0.0
        cached_faces = []
        # Tach YOLO khoi luong doc camera de video khong bi khoa theo FPS AI.
        detector_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="face-detector"
        )
        detection_future = None

        while self.kiosk_running and self.camera_running:
            if token is not None and getattr(self, '_kiosk_thread_token', None) != token:
                break
                
            if self.camera_cap is None:
                time.sleep(0.05)
                continue
                
            ret, frame = self.camera_cap.read()
            if not ret or frame is None:
                consecutive_read_failures += 1
                now_fail = time.time()
                if consecutive_read_failures > 20 and (now_fail - last_reconnect_attempt > 2.0):
                    last_reconnect_attempt = now_fail
                    try:
                        self.camera_cap.release()
                    except Exception:
                        pass
                    cam_source = CAMERA_STREAM_URL if CAMERA_STREAM_URL else (CAMERA_SOURCES[0] if CAMERA_SOURCES else 0)
                    backend = cv2.CAP_FFMPEG if isinstance(cam_source, str) else cv2.CAP_ANY
                    new_cap = open_camera_capture(cam_source, backend)
                    if new_cap.isOpened():
                        new_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        self.camera_cap = new_cap
                        consecutive_read_failures = 0
                        print(f"[CAMERA] Da tu dong ket noi lai camera: {cam_source}")
                    else:
                        new_cap.release()
                time.sleep(0.05)
                continue
            consecutive_read_failures = 0
                
            if not first_frame_logged:
                print("[CAMERA] first frame")
                first_frame_logged = True
                
            if CAMERA_HORIZONTAL_FLIP:
                frame = cv2.flip(frame, 1)
            clean_frame = frame.copy()
            fh, fw = frame.shape[:2]
            
            # 1. Giới hạn tần suất YOLO; camera vẫn cập nhật mượt bằng kết quả gần nhất.
            detection_now = time.perf_counter()
            if detection_future is not None and detection_future.done():
                try:
                    cached_faces = detection_future.result()
                except Exception as exc:
                    print(f"[AI] Loi detector nen: {exc}")
                    cached_faces = []
                detection_future = None
                last_detection_at = detection_now
            if (
                detection_future is None
                and detection_now - last_detection_at
                >= KIOSK_DETECTION_INTERVAL_SECONDS
            ):
                detection_future = detector_executor.submit(
                    detect_faces, self.face_model, clean_frame.copy(), self.device
                )
            faces = cached_faces
            self.kiosk_face_count = len(faces)
            
            # 2. Chọn khuôn mặt gần camera nhất theo diện tích bounding box.
            curr_now = time.time()
            eligible_faces = self._filter_processed_kiosk_faces(faces, curr_now)
            ranked_faces = sorted(
                eligible_faces,
                key=lambda face: (face[2] - face[0]) * (face[3] - face[1]),
                reverse=True,
            )
            primary_face = ranked_faces[0] if ranked_faces else None
            primary_area = (
                (primary_face[2] - primary_face[0])
                * (primary_face[3] - primary_face[1])
                if primary_face else 0
            )
            second_area = (
                (ranked_faces[1][2] - ranked_faces[1][0])
                * (ranked_faces[1][3] - ranked_faces[1][1])
                if len(ranked_faces) > 1 else 0
            )
            primary_is_clear = bool(
                primary_face
                and (
                    second_area <= 0
                    or primary_area / second_area >= KIOSK_PRIMARY_FACE_MIN_AREA_RATIO
                )
            )

            # Mặt chính màu xanh; các mặt phía xa chỉ đánh dấu xám và bị bỏ qua.
            for face in faces:
                fx1, fy1, fx2, fy2, _ = face
                is_primary = face == primary_face
                draw_kiosk_face_box(
                    frame, fx1, fy1, fx2, fy2,
                    color=(100, 255, 100) if is_primary else (150, 150, 150),
                    thickness=3 if is_primary else 1,
                )
                
            waiting_for_departure = getattr(
                self, '_kiosk_waiting_for_departure', False
            )
            if primary_face:
                self._kiosk_face_absent_since = None
                if not waiting_for_departure:
                    if primary_is_clear and self._kiosk_face_stable_since is None:
                        self._kiosk_face_stable_since = curr_now
                    elif not primary_is_clear:
                        self._kiosk_face_stable_since = None
                if len(faces) > 1 and not primary_is_clear and not waiting_for_departure:
                    if curr_now - getattr(self, '_last_multi_face_notice_ts', 0) >= 1.0:
                        self._last_multi_face_notice_ts = curr_now
                        self._safe_after(0, self._show_multiple_faces_notice)
            else:
                self._kiosk_face_stable_since = None
                if self._kiosk_face_absent_since is None:
                    self._kiosk_face_absent_since = curr_now


            # Kết quả chỉ được giữ trong một khoảng ngắn. Sau đó hệ thống
            # mở lượt mới ngay cả khi camera vẫn còn người; vùng mặt vừa
            # xử lý đã bị loại tạm thời khỏi hàng đợi phía trên.
            if (
                waiting_for_departure
                and curr_now >= getattr(self, '_kiosk_result_hold_until', 0.0)
                and not self.is_recognizing
                and not getattr(self, '_kiosk_idle_reset_pending', False)
            ):
                self._kiosk_idle_reset_pending = True
                self._safe_after(0, self._resume_kiosk_after_result)

            # 3. Cơ chế kích hoạt Nhận diện Tự Động (Auto Inference Trigger):
            face_stable_for = (
                curr_now - self._kiosk_face_stable_since
                if self._kiosk_face_stable_since is not None else 0.0
            )
            if (
                primary_face is not None
                and primary_is_clear
                and not self.is_recognizing
                and not getattr(self, '_kiosk_waiting_for_departure', False)
                and face_stable_for >= KIOSK_FACE_STABLE_SECONDS
            ):
                fx1, fy1, fx2, fy2, fconf = primary_face
                face_w = fx2 - fx1
                face_h = fy2 - fy1
                
                if face_w < KIOSK_MIN_FACE_WIDTH:
                    self._kiosk_face_stable_since = curr_now
                    self._safe_after(
                        0, self._on_kiosk_face_quality_rejected,
                        "Vui lòng di chuyển lại gần camera"
                    )
                else:
                    is_valid_face, quality_message = validate_kiosk_face_candidate(
                        (fx1, fy1, fx2, fy2),
                        fconf,
                        clean_frame,
                        getattr(self, 'face_detector', None),
                    )
                    if not is_valid_face:
                        self._kiosk_face_stable_since = curr_now
                        self._safe_after(
                            0, self._on_kiosk_face_quality_rejected, quality_message
                        )
                        continue

                    pad_x = int(face_w * FACE_CROP_PADDING_RATIO)
                    pad_y = int(face_h * FACE_CROP_PADDING_RATIO)
                    cx1 = max(0, fx1 - pad_x)
                    cy1 = max(0, fy1 - pad_y)
                    cx2 = min(fw, fx2 + pad_x)
                    cy2 = min(fh, fy2 + pad_y)
                    
                    if cx2 > cx1 and cy2 > cy1:
                        cropped_img = clean_frame[cy1:cy2, cx1:cx2].copy()
                        # Đặt cờ self.is_recognizing = True để bỏ qua các frame tiếp theo
                        self.is_recognizing = True
                        self._kiosk_active_face_area = primary_area
                        self._kiosk_active_face_box = (fx1, fy1, fx2, fy2)
                        self._kiosk_face_stable_since = None
                        
                        self._safe_after(0, self._on_kiosk_recognizing_started)
                        
                        # Đẩy vào luồng ngầm (Background Thread)
                        threading.Thread(
                            target=self._ai_worker_recognize,
                            args=(cropped_img,),
                            daemon=True
                        ).start()
            
            # 4. Tính toán và làm mượt FPS
            curr_time = time.time()
            instant_fps = 1.0 / (curr_time - prev_time) if curr_time > prev_time else 0
            prev_time = curr_time
            smoothed_fps = instant_fps if smoothed_fps == 0.0 else (0.85 * smoothed_fps + 0.15 * instant_fps)
            self.kiosk_fps = smoothed_fps
            
            # 5. Xuất trực tiếp frame BGR cho luồng MJPEG của web runtime.
            self.kiosk_latest_frame = frame
            self._current_frame_id = getattr(self, '_current_frame_id', 0) + 1
            if not getattr(self, '_first_processed_logged', False):
                print("[ATTENDANCE] first processed frame")
                self._first_processed_logged = True

        detector_executor.shutdown(wait=True, cancel_futures=True)

    def _clear_strict_confirmation(self):
        self._kiosk_strict_candidate_id = None
        self._kiosk_strict_confirmations = 0
        self._kiosk_strict_attempts = 0
        self._kiosk_strict_started_at = None
        self._kiosk_strict_distances = []
        self._kiosk_strict_margins = []

    def _schedule_next_strict_frame(self):
        """Mở khóa frame kế tiếp nhanh hơn nhưng vẫn chừa thời gian ổn định."""
        self.is_recognizing = False
        elapsed_credit = max(
            0.0,
            KIOSK_FACE_STABLE_SECONDS - KIOSK_CONFIRMATION_INTERVAL_SECONDS,
        )
        self._kiosk_face_stable_since = time.time() - elapsed_credit

    def _strict_window_expired(self, now=None):
        now = time.time() if now is None else now
        started = self._kiosk_strict_started_at
        return bool(
            started is not None
            and now - started > ARCFACE_STRICT_WINDOW_SECONDS
        )

    def _record_strict_vote(self, candidate_id, distance, margin, now=None):
        """Ghi phiếu strict trong cửa sổ; một ID strict khác sẽ bắt đầu lại chuỗi."""
        now = time.time() if now is None else now
        candidate_id = str(candidate_id or "")
        if (
            self._kiosk_strict_started_at is None
            or self._strict_window_expired(now)
        ):
            self._clear_strict_confirmation()
            self._kiosk_strict_started_at = now

        self._kiosk_strict_attempts += 1
        if self._kiosk_strict_candidate_id != candidate_id:
            self._kiosk_strict_candidate_id = candidate_id
            self._kiosk_strict_confirmations = 0
            self._kiosk_strict_distances = []
            self._kiosk_strict_margins = []

        self._kiosk_strict_confirmations += 1
        self._kiosk_strict_distances.append(float(distance))
        self._kiosk_strict_margins.append(float(margin))
        return self._strict_window_state()

    def _record_strict_noise(self, now=None):
        """Tính frame nhiễu vào số lượt nhưng không xóa các phiếu strict hợp lệ trước đó."""
        now = time.time() if now is None else now
        if self._kiosk_strict_started_at is None:
            return None
        if self._strict_window_expired(now):
            self._kiosk_strict_attempts = ARCFACE_STRICT_MAX_ATTEMPTS
            return self._strict_window_state()
        self._kiosk_strict_attempts += 1
        return self._strict_window_state()

    def _strict_window_state(self):
        distances = self._kiosk_strict_distances
        margins = self._kiosk_strict_margins
        return {
            "candidate_id": self._kiosk_strict_candidate_id,
            "votes": self._kiosk_strict_confirmations,
            "attempts": self._kiosk_strict_attempts,
            "exhausted": (
                self._kiosk_strict_attempts >= ARCFACE_STRICT_MAX_ATTEMPTS
                or self._strict_window_expired()
            ),
            "median_distance": float(np.median(distances)) if distances else None,
            "median_margin": float(np.median(margins)) if margins else None,
        }

    @staticmethod
    def _strict_state_is_acceptable(state):
        """Cho phép thành công khi đủ phiếu strict cùng một ID trong cửa sổ."""
        return bool(
            state
            and state.get("candidate_id")
            and state.get("votes", 0) >= ARCFACE_STRICT_CONFIRMATIONS
            and state.get("median_distance") is not None
            and state["median_distance"] <= ARCFACE_THRESHOLD
            and state.get("median_margin") is not None
            and state["median_margin"] >= ARCFACE_TOP2_MARGIN
        )

    def _worker_recognition_test_image(self, image_path):
        """Chạy pipeline ảnh tĩnh trong nền, bao gồm cả hồ sơ chỉ kiểm thử."""
        try:
            from deepface import DeepFace

            image_bytes = np.fromfile(image_path, dtype=np.uint8)
            image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Không thể đọc định dạng ảnh đã chọn")

            faces = detect_faces(self.face_model, image, self.device)
            if len(faces) != 1:
                raise ValueError(
                    f"Ảnh phải có đúng một khuôn mặt; hệ thống phát hiện {len(faces)}"
                )
            fx1, fy1, fx2, fy2, _ = faces[0]
            height, width = image.shape[:2]
            pad_x = int((fx2 - fx1) * FACE_CROP_PADDING_RATIO)
            pad_y = int((fy2 - fy1) * FACE_CROP_PADDING_RATIO)
            x1, y1 = max(0, fx1 - pad_x), max(0, fy1 - pad_y)
            x2, y2 = min(width, fx2 + pad_x), min(height, fy2 + pad_y)
            face_crop = image[y1:y2, x1:x2]
            if face_crop.size == 0:
                raise ValueError("Vùng khuôn mặt sau khi crop bị rỗng")
            face_crop, _ = align_face_crop(face_crop, getattr(self, 'face_detector', None))

            representations = DeepFace.represent(
                img_path=face_crop,
                model_name=DEEPFACE_MODEL_NAME,
                detector_backend="skip",
                enforce_detection=False,
            )
            if not representations or representations[0].get("embedding") is None:
                raise ValueError("ArcFace không tạo được vector khuôn mặt")
            query_vector = representations[0]["embedding"]

            if self.embeddings_cache is None:
                self._reload_embeddings_cache()
            ranked = []
            for emp_info in (self.embeddings_cache or {}).values():
                vectors = emp_info.get("embeddings") or []
                if not vectors and emp_info.get("embedding") is not None:
                    vectors = [emp_info["embedding"]]
                if vectors:
                    distance = min(
                        calculate_cosine_distance(query_vector, vector)
                        for vector in vectors
                    )
                    ranked.append((distance, emp_info))
            ranked.sort(key=lambda item: item[0])
            if not ranked:
                raise ValueError("Cơ sở dữ liệu chưa có vector khuôn mặt")

            best_distance, best_profile = ranked[0]
            second_distance, second_profile = (
                ranked[1] if len(ranked) > 1 else (float("inf"), None)
            )
            margin = second_distance - best_distance
            test_decision = classify_recognition_score(best_distance, margin)
            if test_decision == "strict":
                conclusion = "ĐẠT STRICT — cần xác nhận đủ frame strict trước khi ghi nhận"
            elif test_decision == "ambiguous":
                conclusion = "CHƯA ĐỦ TIN CẬY — không được ghi điểm danh"
            else:
                conclusion = "KHÔNG ĐẠT — vượt ngưỡng nhận diện"

            result = {
                "file": Path(image_path).name,
                "best_profile": best_profile,
                "best_distance": best_distance,
                "second_profile": second_profile,
                "second_distance": second_distance,
                "margin": margin,
                "conclusion": conclusion,
            }
            self._safe_after(0, self._show_recognition_test_result, result, None)
        except Exception as exc:
            self._safe_after(0, self._show_recognition_test_result, None, str(exc))

    def _ai_worker_recognize(self, cropped_img):
        """
        Luồng xử lý ngầm (_ai_worker_recognize):
        - Nhận ảnh crop từ YOLO, chạy DeepFace.represent(img_path=cropped_img, model_name='ArcFace', enforce_detection=False).
        - Duyệt qua file embeddings.pkl, tính khoảng cách Cosine với toàn bộ nhân viên.
        - Chỉ chấp nhận khi Top 1 đạt threshold và cách Top 2 đủ margin.
        - Dùng self.after để gọi hàm cập nhật UI.
        """
        try:
            from deepface import DeepFace
            
            # 1. Tự động Căn chỉnh xoay thẳng mặt (Face Alignment) dựa vào 2 mắt
            detector = getattr(self, 'face_detector', None)
            aligned_img, tilt_angle = align_face_crop(cropped_img, detector)
            if abs(tilt_angle) > 2.0:
                print(f"[KIOSK AI] Face Alignment: Đã căn chỉnh xoay mặt {tilt_angle:.1f}° về thẳng")
            
            # 2. Trích xuất Vector khuôn mặt qua ArcFace trên ảnh đã căn chỉnh
            reps = DeepFace.represent(
                img_path=aligned_img,
                model_name=DEEPFACE_MODEL_NAME, # "ArcFace"
                detector_backend="skip",
                enforce_detection=False
            )
            
            if not reps or len(reps) == 0:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return
                
            query_vector = reps[0]["embedding"]
            
            # 2. Đọc cơ sở dữ liệu vector từ RAM cache (siêu tốc, không tốn thời gian đọc đĩa)
            if self.embeddings_cache is None or len(self.embeddings_cache) == 0:
                self._reload_embeddings_cache()
            embeddings_data = self.embeddings_cache
            
            if not embeddings_data:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return

            # 3. Duyệt qua file embeddings.pkl, tính khoảng cách Cosine với toàn bộ nhân viên
            ranked_matches = []
            
            for emp_id, emp_info in embeddings_data.items():
                # Hồ sơ bị tắt (ví dụ dữ liệu benchmark LFW) vẫn được giữ để
                # kiểm thử nhưng tuyệt đối không tham gia nhận diện điểm danh.
                if not emp_info.get("recognition_enabled", True):
                    continue
                employee_vectors = emp_info.get("embeddings") or []
                if not employee_vectors and emp_info.get("embedding") is not None:
                    employee_vectors = [emp_info["embedding"]]
                if employee_vectors:
                    # Khoảng cách của danh tính là mẫu phù hợp nhất trong hồ sơ.
                    dist = min(
                        calculate_cosine_distance(query_vector, emp_emb)
                        for emp_emb in employee_vectors
                    )
                    ranked_matches.append((dist, emp_info))

            ranked_matches.sort(key=lambda item: item[0])
            best_dist, best_emp = ranked_matches[0] if ranked_matches else (999.0, None)
            second_dist = ranked_matches[1][0] if len(ranked_matches) > 1 else float("inf")
            top2_margin = second_dist - best_dist
            second_name = ranked_matches[1][1].get("name") if len(ranked_matches) > 1 else "Không có"

            decision = classify_recognition_score(best_dist, top2_margin)
            print(
                f"[KIOSK AI] Top 1: {best_emp.get('name') if best_emp else 'Không có'} "
                f"({best_dist:.4f}) | Top 2: {second_name} ({second_dist:.4f}) | "
                f"Margin: {top2_margin:.4f} | Threshold: {ARCFACE_THRESHOLD}/"
                f"{ARCFACE_UNCERTAIN_THRESHOLD} | Decision: {decision}"
            )

            if best_emp is not None and decision == "strict":
                self._kiosk_unknown_attempts = 0
                if is_high_confidence_recognition(best_dist, top2_margin):
                    print(
                        "[KIOSK AI] Xác nhận nhanh độ tin cậy cao: "
                        f"distance={best_dist:.4f} <= "
                        f"{ARCFACE_HIGH_CONFIDENCE_THRESHOLD:.4f}, "
                        f"margin={top2_margin:.4f} >= "
                        f"{ARCFACE_HIGH_CONFIDENCE_MARGIN:.4f}"
                    )
                    self._clear_strict_confirmation()
                    self._safe_after(
                        0, self._on_kiosk_recognition_success,
                        best_emp, best_dist,
                    )
                else:
                    candidate_id = str(best_emp.get("id", ""))
                    state = self._record_strict_vote(
                        candidate_id, best_dist, top2_margin
                    )
                    accepted = self._strict_state_is_acceptable(state)
                    if accepted:
                        confirmed_distance = state["median_distance"]
                        self._clear_strict_confirmation()
                        self._safe_after(
                            0, self._on_kiosk_recognition_success,
                            best_emp, confirmed_distance,
                        )
                    elif not state["exhausted"]:
                        self._schedule_next_strict_frame()
                        self._safe_after(
                            0, self._on_kiosk_strict_confirmation_pending,
                            state["votes"], state["attempts"],
                            best_dist, top2_margin,
                        )
                    else:
                        self._clear_strict_confirmation()
                        self._safe_after(
                            0, self._on_kiosk_recognition_ambiguous,
                            best_dist, second_dist, top2_margin,
                        )
            elif best_emp is not None and decision == "ambiguous":
                self._kiosk_unknown_attempts = 0
                state = self._record_strict_noise()
                if state is not None and not state["exhausted"]:
                    self._schedule_next_strict_frame()
                    self._safe_after(
                        0, self._on_kiosk_strict_confirmation_pending,
                        state["votes"], state["attempts"],
                        best_dist, top2_margin,
                    )
                else:
                    self._clear_strict_confirmation()
                    self._safe_after(
                        0, self._on_kiosk_recognition_ambiguous,
                        best_dist, second_dist, top2_margin,
                    )
            else:
                state = self._record_strict_noise()
                if state is not None and not state["exhausted"]:
                    self._schedule_next_strict_frame()
                    self._safe_after(
                        0, self._on_kiosk_strict_confirmation_pending,
                        state["votes"], state["attempts"],
                        best_dist, top2_margin,
                    )
                elif state is not None:
                    self._clear_strict_confirmation()
                    self._safe_after(
                        0, self._on_kiosk_recognition_ambiguous,
                        best_dist, second_dist, top2_margin,
                    )
                else:
                    self._kiosk_unknown_attempts = getattr(self, '_kiosk_unknown_attempts', 0) + 1
                    if self._kiosk_unknown_attempts < KIOSK_UNKNOWN_CONFIRMATIONS:
                        attempt = self._kiosk_unknown_attempts
                        self.is_recognizing = False
                        self._kiosk_face_stable_since = time.time()
                        self._safe_after(
                            0, self._on_kiosk_unknown_confirmation_pending, attempt
                        )
                    else:
                        self._kiosk_unknown_attempts = 0
                        self._safe_after(0, self._on_kiosk_recognition_failed, best_dist)
                
        except Exception as e:
            print(f"[KIOSK AI ERROR]: Lỗi nhận diện: {e}")
            self._clear_strict_confirmation()
            self.is_recognizing = False
            self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
