"""
============================================
AI ENGINE - Nhận diện khuôn mặt
============================================
Module chứa toàn bộ logic AI:
  - Kiểm tra GPU / CUDA
  - Load models (YOLOv8-Face, YOLOv8n Person, MediaPipe)
  - Detect faces / persons
  - Kiểm tra ràng buộc (vị trí, kích thước, góc nghiêng)
============================================
"""

import math
import numpy as np
import cv2
import torch
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from pathlib import Path
from ultralytics import YOLO

from config import (
    FACE_MODEL_PATH, MP_FACE_MODEL_PATH,
    FACE_CONFIDENCE, PERSON_CONFIDENCE, PERSON_CLASS_ID,
    FACE_MIN_SIZE_RATIO, FACE_MAX_SIZE_RATIO, FACE_MAX_TILT_ANGLE,
    KIOSK_MIN_FACE_CONFIDENCE, KIOSK_FACE_EDGE_MARGIN_RATIO,
    KIOSK_MAX_ROLL_ANGLE, KIOSK_MIN_YAW_RATIO, KIOSK_MAX_YAW_RATIO,
    KIOSK_MIN_PITCH_RATIO, KIOSK_MAX_PITCH_RATIO,
    CV_COLOR_DEFAULT, CV_COLOR_RED, CV_COLOR_GREEN,
)


# ============================================
# GPU / CUDA
# ============================================
def check_gpu():
    """
    Kiểm tra và hiển thị thông tin GPU/CUDA.
    Returns:
        device_str: '0' nếu có GPU, 'cpu' nếu không
    """
    print("\n" + "=" * 50)
    print("  KIỂM TRA GPU / CUDA")
    print("=" * 50)
    
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"✅ PyTorch CUDA: SẴN SÀNG")
        print(f"🔌 GPU: {gpu_name}")
        print(f"💾 VRAM: {gpu_memory:.1f} GB")
        print(f"🔧 CUDA Version: {torch.version.cuda}")
        print(f"📦 PyTorch Version: {torch.__version__}")
        device = '0'
    else:
        print("⚠️  PyTorch CUDA: KHÔNG KHẢ DỤNG")
        print("    -> Sẽ fallback về CPU (chậm hơn đáng kể)")
        device = 'cpu'
    
    # TensorFlow GPU check (cho DeepFace)
    try:
        import tensorflow as tf
        gpus = tf.config.list_physical_devices('GPU')
        if gpus:
            print(f"✅ TensorFlow GPU: SẴN SÀNG ({len(gpus)} GPU)")
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
            print("   -> Đã bật memory growth (tối ưu VRAM)")
        else:
            print("⚠️  TensorFlow GPU: KHÔNG KHẢ DỤNG")
    except ImportError:
        print("ℹ️  TensorFlow: Chưa cài đặt (sẽ cần cho DeepFace)")
    except Exception as e:
        print(f"⚠️  TensorFlow GPU check lỗi: {e}")
    
    print("=" * 50 + "\n")
    return device


def check_gpu_quick():
    """
    Kiểm tra GPU nhanh gọn (cho Admin Panel, không cần log dài).
    Returns:
        device_str: '0' nếu có GPU, 'cpu' nếu không
    """
    if torch.cuda.is_available():
        print(f"[AI] GPU: {torch.cuda.get_device_name(0)}")
        return '0'
    else:
        print("[AI] GPU không khả dụng, dùng CPU")
        return 'cpu'


# ============================================
# LOAD MODELS
# ============================================
def load_face_model(device='0'):
    """
    Load YOLOv8-Face model.
    Returns:
        (face_model, device) hoặc (None, device) nếu không tìm thấy model
    """
    model_path = Path(FACE_MODEL_PATH)
    
    if not model_path.exists():
        print(f"[AI] WARN: Không tìm thấy model tại: {model_path.absolute()}")
        print("[AI] Hãy tải model từ HuggingFace:")
        print("     python -c \"from huggingface_hub import hf_hub_download; "
              "hf_hub_download(repo_id='arnabdhar/YOLOv8-Face-Detection', "
              "filename='model.pt', local_dir='models')\"")
        return None, device
    
    face_model = YOLO(str(model_path))
    target = 'GPU' if device == '0' and torch.cuda.is_available() else 'CPU'
    print(f"[AI] YOLOv8-Face loaded → {target}")
    return face_model, device


