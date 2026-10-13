"""
============================================
CẤU HÌNH CHUNG - AI RECOGNITION SYSTEM
============================================
Tất cả hằng số dùng chung giữa Kiosk Camera
và Admin Panel được tập trung tại đây.
============================================
"""

import os

# Ép TensorFlow giảm log
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'


# ============================================
# CẤU HÌNH CAMERA
# ============================================
# Máy hiện tại gán webcam ngoài cho ID=0. Có thể đổi mà không sửa
# code bằng biến môi trường FACECHECK_CAMERA_INDEX, ví dụ: 1 hoặc 2.
try:
    CAMERA_PREFERRED_ID = int(os.environ.get("FACECHECK_CAMERA_INDEX", "0"))
except ValueError:
    CAMERA_PREFERRED_ID = 0

CAMERA_DEVICE_IDS = tuple(dict.fromkeys((CAMERA_PREFERRED_ID, 0, 1, 2)))
CAMERA_STREAM_URL = os.environ.get("FACECHECK_CAMERA_URL", "").strip()
CAMERA_SOURCES = (
    (CAMERA_STREAM_URL,) + CAMERA_DEVICE_IDS
    if CAMERA_STREAM_URL else CAMERA_DEVICE_IDS
)
CAMERA_PROFILE = os.environ.get(
    "FACECHECK_CAMERA_PROFILE",
    "embedded_ov3660" if CAMERA_STREAM_URL else "webcam",
).strip().lower()
IS_EMBEDDED_CAMERA = CAMERA_PROFILE == "embedded_ov3660"

# Cấu hình đảo gương (Horizontal Flip):
# Đối với webcam laptop tích hợp sẵn thì lật gương (True),
# đối với camera nhúng/ngoài (OV2640/OV3660) thì mặc định tắt lật gương (False).
_default_flip = "0" if IS_EMBEDDED_CAMERA else "1"
CAMERA_HORIZONTAL_FLIP = os.environ.get(
    "FACECHECK_CAMERA_FLIP", _default_flip
).strip().lower() in ("1", "true", "yes")

# Bounding boxes are useful while tuning the detector, but the production kiosk
# keeps the camera image clean and reports only the final recognition result.
KIOSK_DRAW_DEBUG_BOXES = os.environ.get(
    "FACECHECK_DRAW_DEBUG_BOXES", "0"
).strip().lower() in ("1", "true", "yes")


# ============================================
# CẤU HÌNH AI MODELS
# ============================================
FACE_MODEL_PATH = "models/model.pt"
MP_FACE_MODEL_PATH = "models/blaze_face_short_range.tflite"
DEEPFACE_MODEL_NAME = "ArcFace"   # ArcFace: Vector đặc trưng 512 chiều (512-d embedding)
ARCFACE_THRESHOLD = 0.460         # Hiệu chỉnh cho trung bình embedding 2 frame QVGA/q30
ARCFACE_TOP2_MARGIN = 0.120       # Top 1 phải tách biệt rõ với Top 2
ARCFACE_QUERY_AGGREGATION_FRAMES = 2
ARCFACE_QUERY_AGGREGATION_MAX_SECONDS = 1.2
ARCFACE_UNCERTAIN_THRESHOLD = 0.620  # Chỉ để phân loại "chưa chắc chắn", không được ghi điểm danh
ARCFACE_HIGH_CONFIDENCE_THRESHOLD = 0.400  # Nhánh nhanh vẫn phải có 2 frame rất gần mẫu
ARCFACE_HIGH_CONFIDENCE_MARGIN = 0.120     # Và phải tách biệt rõ với người đứng thứ hai
ARCFACE_HIGH_CONFIDENCE_CONFIRMATIONS = 2
ARCFACE_HIGH_CONFIDENCE_MIN_SPAN_SECONDS = 0.25
ARCFACE_STRICT_CONFIRMATIONS = 3  # Kết quả sát ngưỡng cần 3 frame strict liên tiếp
ARCFACE_STRICT_MAX_ATTEMPTS = 8   # Chịu được vài frame mờ/nhiễu từ camera truyền USB/Wi-Fi
ARCFACE_STRICT_WINDOW_SECONDS = 3.0
ARCFACE_STRICT_MIN_SPAN_SECONDS = 0.55  # Không cho các frame trùng/gần nhau xác nhận tức thời
KIOSK_MIN_FACE_WIDTH = 70         # QVGA: khuôn mặt phải đủ điểm ảnh cho ArcFace
FACE_CROP_PADDING_RATIO = 0.10    # Dùng thống nhất khi đăng ký, điểm danh và kiểm thử
KIOSK_PRIMARY_FACE_MIN_AREA_RATIO = 1.25  # Mặt gần phải lớn hơn mặt thứ hai 25%
KIOSK_MAX_ROLL_ANGLE = 20         # Ảnh QVGA cần góc mặt chặt hơn để tránh embedding lệch
KIOSK_MIN_YAW_RATIO = 0.65
KIOSK_MAX_YAW_RATIO = 1.55
KIOSK_MIN_PITCH_RATIO = 0.55
KIOSK_MAX_PITCH_RATIO = 1.90

FACE_CONFIDENCE = 0.45
FACE_DETECTION_IMAGE_SIZE = 320   # Ưu tiên tốc độ; YOLO mặc định 640 chậm và dư cho QVGA

# Ràng buộc khuôn mặt
FACE_MIN_SIZE_RATIO = 0.55    # face_w >= 50% box_w
FACE_MAX_SIZE_RATIO = 0.9    # face_w <= 90% box_w
FACE_MAX_TILT_ANGLE = 10     # Góc nghiêng tối đa (độ)
FACE_MIN_BRIGHTNESS = 55.0   # Độ sáng trung bình tối thiểu của vùng mặt (0-255)
FACE_MIN_SHADOW_LEVEL = 28.0 # Phân vị tối 20%; chống mặt sáng một phía nhưng nửa còn lại quá tối
FACE_MAX_BRIGHTNESS = 225.0  # Chống cháy sáng làm mất chi tiết mắt/mũi/miệng

