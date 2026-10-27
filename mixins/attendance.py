"""Headless face-recognition and attendance pipeline."""

import cv2
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

from camera_capture import open_camera_capture
from frame_metrics import FrameRateMeter
from recognition_policy import aggregate_query_embeddings, match_face_profiles

from config import (
    DEEPFACE_MODEL_NAME,
    ARCFACE_QUERY_AGGREGATION_FRAMES, ARCFACE_QUERY_AGGREGATION_MAX_SECONDS,
    KIOSK_MIN_FACE_WIDTH, FACE_CROP_PADDING_RATIO,
    KIOSK_PRIMARY_FACE_MIN_AREA_RATIO, KIOSK_FACE_STABLE_SECONDS,
    KIOSK_CONFIRMATION_INTERVAL_SECONDS, KIOSK_RESULT_HOLD_SECONDS,
    KIOSK_PROCESSED_FACE_TTL_SECONDS, KIOSK_PROCESSED_FACE_ABSENCE_SECONDS,
    KIOSK_UNKNOWN_CONFIRMATIONS,
    KIOSK_DETECTION_INTERVAL_SECONDS, CAMERA_STREAM_URL, CAMERA_SOURCES,
    CAMERA_HORIZONTAL_FLIP, KIOSK_DRAW_DEBUG_BOXES,
)
from ai_engine import (
    detect_faces, draw_kiosk_face_box,
    align_face_crop, validate_kiosk_face_candidate,
)