def load_person_model(device='0'):
    """
    Load YOLOv8n (COCO) cho detect person.
    Returns:
        (person_model, device) hoặc (None, device) nếu lỗi
    """
    try:
        person_model = YOLO("yolov8n.pt")
        target = 'GPU' if device == '0' and torch.cuda.is_available() else 'CPU'
        print(f"[AI] YOLOv8n Person loaded → {target}")
        return person_model, device
    except Exception as e:
        print(f"[AI] WARN: Không load được person model: {e}")
        return None, device


def load_mediapipe_detector():
    """
    Khởi tạo MediaPipe Face Detection (Tasks API) cho tính góc nghiêng.
    Returns:
        face_detector hoặc None nếu lỗi
    """
    try:
        mp_model_path = MP_FACE_MODEL_PATH
        if not Path(mp_model_path).exists():
            print(f"[AI] WARN: Không tìm thấy {mp_model_path}")
            return None
        
        base_options = mp_python.BaseOptions(model_asset_path=mp_model_path)
        options = mp_vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=0.5,
        )
        detector = mp_vision.FaceDetector.create_from_options(options)
        print("[AI] MediaPipe Face Detector loaded")
        return detector
    except Exception as e:
        print(f"[AI] WARN: MediaPipe init lỗi: {e}")
        return None


# ============================================
# DETECTION FUNCTIONS
# ============================================
def detect_faces(face_model, frame, device='0'):
    """
    Detect khuôn mặt bằng YOLOv8-Face model.
    Returns:
        faces: [(x1, y1, x2, y2, confidence), ...]
    """
    if face_model is None:
        return []
    
    results = face_model(frame, verbose=False, conf=FACE_CONFIDENCE, device=device)
    
    faces = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
            confidence = float(box.conf[0])
            faces.append((x1, y1, x2, y2, confidence))
    
    return faces


def detect_persons(person_model, frame, device='0'):
    """
    Detect "person" bằng YOLOv8n (COCO).
    Returns:
        persons: [(x1, y1, x2, y2, confidence), ...]
    """
    if person_model is None:
        return []
    
    results = person_model(frame, verbose=False, conf=PERSON_CONFIDENCE, device=device)
    
    persons = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            if int(box.cls[0]) != PERSON_CLASS_ID:
                continue
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
            confidence = float(box.conf[0])
            persons.append((x1, y1, x2, y2, confidence))
    
    return persons


# ============================================
# CONSTRAINT CHECKING
# ============================================
def is_fully_inside(face_box, constraint_box):
    """
    Kiểm tra khuôn mặt nằm gọn 100% bên trong khung định vị.
    """
    fx1, fy1, fx2, fy2 = face_box
    cx1, cy1, cx2, cy2 = constraint_box
    return (fx1 >= cx1 and fy1 >= cy1 and fx2 <= cx2 and fy2 <= cy2)


