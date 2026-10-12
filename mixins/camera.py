"""Pipeline camera cho ??ng k? khu?n m?t tr?n web runtime."""

import cv2
import time
import threading
import mediapipe as mp

from camera_capture import open_camera_capture

from config import (
    CAMERA_SOURCES, ENROLLMENT_DETECTION_INTERVAL_SECONDS,
    ENROLLMENT_SAMPLE_INTERVAL_SECONDS, ENROLLMENT_AUTO_CAPTURE_SECONDS,
    ENROLLMENT_VALID_STREAK, ENROLLMENT_INVALID_STREAK,
    ENROLLMENT_MIN_SAMPLES, FACE_CROP_PADDING_RATIO,
    CAMERA_HORIZONTAL_FLIP,
)
from ai_engine import detect_faces, check_face_constraints, measure_face_brightness
from ekyc_renderer import compute_ekyc_layout, create_rounded_rect_mask, render_full_hud


class CameraMixin:
    """Qu?n l? camera v? lu?ng thu m?u ??ng k?."""

    def _start_camera(self):
        """Khởi tạo camera + tính layout eKYC với cấu hình phần cứng tối ưu (MJPG, 30 FPS, buffer 1)."""
        if self.camera_cap is not None:
            self.camera_cap.release()
            self.camera_cap = None
        
        print(f"[CAMERA] Nguồn ưu tiên: {CAMERA_SOURCES}")
        for cam_id in CAMERA_SOURCES:
            backends = [cv2.CAP_FFMPEG, cv2.CAP_ANY] if isinstance(cam_id, str) else [cv2.CAP_DSHOW, cv2.CAP_ANY]
            for backend in backends:
                cap = open_camera_capture(cam_id, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                
                # Cấu hình tối ưu phần cứng (đặc biệt chuẩn nén MJPG 30 FPS cho webcam Logitech C270)
                if not isinstance(cam_id, str):
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                ret, frame = cap.read()
                if ret and frame is not None:
                    self.camera_cap = cap
                    self.camera_running = True
                    self.frame_height, self.frame_width = frame.shape[:2]
                    
                    # Tính layout + tạo mask (1 lần)
                    self.shape_rect, corner_radius = compute_ekyc_layout(
                        self.frame_width, self.frame_height)
                    self.constraint_box = self.shape_rect
                    self.ekyc_mask = create_rounded_rect_mask(
                        self.frame_width, self.frame_height,
                        self.shape_rect, corner_radius)
                    
                    print(f"[CAMERA] Đã kết nối camera ID={cam_id} ({self.frame_width}x{self.frame_height} @ 30 FPS MJPG)")
                    
                    if self.current_page == "attendance":
                        if hasattr(self, '_start_kiosk_worker'):
                            self._start_kiosk_worker()
                        else:
                            self.kiosk_running = True
                            self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                            self.kiosk_thread.start()
                    else:
                        self.enrollment_running = True
                        self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                        self.camera_thread.start()
                    return
                else:
                    cap.release()
        
        print("[CAMERA] KHÔNG TÌM THẤY CAMERA!")

    def _camera_loop(self):
        """
        Background Thread: AI Pipeline Enrollment
        Chuyên đọc frame, chạy AI và render HUD để không block UI.
        """
        prev_time = time.time()
        smoothed_fps = 0.0
        
        # Biến State Machine chống nhiễu (Debounce)
        green_streak = 0
        red_streak = 0
        capture_ready = bool(getattr(self, "enrollment_capture_ready", False))
        display_color = (100, 255, 100) if capture_ready else (50, 50, 255)
        display_text = (
            "DA QUET KHUON MAT - SAN SANG LUU"
            if capture_ready else "Dua mat vao khung hinh"
        )
        display_locked = capture_ready
        current_color = display_color
        faces = []
        mp_results = None
        last_detection_at = 0.0
        
        consecutive_read_failures = 0
        last_reconnect_attempt = 0.0
        
        while self.enrollment_running and self.camera_running:
            if self.camera_cap is None:
                break
                
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
                    cam_source = CAMERA_SOURCES[0] if CAMERA_SOURCES else 0
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
                
            if CAMERA_HORIZONTAL_FLIP:
                frame = cv2.flip(frame, 1)
            self.current_frame = frame.copy()  # Bản sạch để chụp
            
            fw, fh = self.frame_width, self.frame_height
            
            # --- AI Inference có giới hạn tần suất; video vẫn dùng frame mới nhất ---
            detection_now = time.perf_counter()
            if detection_now - last_detection_at >= ENROLLMENT_DETECTION_INTERVAL_SECONDS:
                last_detection_at = detection_now
                faces = detect_faces(self.face_model, frame, self.device)

                if faces:
                    primary_face = max(
                        faces,
                        key=lambda face: (face[2] - face[0]) * (face[3] - face[1]),
                    )
                    light_ok, brightness = measure_face_brightness(
                        frame, primary_face[:4]
                    )
                    self.current_face_brightness = brightness
                    self.is_face_bright = light_ok
                else:
                    self.current_face_brightness = None
                    self.is_face_bright = False

                mp_results = None
                if self.face_detector is not None and len(faces) > 0:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                    mp_results = self.face_detector.detect(mp_image)

                if getattr(self, "enrollment_capture_ready", False):
                    # Snapshot đã khóa: camera vẫn chạy nhưng không tự ghi đè bằng người khác.
                    display_locked = True
                    display_color = (100, 255, 100)
                    display_text = "DA QUET KHUON MAT - SAN SANG LUU"
                elif getattr(self, "_enrollment_waiting_for_face_leave", False):
                    # Sau khi lưu, bắt buộc người cũ rời khung trước lượt đăng ký mới.
                    display_locked = False
                    display_color = (50, 50, 255)
                    display_text = "ROI KHOI KHUNG DE QUET NGUOI TIEP THEO"
                    self._enrollment_valid_since = None
                    self.enrollment_face_samples.clear()
                    self._last_enrollment_sample_at = 0.0
                    green_streak = 0
                    if faces:
                        red_streak = 0
                    else:
                        red_streak += 1
                        if red_streak >= 2:
                            self._enrollment_waiting_for_face_leave = False
                            red_streak = 0
                            display_text = "Dua mat vao khung hinh"
                            on_face_left = getattr(
                                self, "_on_enrollment_face_left", None
                            )
                            if callable(on_face_left):
                                on_face_left()
                elif not getattr(self, "_enrollment_scan_armed", True):
                    # Camera chỉ hiển thị preview. Không thu mẫu cho tới khi
                    # quản trị viên nhập hồ sơ và chủ động bắt đầu lượt mới.
                    display_locked = False
                    display_color = (255, 180, 40)
                    display_text = "CHO THONG TIN NGUOI TIEP THEO"
                    self._enrollment_valid_since = None
                    self.enrollment_face_samples.clear()
                    self._last_enrollment_sample_at = 0.0
                    green_streak = 0
                    red_streak = 0
                else:
                    current_color, status_text, is_locked = check_face_constraints(
                        faces, self.constraint_box, mp_results, fw, fh,
                        frame=frame, strict_mode=True,
                    )

                    # State machine chỉ đếm kết quả AI mới, không đếm frame video lặp.
                    if is_locked and len(faces) == 1:
                        green_streak += 1
                        red_streak = 0
                    else:
                        red_streak += 1
                        green_streak = 0

                    if green_streak >= ENROLLMENT_VALID_STREAK:
                        display_locked = True
                        display_color = current_color
                    elif red_streak >= ENROLLMENT_INVALID_STREAK:
                        display_locked = False
                        display_color = current_color
                        display_text = status_text
                        self._enrollment_valid_since = None
                        self.enrollment_face_samples.clear()
                        self._last_enrollment_sample_at = 0.0

                    # Khi mặt hợp lệ, bắt đầu đếm 3 giây và tự thu các mẫu vào RAM.
                    sample_now = time.perf_counter()
                    # Chỉ lưu những frame hiện tại thực sự đạt kiểm tra chất lượng.
                    # Debounce được phép giữ bộ đếm qua vài frame nhiễu nhưng không
                    # được đưa chính các frame nhiễu đó vào tập vector đăng ký.
                    if display_locked and is_locked and len(faces) == 1:
                        if self._enrollment_valid_since is None:
                            self._enrollment_valid_since = sample_now
                            self.enrollment_face_samples.clear()
                            self._last_enrollment_sample_at = 0.0

                        elapsed = sample_now - self._enrollment_valid_since
                        remaining = max(0.0, ENROLLMENT_AUTO_CAPTURE_SECONDS - elapsed)
                        display_text = f"GIU YEN KHUON MAT: {remaining:.1f} GIAY"

                        if (
                            sample_now - self._last_enrollment_sample_at
                            >= ENROLLMENT_SAMPLE_INTERVAL_SECONDS
                        ):
                            fx1, fy1, fx2, fy2, _ = faces[0]
                            pad_x = int((fx2 - fx1) * FACE_CROP_PADDING_RATIO)
                            pad_y = int((fy2 - fy1) * FACE_CROP_PADDING_RATIO)
                            cx1, cy1 = max(0, fx1 - pad_x), max(0, fy1 - pad_y)
                            cx2, cy2 = min(fw, fx2 + pad_x), min(fh, fy2 + pad_y)
                            if cx2 > cx1 and cy2 > cy1:
                                self.enrollment_face_samples.append(
                                    frame[cy1:cy2, cx1:cx2].copy()
                                )
                                self._last_enrollment_sample_at = sample_now

                        if (
                            elapsed >= ENROLLMENT_AUTO_CAPTURE_SECONDS
                            and len(self.enrollment_face_samples) >= ENROLLMENT_MIN_SAMPLES
                        ):
                            fx1, fy1, fx2, fy2, _ = faces[0]
                            pad_x = int((fx2 - fx1) * FACE_CROP_PADDING_RATIO)
                            pad_y = int((fy2 - fy1) * FACE_CROP_PADDING_RATIO)
                            cx1, cy1 = max(0, fx1 - pad_x), max(0, fy1 - pad_y)
                            cx2, cy2 = min(fw, fx2 + pad_x), min(fh, fy2 + pad_y)
                            if cx2 > cx1 and cy2 > cy1:
                                self.enrollment_captured_samples = [
                                    sample.copy() for sample in self.enrollment_face_samples
                                ]
                                self.enrollment_captured_frame = frame.copy()
                                self.enrollment_captured_face = frame[cy1:cy2, cx1:cx2].copy()
                                self.enrollment_capture_ready = True
                                display_text = "DA QUET KHUON MAT - SAN SANG LUU"
                                print("[ENROLLMENT] Tự động quét khuôn mặt hoàn tất sau 3 giây.")
            
            # Cập nhật biến trạng thái (Atomic)
            self.is_face_valid = display_locked
            self.ai_status_text = display_text
            
            # Lưu telemetry
            highest_conf = 0.0
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                if fconf > highest_conf:
                    highest_conf = fconf
            self.current_fps = smoothed_fps
            self.current_conf = highest_conf
            
            # Log terminal
            if display_text != self.prev_status:
                icon = "✅ XANH" if display_locked else "🔴 ĐỎ"
                print(f"[{icon}] {display_text}")
                self.prev_status = display_text
                
            # --- Render HUD ---
            if self.ekyc_mask is not None:
                output = render_full_hud(
                    frame, self.ekyc_mask, self.shape_rect,
                    display_color, display_text, fw, fh
                )
            else:
                output = frame
                
            # --- Tính và vẽ FPS ---
            curr_time = time.time()
            instant_fps = 1.0 / (curr_time - prev_time) if curr_time > prev_time else 0
            prev_time = curr_time
            
            if smoothed_fps == 0.0:
                smoothed_fps = instant_fps
            else:
                smoothed_fps = 0.9 * smoothed_fps + 0.1 * instant_fps
                
            self.latest_processed_frame = output
            self._current_frame_id = getattr(self, '_current_frame_id', 0) + 1