class AttendanceMixin:
    """Camera capture, face quality checks and attendance recognition."""

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

    def _hold_result_and_suppress_active_face(self, now=None, retry_delay=None):
        """Giữ kết quả ngắn hạn, nhưng không buộc toàn bộ camera phải trống."""
        now = time.time() if now is None else now
        hold_seconds = KIOSK_RESULT_HOLD_SECONDS if retry_delay is None else retry_delay
        active_box = getattr(self, '_kiosk_active_face_box', None)
        tracks = list(getattr(self, '_kiosk_suppressed_face_tracks', []))
        if active_box is not None:
            tracks.append({
                "box": tuple(active_box),
                "suppressed_at": now,
                "last_seen": now,
                "ttl_seconds": max(KIOSK_PROCESSED_FACE_TTL_SECONDS, hold_seconds),
            })
        self._kiosk_suppressed_face_tracks = tracks
        self._kiosk_result_hold_until = now + hold_seconds
        self._kiosk_waiting_for_departure = True

    def _filter_processed_kiosk_faces(self, faces, now=None):
        """Bỏ qua tạm thời các mặt đã xử lý và trả về hàng đợi mặt mới."""
        now = time.time() if now is None else now
        tracks = [
            dict(track)
            for track in getattr(self, '_kiosk_suppressed_face_tracks', [])
            if now - track["suppressed_at"] < track.get("ttl_seconds", KIOSK_PROCESSED_FACE_TTL_SECONDS)
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
        fps_meter = FrameRateMeter()
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
                self.kiosk_fps = fps_meter.value()
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
            self.door_service.observe_faces(self.kiosk_face_count)
            # Presence guards use unfiltered foreground detections, including
            # the face temporarily suppressed after a successful recognition.
            raw_ranked = sorted(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]), reverse=True)
            if not raw_ranked:
                self.door_service.observe_primary_face(None)
            elif len(raw_ranked) == 1 or (
                (raw_ranked[0][2] - raw_ranked[0][0]) * (raw_ranked[0][3] - raw_ranked[0][1])
                >= KIOSK_PRIMARY_FACE_MIN_AREA_RATIO *
                (raw_ranked[1][2] - raw_ranked[1][0]) * (raw_ranked[1][3] - raw_ranked[1][1])
            ):
                self.door_service.observe_primary_face(raw_ranked[0][:4])
            else:
                self.door_service.observe_primary_face(None, ambiguous=True)
            
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

            # Production display stays clean. Bounding boxes can still be
            # enabled explicitly when detector diagnostics are needed.
            if KIOSK_DRAW_DEBUG_BOXES:
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
                self._kiosk_query_embeddings = []
                self._kiosk_query_started_at = None
                self._kiosk_query_face_box = None
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
                        self._kiosk_alert_face = cropped_img
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
            self.kiosk_fps = fps_meter.tick()
            
            # 5. Xuất trực tiếp frame BGR cho luồng MJPEG của web runtime.
            self.kiosk_latest_frame = frame
            self._current_frame_id = getattr(self, '_current_frame_id', 0) + 1
            if not getattr(self, '_first_processed_logged', False):
                print("[ATTENDANCE] first processed frame")
                self._first_processed_logged = True

        detector_executor.shutdown(wait=True, cancel_futures=True)

    def _reset_query_aggregation(self):
        """Discard partial embeddings when the recognition session changes."""
        self._kiosk_query_embeddings = []
        self._kiosk_query_started_at = None
        self._kiosk_query_face_box = None

    def _aggregate_current_query(self, query_vector, now=None):
        """Return one robust query after two fresh frames of the same face track."""
        now = time.time() if now is None else now
        active_box = getattr(self, "_kiosk_active_face_box", None)
        started = getattr(self, "_kiosk_query_started_at", None)
        previous_box = getattr(self, "_kiosk_query_face_box", None)
        expired = started is not None and now - started > ARCFACE_QUERY_AGGREGATION_MAX_SECONDS
        changed_face = bool(
            previous_box is not None
            and active_box is not None
            and not self._kiosk_same_face_track(previous_box, active_box)
        )
        if started is None or expired or changed_face:
            self._kiosk_query_embeddings = []
            self._kiosk_query_started_at = now
        self._kiosk_query_face_box = active_box
        self._kiosk_query_embeddings.append(query_vector)
        if len(self._kiosk_query_embeddings) < ARCFACE_QUERY_AGGREGATION_FRAMES:
            return None
        result = aggregate_query_embeddings(
            self._kiosk_query_embeddings[-ARCFACE_QUERY_AGGREGATION_FRAMES:]
        )
        self._kiosk_query_embeddings = []
        self._kiosk_query_started_at = None
        self._kiosk_query_face_box = None
        return result

    def _schedule_next_query_frame(self):
        """Mở khóa frame kế tiếp nhanh hơn nhưng vẫn chừa thời gian ổn định."""
        self.is_recognizing = False
        elapsed_credit = max(
            0.0,
            KIOSK_FACE_STABLE_SECONDS - KIOSK_CONFIRMATION_INTERVAL_SECONDS,
        )
        self._kiosk_face_stable_since = time.time() - elapsed_credit

    def _worker_recognition_test_image(self, image_path):
        """Chạy pipeline ảnh tĩnh trong nền, bao gồm cả hồ sơ chỉ kiểm thử."""
        try:
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
            query_vector = self._extract_face_embedding(face_crop)
            if query_vector is None:
                raise ValueError("ArcFace không tạo được vector khuôn mặt")

            match = match_face_profiles(
                query_vector, self._get_recognition_profiles(), live_only=False
            )
            if match.best_profile is None:
                raise ValueError("Cơ sở dữ liệu chưa có vector khuôn mặt")

            test_decision = match.decision
            if test_decision == "matched":
                conclusion = "ĐẠT NGƯỠNG — kết quả ảnh tĩnh, không thực hiện mở khóa"
            elif test_decision == "ambiguous":
                conclusion = "CHƯA ĐỦ TIN CẬY — không được ghi điểm danh"
            else:
                conclusion = "KHÔNG ĐẠT — vượt ngưỡng nhận diện"

            result = {
                "file": Path(image_path).name,
                "best_profile": match.best_profile,
                "best_distance": match.best_distance,
                "second_profile": match.second_profile,
                "second_distance": match.second_distance,
                "margin": match.margin,
                "conclusion": conclusion,
            }
            self._safe_after(0, self._show_recognition_test_result, result, None)
        except Exception as exc:
            self._safe_after(0, self._show_recognition_test_result, None, str(exc))

    def _extract_face_embedding(self, face_crop):
        """Align one face crop and extract its ArcFace embedding."""
        from deepface import DeepFace

        aligned_img, _ = align_face_crop(
            face_crop, getattr(self, "face_detector", None)
        )
        representations = DeepFace.represent(
            img_path=aligned_img,
            model_name=DEEPFACE_MODEL_NAME,
            detector_backend="skip",
            enforce_detection=False,
        )
        if not representations:
            return None
        return representations[0].get("embedding")

    def _get_recognition_profiles(self, *, reload_empty=False):
        """Use the RAM cache, reloading only under the caller's existing policy."""
        if self.embeddings_cache is None or (reload_empty and not self.embeddings_cache):
            self._reload_embeddings_cache()
        return self.embeddings_cache or {}

    def _handle_recognition_match(self, match):
        """Dispatch the decision; door access stays in the runtime callbacks."""
        if match.best_profile is None or match.decision == "unknown":
            self._handle_unknown_match(match.best_distance)
            return

        self._kiosk_unknown_attempts = 0
        self._reset_query_aggregation()
        if match.decision == "matched":
            self._safe_after(
                0, self._on_kiosk_recognition_success,
                match.best_profile, match.best_distance,
            )
        else:
            self._safe_after(
                0, self._on_kiosk_recognition_ambiguous,
                match.best_distance, match.second_distance, match.margin,
            )

    def _handle_unknown_match(self, distance):
        """Require repeated unknown comparisons before reporting a final failure."""
        self._kiosk_unknown_attempts = getattr(self, "_kiosk_unknown_attempts", 0) + 1
        if self._kiosk_unknown_attempts < KIOSK_UNKNOWN_CONFIRMATIONS:
            self.is_recognizing = False
            self._kiosk_face_stable_since = time.time()
            self._safe_after(
                0, self._on_kiosk_unknown_confirmation_pending,
                self._kiosk_unknown_attempts,
            )
            return
        self._kiosk_unknown_attempts = 0
        self._safe_after(0, self._on_kiosk_recognition_failed, distance)

    @staticmethod
    def _log_recognition_match(match):
        best_name = match.best_profile.get("name") if match.best_profile else "Không có"
        second_name = match.second_profile.get("name") if match.second_profile else "Không có"
        print(
            f"[KIOSK AI] Top 1: {best_name} ({match.best_distance:.4f}) | "
            f"Top 2: {second_name} ({match.second_distance:.4f}) | "
            f"Margin: {match.margin:.4f} | Decision: {match.decision}"
        )

    def _ai_worker_recognize(self, cropped_img):
        """Extract -> aggregate -> compare -> dispatch, without directly opening a door."""
        try:
            embedding = self._extract_face_embedding(cropped_img)
            if embedding is None:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return

            query = self._aggregate_current_query(embedding)
            if query is None:
                self._schedule_next_query_frame()
                self._safe_after(
                    0, self._on_kiosk_query_aggregation_pending,
                    len(self._kiosk_query_embeddings),
                )
                return

            profiles = self._get_recognition_profiles(reload_empty=True)
            if not profiles:
                self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
                return

            match = match_face_profiles(query, profiles)
            self._log_recognition_match(match)
            self._handle_recognition_match(match)
        except Exception as exc:
            print(f"[KIOSK AI ERROR]: Lỗi nhận diện: {exc}")
            self._reset_query_aggregation()
            self.is_recognizing = False
            self._safe_after(0, self._on_kiosk_recognition_failed, 1.0)