def analyze_face_quality(
    face_box,
    mp_results,
    frame_width,
    frame_height,
    max_tilt_angle=None,
    yaw_ratio_range=None,
    pitch_ratio_range=None,
):
    """
    Phân tích chất lượng khuôn mặt: ngẩng/cúi (pitch), quay ngang (yaw), nghiêng (roll).
    Returns:
        (is_valid, error_msg)
    """
    max_tilt_angle = (
        FACE_MAX_TILT_ANGLE if max_tilt_angle is None else max_tilt_angle
    )
    yaw_min, yaw_max = yaw_ratio_range or (0.75, 1.30)
    pitch_min, pitch_max = pitch_ratio_range or (0.70, 1.60)

    if not mp_results or not mp_results.detections:
        return False, "Khuon mat chua ro rang"
    
    fx1, fy1, fx2, fy2 = face_box
    face_w = fx2 - fx1
    yolo_cx = (fx1 + fx2) / 2.0
    yolo_cy = (fy1 + fy2) / 2.0
    
    # Tìm khuôn mặt MediaPipe tương ứng với YOLO box
    best_detection = None
    min_dist = float('inf')
    
    for detection in mp_results.detections:
        bbox = detection.bounding_box
        mx = bbox.origin_x + bbox.width / 2.0
        my = bbox.origin_y + bbox.height / 2.0
        dist = math.hypot(mx - yolo_cx, my - yolo_cy)
        if dist < min_dist:
            min_dist = dist
            best_detection = detection
    
    if not best_detection or min_dist >= max(face_w, fy2 - fy1):
        return False, "Khuon mat chua ro rang"
    
    if len(best_detection.keypoints) < 4:
        return False, "Khuon mat chua ro rang"
        
    right_eye = best_detection.keypoints[0]
    left_eye = best_detection.keypoints[1]
    nose = best_detection.keypoints[2]
    mouth = best_detection.keypoints[3]
    
    if right_eye.x < left_eye.x:
        pt_left, pt_right = right_eye, left_eye
    else:
        pt_left, pt_right = left_eye, right_eye
        
    # 1. TILT / ROLL (Nghiêng đầu)
    dx = (pt_right.x - pt_left.x) * frame_width
    dy = (pt_right.y - pt_left.y) * frame_height
    if dx != 0:
        angle_roll = math.degrees(math.atan2(dy, dx))
        if abs(angle_roll) > max_tilt_angle:
            return False, "Giu thang mat voi khung hinh"
            
    # 2. YAW (Quay ngang)
    dist_nose_right = math.hypot(nose.x - pt_right.x, nose.y - pt_right.y)
    dist_nose_left = math.hypot(nose.x - pt_left.x, nose.y - pt_left.y)
    if dist_nose_right == 0 or dist_nose_left == 0:
        return False, "Khuon mat chua ro rang"
    
    yaw_ratio = dist_nose_left / dist_nose_right
    if yaw_ratio > yaw_max or yaw_ratio < yaw_min:
        return False, "Vui long nhin thang vao camera"
        
    # 3. PITCH (Ngẩng / cúi)
    eye_cy = (pt_left.y + pt_right.y) / 2.0
    dist_eye_nose = nose.y - eye_cy
    dist_nose_mouth = mouth.y - nose.y
    if dist_nose_mouth <= 0 or dist_eye_nose <= 0:
        return False, "Khuon mat chua ro rang"
        
    pitch_ratio = dist_eye_nose / dist_nose_mouth
    if pitch_ratio > pitch_max or pitch_ratio < pitch_min:
        return False, "Vui long dieu chinh goc ngang cui"
        
    return True, ""
    
def check_face_constraints(faces, constraint_box, mp_results, frame_width, frame_height):
    """
    Kiểm tra tất cả ràng buộc cho từng khuôn mặt.
    Returns:
        (color, status_text, is_locked): trạng thái UI
    """
    color = CV_COLOR_DEFAULT
    status_text = "Dua mat vao khung hinh"
    is_locked = False
    
    if constraint_box is None:
        return color, status_text, is_locked
    
    cx1, cy1, cx2, cy2 = constraint_box
    box_w = cx2 - cx1
    
    for (fx1, fy1, fx2, fy2, _fconf) in faces:
        # 1. Kiểm tra vị trí
        if not is_fully_inside((fx1, fy1, fx2, fy2), constraint_box):
            continue
        
        # 2. Ràng buộc kích thước (khoảng cách)
        face_w = fx2 - fx1
        
        if face_w < FACE_MIN_SIZE_RATIO * box_w:
            color = CV_COLOR_RED
            status_text = "Di chuyen lai gan hon"
            continue
        elif face_w > FACE_MAX_SIZE_RATIO * box_w:
            color = CV_COLOR_RED
            status_text = "Vui long lui lai"
            continue
        
        # 3. Ràng buộc góc nghiêng, quay, ngẩng và độ rõ nét khuôn mặt
        is_valid_pose, pose_error = analyze_face_quality(
            (fx1, fy1, fx2, fy2), mp_results, frame_width, frame_height
        )
        
        if not is_valid_pose:
            color = CV_COLOR_RED
            status_text = pose_error
            continue
        
        # TẤT CẢ 3 ĐIỀU KIỆN THỎA MÃN → KHÓA MỤC TIÊU
        color = CV_COLOR_GREEN
        status_text = "Goc mat hop le - San sang luu"
        is_locked = True
        break
    
    return color, status_text, is_locked


