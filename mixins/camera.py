"""
==============================================================
CAMERA MIXIN
Quản lý kết nối camera OpenCV, luồng chạy AI background cho tab
quét khuôn mặt nhân viên mới (Enrollment) và cập nhật frame lên UI.
==============================================================
"""

import cv2
import time
import threading
import mediapipe as mp
from PIL import Image
import customtkinter as ctk

from config import (
    ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT, ADMIN_CAMERA_FPS_DELAY,
    ENROLLMENT_DETECTION_INTERVAL_SECONDS, ENROLLMENT_EMBEDDING_SAMPLES,
    ENROLLMENT_SAMPLE_INTERVAL_SECONDS, FACE_CROP_PADDING_RATIO,
    CTK_SUCCESS, CTK_DANGER, CTK_WARNING,
)
from ai_engine import detect_faces, check_face_constraints
from ekyc_renderer import compute_ekyc_layout, create_rounded_rect_mask, render_full_hud


class CameraMixin:
    """Mixin quản lý camera chung và luồng camera Enrollment."""

    def _start_camera(self):
        """Khởi tạo camera + tính layout eKYC."""
        if self.camera_cap is not None:
            self.camera_cap.release()
        
        for cam_id in [1, 0]:
            cap = cv2.VideoCapture(cam_id)
            if cap.isOpened():
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
                    
                    if hasattr(self, 'cam_status_dot'):
                        self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
                    if hasattr(self, 'lbl_stat_status'):
                        self.lbl_stat_status.configure(text="● Hoạt động tốt", text_color=CTK_SUCCESS)
                    if hasattr(self, 'cam_live_status'):
                        self.cam_live_status.configure(text="● ĐANG CHỜ KHUÔN MẶT", text_color=CTK_SUCCESS)
                    print(f"[CAMERA] Đã kết nối camera ID={cam_id} ({self.frame_width}x{self.frame_height})")
                    
                    if self.current_page == "attendance":
                        if hasattr(self, '_start_kiosk_worker'):
                            self._start_kiosk_worker()
                        else:
                            self.kiosk_running = True
                            self.kiosk_thread = threading.Thread(target=self._kiosk_camera_loop, daemon=True)
                            self.kiosk_thread.start()
                        self._update_kiosk_frame()
                    else:
                        self.enrollment_running = True
                        self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                        self.camera_thread.start()
                        self._update_frame()
                    return
                else:
                    cap.release()
        
        if hasattr(self, 'cam_status_dot'):
            self.cam_status_dot.configure(text="● OFFLINE", text_color=CTK_DANGER)
        if hasattr(self, 'lbl_stat_status'):
            self.lbl_stat_status.configure(text="● Mất kết nối", text_color=CTK_DANGER)
        if hasattr(self, 'cam_live_status'):
            self.cam_live_status.configure(text="● KHÔNG CÓ TÍN HIỆU CAMERA", text_color=CTK_DANGER)
        if hasattr(self, 'camera_label'):
            self.camera_label.configure(text="Không thể kết nối camera.\nKiểm tra thiết bị.")
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
        display_color = (50, 50, 255)  # Mặc định Đỏ
        display_text = "Dua mat vao khung hinh"
        display_locked = False
        current_color = display_color
        faces = []
        mp_results = None
        last_detection_at = 0.0
        
        while self.enrollment_running and self.camera_running:
            if self.camera_cap is None:
                break
                
            ret, frame = self.camera_cap.read()
            if not ret:
                time.sleep(0.1)
                continue
                
            frame = cv2.flip(frame, 1)
            self.current_frame = frame.copy()  # Bản sạch để chụp
            
            fw, fh = self.frame_width, self.frame_height
            
            # --- AI Inference có giới hạn tần suất; video vẫn dùng frame mới nhất ---
            detection_now = time.perf_counter()
            if detection_now - last_detection_at >= ENROLLMENT_DETECTION_INTERVAL_SECONDS:
                last_detection_at = detection_now
                faces = detect_faces(self.face_model, frame, self.device)

                mp_results = None
                if self.face_detector is not None and len(faces) > 0:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
                    mp_results = self.face_detector.detect(mp_image)

                current_color, status_text, is_locked = check_face_constraints(
                    faces, self.constraint_box, mp_results, fw, fh
                )

                # State machine chỉ đếm kết quả AI mới, không đếm frame video lặp.
                if is_locked:
                    green_streak += 1
                    red_streak = 0
                else:
                    red_streak += 1
                    green_streak = 0

                if green_streak >= 5:
                    display_locked = True
                    display_color = current_color
                    display_text = status_text
                elif red_streak >= 2:
                    display_locked = False
                    display_color = current_color
                    display_text = status_text
                    self.enrollment_face_samples.clear()

                # Thu nhiều mẫu đạt eKYC vào RAM, cách nhau đủ xa để tránh
                # lưu 5 bản sao gần như giống hệt của cùng một frame.
                sample_now = time.perf_counter()
                if (
                    display_locked and len(faces) == 1
                    and sample_now - self._last_enrollment_sample_at
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
                        while len(self.enrollment_face_samples) > ENROLLMENT_EMBEDDING_SAMPLES:
                            self.enrollment_face_samples.popleft()
            
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
                
            # --- Vẽ Bounding Box & Confidence (Dành cho Debug) ---
            for (fx1, fy1, fx2, fy2, fconf) in faces:
                cv2.rectangle(frame, (fx1, fy1), (fx2, fy2), (255, 150, 0), 2)
                cv2.putText(frame, f"YOLO: {fconf:.2f}", (fx1, fy1 - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 150, 0), 2)
                            
            if mp_results and mp_results.detections:
                for det in mp_results.detections:
                    mp_score = det.categories[0].score
                    bbox = det.bounding_box
                    mx = int(bbox.origin_x)
                    my = int(bbox.origin_y)
                    cv2.putText(frame, f"MP: {mp_score:.2f}", (mx, my - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
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
                
            cv2.putText(output, f"FPS: {int(smoothed_fps)}", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2, cv2.LINE_AA)
                
            try:
                output_resized = cv2.resize(output, (ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT), interpolation=cv2.INTER_LINEAR)
                frame_rgb = cv2.cvtColor(output_resized, cv2.COLOR_BGR2RGB)
                self.latest_pil_image = Image.fromarray(frame_rgb)
            except Exception:
                pass
                
            self.latest_processed_frame = output
            time.sleep(0.001)

    def _update_frame(self):
        """
        Main UI Thread: Lấy ảnh PIL đã định dạng từ background thread và hiển thị (siêu nhẹ, <1ms).
        """
        if not self.camera_running or self.current_page != "add_employee":
            return
            
        pil_image = getattr(self, 'latest_pil_image', None)
        if pil_image is not None and hasattr(self, 'camera_label'):
            ctk_image = ctk.CTkImage(
                light_image=pil_image, dark_image=pil_image,
                size=(ADMIN_CAMERA_WIDTH, ADMIN_CAMERA_HEIGHT),
            )
            self.camera_label.configure(image=ctk_image, text="")
            self.photo_image = ctk_image
        
        # --- Cập nhật Telemetry Metrics ---
        if hasattr(self, 'lbl_stat_fps'):
            self.lbl_stat_fps.configure(text=f"{int(getattr(self, 'current_fps', 0))}")
            
        if hasattr(self, 'lbl_stat_conf'):
            c_val = getattr(self, 'current_conf', 0.0)
            self.lbl_stat_conf.configure(text=f"{c_val:.2f}" if c_val > 0 else "0.00")
            
        if hasattr(self, 'cam_live_status'):
            if self.is_face_valid:
                self.cam_live_status.configure(
                    text="● KHUÔN MẶT ĐƯỢC NHẬN DIỆN",
                    text_color=CTK_SUCCESS
                )
            elif getattr(self, 'current_conf', 0.0) > 0:
                self.cam_live_status.configure(
                    text=f"● {self.ai_status_text.upper()}",
                    text_color=CTK_WARNING
                )
            else:
                self.cam_live_status.configure(
                    text="● CHƯA PHÁT HIỆN KHUÔN MẶT",
                    text_color=CTK_DANGER
                )
        
        self.after(ADMIN_CAMERA_FPS_DELAY, self._update_frame)

    def _toggle_camera(self):
        """Tạm dừng / tiếp tục camera."""
        if self.camera_running:
            self.camera_running = False
            self.btn_toggle_cam.configure(text="▶  Tiếp tục")
            self.cam_status_dot.configure(text="● PAUSED", text_color=CTK_WARNING)
            if hasattr(self, 'lbl_stat_status'):
                self.lbl_stat_status.configure(text="● Tạm dừng", text_color=CTK_WARNING)
            if hasattr(self, 'cam_live_status'):
                self.cam_live_status.configure(text="● TẠM DỪNG CAMERA", text_color=CTK_WARNING)
        else:
            self.camera_running = True
            self.btn_toggle_cam.configure(text="⏸  Tạm dừng")
            self.cam_status_dot.configure(text="● LIVE", text_color=CTK_SUCCESS)
            if hasattr(self, 'lbl_stat_status'):
                self.lbl_stat_status.configure(text="● Hoạt động tốt", text_color=CTK_SUCCESS)
            
            if self.camera_thread is None or not self.camera_thread.is_alive():
                self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
                self.camera_thread.start()
                
            self._update_frame()

    def _restart_camera(self):
        """Khởi động lại camera."""
        self.camera_running = False
        if self.camera_cap is not None:
            self.camera_cap.release()
            self.camera_cap = None
        self.camera_label.configure(image=None, text="Đang khởi động lại camera...")
        self.cam_status_dot.configure(text="● RESTARTING", text_color=CTK_WARNING)
        if hasattr(self, 'lbl_stat_status'):
            self.lbl_stat_status.configure(text="● Đang khởi động...", text_color=CTK_WARNING)
        if hasattr(self, 'cam_live_status'):
            self.cam_live_status.configure(text="● ĐANG KẾT NỐI LẠI...", text_color=CTK_WARNING)
        self.after(500, self._start_camera)