# Profile đăng ký. OV3660 640x480 có ảnh nén/mềm hơn webcam C270 nên dùng
# ngưỡng riêng; vẫn giữ kiểm tra landmark và vùng miệng để chống ảnh che khuất.
ENROLLMENT_MIN_YOLO_CONFIDENCE = 0.72 if IS_EMBEDDED_CAMERA else 0.88
ENROLLMENT_MIN_MEDIAPIPE_CONFIDENCE = 0.65 if IS_EMBEDDED_CAMERA else 0.88
ENROLLMENT_MIN_YAW_RATIO = 0.65 if IS_EMBEDDED_CAMERA else 0.75
ENROLLMENT_MAX_YAW_RATIO = 1.50 if IS_EMBEDDED_CAMERA else 1.30
ENROLLMENT_MIN_PITCH_RATIO = 0.55 if IS_EMBEDDED_CAMERA else 0.70
ENROLLMENT_MAX_PITCH_RATIO = 1.90 if IS_EMBEDDED_CAMERA else 1.60
ENROLLMENT_MOUTH_MIN_EDGE_DENSITY = 12.0 if IS_EMBEDDED_CAMERA else 25.0
ENROLLMENT_MOUTH_MIN_CR = 132 if IS_EMBEDDED_CAMERA else 145


# ============================================
# CẤU HÌNH eKYC UI - Industrial Kiosk HUD
# ============================================
# Kích thước vùng quét (Rounded Rect)
SHAPE_W_RATIO = 0.52              # Chiều rộng shape (% frame_width)
SHAPE_H_RATIO = 0.75              # Chiều cao shape (% frame_height)
CORNER_RADIUS_RATIO = 0.35        # Bán kính bo góc (% shape_width)

# Nền tối công nghiệp (Dark Overlay)
OVERLAY_COLOR_DARK = (0, 0, 0)    # Đen
OVERLAY_ALPHA_DARK = 0.7          # Độ mờ (0.0 - 1.0)

# Viền ngắm HUD (Corner Brackets)
HUD_CORNER_LENGTH = 45            # Chiều dài 1 cạnh của góc
HUD_CORNER_THICKNESS = 5          # Độ dày nét vẽ

# Bottom Status Bar
STATUS_BAR_HEIGHT = 34
STATUS_FONT_SCALE = 0.48
STATUS_FONT_THICKNESS = 1


# ============================================
# MÀU SẮC BGR (OpenCV)
# ============================================
CV_COLOR_DEFAULT = (75, 195, 140)   # Xanh olive/lime (chưa khóa)
CV_COLOR_RED = (50, 50, 255)        # Lỗi / chưa hợp lệ (Đỏ Cam)
CV_COLOR_GREEN = (100, 255, 100)    # Hợp lệ (Xanh lá dịu)


# ============================================
# PIPELINE TIMING
# ============================================
KIOSK_DETECTION_INTERVAL_SECONDS = 0.08  # YOLO tối đa ~12.5 lần/giây
ENROLLMENT_DETECTION_INTERVAL_SECONDS = 0.10  # AI đăng ký tối đa 10 lần/giây
ENROLLMENT_EMBEDDING_SAMPLES = 5      # Số vector lưu cho mỗi hồ sơ (chỉ lưu 1 ảnh)
ENROLLMENT_MIN_SAMPLES = 3            # Tối thiểu mẫu hợp lệ để cho phép đăng ký
ENROLLMENT_SAMPLE_INTERVAL_SECONDS = 0.20
ENROLLMENT_AUTO_CAPTURE_SECONDS = 2.2 if IS_EMBEDDED_CAMERA else 3.0
ENROLLMENT_VALID_STREAK = 3 if IS_EMBEDDED_CAMERA else 5
ENROLLMENT_INVALID_STREAK = 4 if IS_EMBEDDED_CAMERA else 2
ENROLLMENT_SESSION_IDLE_TIMEOUT_SECONDS = 15.0  # Không có thao tác quản trị thì tự về điểm danh
KIOSK_FACE_STABLE_SECONDS = 0.7     # Khuôn mặt phải ổn định trước khi tự nhận diện
KIOSK_CONFIRMATION_INTERVAL_SECONDS = 0.38  # Lớn hơn chu kỳ 333 ms để chắc chắn là frame mới
KIOSK_RESULT_HOLD_SECONDS = 1.5     # Thời gian hiển thị kết quả trước khi tự mở lượt mới
KIOSK_PROCESSED_FACE_TTL_SECONDS = 3.0  # Tạm bỏ qua vùng mặt vừa xử lý để ưu tiên người kế tiếp
KIOSK_PROCESSED_FACE_ABSENCE_SECONDS = 0.45  # Xóa dấu vết khi khuôn mặt thực sự rời camera
KIOSK_ATTENDANCE_COOLDOWN_SECONDS = 60  # Không ghi trùng cùng nhân viên trong khoảng này
KIOSK_MIN_FACE_CONFIDENCE = 0.75    # Loại vùng mặt yếu trước khi chạy ArcFace
KIOSK_FACE_EDGE_MARGIN_RATIO = 0.04 # Loại khuôn mặt bị cắt sát mép camera
KIOSK_UNKNOWN_CONFIRMATIONS = 2     # Số lần không khớp hợp lệ trước khi báo người lạ




# ============================================
# ĐƯỜNG DẪN LƯU TRỮ
# ============================================
DATA_FACES_DIR = "data/faces"