# ============================================
# KIOSK FACE RECOGNITION HELPERS
# ============================================
def calculate_cosine_distance(vec1, vec2):
    """
    Tính khoảng cách Cosine giữa 2 vector đặc trưng (ArcFace/DeepFace).
    Metric Cosine: distance = 1 - (A . B) / (||A|| * ||B||)
    Returns:
        float: 0.0 (giống hệt) đến 2.0 (hoàn toàn khác).
    """
    v1 = np.asarray(vec1, dtype=np.float32).flatten()
    v2 = np.asarray(vec2, dtype=np.float32).flatten()
    if v1.shape[0] != v2.shape[0] or v1.shape[0] == 0:
        return 1.0
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 1.0
    cos_sim = float(np.dot(v1, v2) / (norm1 * norm2))
    cos_sim = max(-1.0, min(1.0, cos_sim))
    return float(1.0 - cos_sim)


def validate_kiosk_face_candidate(face_box, confidence, frame, face_detector):
    """Kiểm tra khuôn mặt đủ đầy và rõ trước khi chuyển sang ArcFace."""
    if frame is None or frame.size == 0:
        return False, "Không đọc được hình ảnh camera"

    frame_h, frame_w = frame.shape[:2]
    fx1, fy1, fx2, fy2 = face_box
    face_w = fx2 - fx1
    face_h = fy2 - fy1

    if confidence < KIOSK_MIN_FACE_CONFIDENCE:
        return False, "Vui lòng nhìn rõ và hướng mặt vào camera"

    margin_x = max(8, int(frame_w * KIOSK_FACE_EDGE_MARGIN_RATIO))
    margin_y = max(8, int(frame_h * KIOSK_FACE_EDGE_MARGIN_RATIO))
    if fx1 <= margin_x or fy1 <= margin_y or fx2 >= frame_w - margin_x or fy2 >= frame_h - margin_y:
        return False, "Vui lòng đưa toàn bộ khuôn mặt vào khung"

    if face_h <= 0:
        return False, "Khuôn mặt chưa rõ ràng"
    aspect_ratio = face_w / face_h
    if aspect_ratio < 0.55 or aspect_ratio > 1.30:
        return False, "Vui lòng đưa toàn bộ khuôn mặt vào khung"

    if face_detector is None:
        return False, "Bộ kiểm tra khuôn mặt chưa sẵn sàng"

    try:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        mp_results = face_detector.detect(mp_img)
        is_valid, error = analyze_face_quality(
            face_box,
            mp_results,
            frame_w,
            frame_h,
            max_tilt_angle=KIOSK_MAX_ROLL_ANGLE,
            yaw_ratio_range=(KIOSK_MIN_YAW_RATIO, KIOSK_MAX_YAW_RATIO),
            pitch_ratio_range=(KIOSK_MIN_PITCH_RATIO, KIOSK_MAX_PITCH_RATIO),
        )
        if not is_valid:
            # YOLO đã xác nhận đây là khuôn mặt rõ với confidence đủ cao.
            # MediaPipe đôi khi mất landmark ở góc nghiêng; không nên vì vậy mà
            # chặn ArcFace trước khi mô hình có cơ hội đối chiếu danh tính.
            if error == "Khuon mat chua ro rang":
                print("[KIOSK QUALITY] Không đủ landmark; chuyển sang ArcFace dự phòng")
                return True, ""
            message_map = {
                "Giu thang mat voi khung hinh": "Góc nghiêng quá lớn, vui lòng nghiêng lại một chút",
                "Vui long nhin thang vao camera": "Góc quay quá lớn, vui lòng quay nhẹ về camera",
                "Vui long dieu chinh goc ngang cui": "Góc ngẩng/cúi quá lớn, vui lòng điều chỉnh nhẹ",
            }
            return False, message_map.get(error, "Vui lòng căn chỉnh lại khuôn mặt")
        return True, ""
    except Exception as exc:
        print(f"[KIOSK QUALITY] Không thể kiểm tra khuôn mặt: {exc}")
        return False, "Vui lòng đưa toàn bộ khuôn mặt vào khung"


def draw_kiosk_face_box(frame, x1, y1, x2, y2, color=(100, 255, 100), thickness=3, corner_ratio=0.25):
    """
    Vẽ khung bounding box hiện đại dạng Corner Brackets [ ] bám theo khuôn mặt,
    phù hợp giao diện Kiosk Điểm danh công nghệ cao (không dùng mask tối).
    """
    w = x2 - x1
    h = y2 - y1
    corner_len = max(18, int(min(w, h) * corner_ratio))
    
    # Top-Left
    cv2.line(frame, (x1, y1), (x1 + corner_len, y1), color, thickness)
    cv2.line(frame, (x1, y1), (x1, y1 + corner_len), color, thickness)
    
    # Top-Right
    cv2.line(frame, (x2, y1), (x2 - corner_len, y1), color, thickness)
    cv2.line(frame, (x2, y1), (x2, y1 + corner_len), color, thickness)
    
    # Bottom-Left
    cv2.line(frame, (x1, y2), (x1 + corner_len, y2), color, thickness)
    cv2.line(frame, (x1, y2), (x1, y2 - corner_len), color, thickness)
    
    # Bottom-Right
    cv2.line(frame, (x2, y2), (x2 - corner_len, y2), color, thickness)
    cv2.line(frame, (x2, y2), (x2, y2 - corner_len), color, thickness)


def align_face_crop(face_crop, face_detector):
    """
    Tự động căn chỉnh xoay thẳng mặt (Face Alignment) dựa vào 2 mắt:
    - Sử dụng MediaPipe Face Detector để tìm tọa độ 2 mắt trong ảnh crop (< 1.5ms).
    - Nếu phát hiện góc nghiêng (Roll angle), xoay ảnh sao cho 2 mắt nằm ngang.
    - Giúp ArcFace trích xuất vector chuẩn xác như khi nhìn thẳng (giảm Cosine Distance khi nghiêng đầu từ 0.86 xuống ~0.10).
    """
    if face_detector is None or face_crop is None or face_crop.size == 0:
        return face_crop, 0.0
        
    try:
        h, w = face_crop.shape[:2]
        rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = face_detector.detect(mp_img)
        
        if not res.detections or len(res.detections[0].keypoints) < 2:
            return face_crop, 0.0
            
        kp0 = res.detections[0].keypoints[0] # right eye
        kp1 = res.detections[0].keypoints[1] # left eye
        
        # Sắp xếp mắt trái và mắt phải theo trục X
        if kp0.x < kp1.x:
            pt_l, pt_r = kp0, kp1
        else:
            pt_l, pt_r = kp1, kp0
            
        lx, ly = pt_l.x * w, pt_l.y * h
        rx, ry = pt_r.x * w, pt_r.y * h
        
        dx = rx - lx
        dy = ry - ly
        
        if dx == 0:
            return face_crop, 0.0
            
        angle = math.degrees(math.atan2(dy, dx))
        
        # Chỉ xoay nếu góc nghiêng đáng kể (> 2.0 độ)
        if abs(angle) > 2.0:
            eye_center = (float((lx + rx) / 2.0), float((ly + ry) / 2.0))
            M = cv2.getRotationMatrix2D(eye_center, angle, 1.0)
            aligned = cv2.warpAffine(face_crop, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            return aligned, angle
        else:
            return face_crop, angle
            
    except Exception as e:
        print(f"[ALIGN ERROR]: {e}")
        return face_crop, 0.0
