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
# CẤU HÌNH AI MODELS
# ============================================
FACE_MODEL_PATH = "models/model.pt"
MP_FACE_MODEL_PATH = "models/blaze_face_short_range.tflite"
DEEPFACE_MODEL_NAME = "ArcFace"   # ArcFace: Vector đặc trưng 512 chiều (512-d embedding)
ARCFACE_THRESHOLD = 0.480         # Ngưỡng strict ưu tiên chống chấp nhận người lạ
ARCFACE_TOP2_MARGIN = 0.075       # Top 1 phải tách biệt đủ rõ với Top 2
ARCFACE_UNCERTAIN_THRESHOLD = 0.620  # Chỉ để phân loại "chưa chắc chắn", không được ghi điểm danh
ARCFACE_STRICT_CONFIRMATIONS = 3  # Chấp nhận sớm khi có 3 frame strict liên tiếp cùng ID
ARCFACE_STRICT_MAX_ATTEMPTS = 5
ARCFACE_STRICT_WINDOW_SECONDS = 2.0
KIOSK_MIN_FACE_WIDTH = 100        # Kích thước mặt tối thiểu (px) để kích hoạt nhận diện
FACE_CROP_PADDING_RATIO = 0.10    # Dùng thống nhất khi đăng ký, điểm danh và kiểm thử
KIOSK_PRIMARY_FACE_MIN_AREA_RATIO = 1.25  # Mặt gần phải lớn hơn mặt thứ hai 25%
KIOSK_PRIMARY_FACE_LEAVE_AREA_RATIO = 0.45
KIOSK_MAX_ROLL_ANGLE = 30         # Cho phép nghiêng đầu; ArcFace sẽ nhận ảnh đã căn thẳng
KIOSK_MIN_YAW_RATIO = 0.55        # Cho phép quay trái/phải vừa phải khi điểm danh
KIOSK_MAX_YAW_RATIO = 1.80
KIOSK_MIN_PITCH_RATIO = 0.45      # Cho phép ngẩng/cúi vừa phải khi điểm danh
KIOSK_MAX_PITCH_RATIO = 2.20

FACE_CONFIDENCE = 0.45
PERSON_CONFIDENCE = 0.5
PERSON_CLASS_ID = 0

# Ràng buộc khuôn mặt
FACE_MIN_SIZE_RATIO = 0.55    # face_w >= 50% box_w
FACE_MAX_SIZE_RATIO = 0.9    # face_w <= 90% box_w
FACE_MAX_TILT_ANGLE = 10     # Góc nghiêng tối đa (độ)
FACE_MIN_BRIGHTNESS = 55.0   # Độ sáng trung bình tối thiểu của vùng mặt (0-255)
FACE_MIN_SHADOW_LEVEL = 28.0 # Phân vị tối 20%; chống mặt sáng một phía nhưng nửa còn lại quá tối
FACE_MAX_BRIGHTNESS = 225.0  # Chống cháy sáng làm mất chi tiết mắt/mũi/miệng


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
STATUS_BAR_HEIGHT = 45
STATUS_FONT_SCALE = 0.65
STATUS_FONT_THICKNESS = 2


# ============================================
# MÀU SẮC BGR (OpenCV)
# ============================================
CV_COLOR_DEFAULT = (75, 195, 140)   # Xanh olive/lime (chưa khóa)
CV_COLOR_RED = (50, 50, 255)        # Lỗi / chưa hợp lệ (Đỏ Cam)
CV_COLOR_GREEN = (100, 255, 100)    # Hợp lệ (Xanh lá dịu)
CV_COLOR_WHITE = (255, 255, 255)    # Trắng
CV_COLOR_CYAN = (255, 255, 0)      # Bounding box (nếu cần)


# ============================================
# CẤU HÌNH GIAO DIỆN ADMIN PANEL (Enterprise HR / Attendance Palette)
# ============================================
CTK_BG_MAIN = ("#F6F7F9", "#0F172A")        # Main background: Clean light gray / Dark slate
CTK_BG_DARK = ("#FFFFFF", "#1E293B")        # Sidebar background: Clean white / Dark slate surface
CTK_CARD = ("#FFFFFF", "#1E293B")           # Card/Surface: Clean white / Dark slate surface
CTK_ACCENT = ("#E5E7EB", "#334155")         # Borders & dividers: Soft gray #E5E7EB / Slate border #334155
CTK_PRIMARY = ("#2563EB", "#3B82F6")        # Primary Blue
CTK_PRIMARY_HOVER = ("#1D4ED8", "#2563EB")  # Primary Blue Hover
CTK_SUCCESS = ("#16A34A", "#22C55E")        # Success Green
CTK_DANGER = ("#DC2626", "#EF4444")         # Danger Red
CTK_WARNING = ("#D97706", "#F59E0B")        # Warning Amber
CTK_TEXT = ("#1F2937", "#F8FAFC")           # Text primary: Dark slate #1F2937 / Off-white #F8FAFC
CTK_TEXT_DIM = ("#6B7280", "#94A3B8")       # Text secondary: Slate-500 #6B7280 / Slate-400 #94A3B8
CTK_SIDEBAR_HOVER = ("#F3F4F6", "#334155")  # Sidebar item hover
CTK_BTN_ACTIVE = ("#0284C7", "#00A3E0")     # Active nav item background: Sky blue / Bright cyan
CTK_BTN_ACTIVE_TEXT = ("#FFFFFF", "#FFFFFF")# Active nav item text: Pure white




# ============================================
# CẤU HÌNH KIOSK CAMERA WINDOW
# ============================================
KIOSK_WINDOW_NAME = "KIOSK - Nhan dien khuon mat"


# ============================================
# CẤU HÌNH ADMIN PANEL WINDOW
# ============================================
ADMIN_WINDOW_WIDTH = 1320
ADMIN_WINDOW_HEIGHT = 780
ADMIN_SIDEBAR_WIDTH = 250
ADMIN_CAMERA_WIDTH = 520
ADMIN_CAMERA_HEIGHT = 350
ADMIN_CAMERA_FPS_DELAY = 25    # UI camera ~30-40 FPS: hiển thị mượt mà thời gian thực
KIOSK_DETECTION_INTERVAL_SECONDS = 0.08  # YOLO tối đa ~12.5 lần/giây
ENROLLMENT_DETECTION_INTERVAL_SECONDS = 0.10  # AI đăng ký tối đa 10 lần/giây
ENROLLMENT_EMBEDDING_SAMPLES = 5      # Số vector lưu cho mỗi hồ sơ (chỉ lưu 1 ảnh)
ENROLLMENT_MIN_SAMPLES = 3            # Tối thiểu mẫu hợp lệ để cho phép đăng ký
ENROLLMENT_SAMPLE_INTERVAL_SECONDS = 0.20
ENROLLMENT_AUTO_CAPTURE_SECONDS = 3.0 # Giữ mặt hợp lệ liên tục trước khi tự chụp
KIOSK_FACE_STABLE_SECONDS = 0.7     # Khuôn mặt phải ổn định trước khi tự nhận diện
KIOSK_CONFIRMATION_INTERVAL_SECONDS = 0.25  # Nhịp lấy frame xác minh sau lần đầu
KIOSK_FACE_LEAVE_SECONDS = 1.5      # Thời gian rời khung để mở lượt tiếp theo
KIOSK_ATTENDANCE_COOLDOWN_SECONDS = 60  # Không ghi trùng cùng nhân viên trong khoảng này
KIOSK_MIN_FACE_CONFIDENCE = 0.70    # Độ tin cậy YOLO tối thiểu trước khi chạy ArcFace
KIOSK_FACE_EDGE_MARGIN_RATIO = 0.04 # Loại khuôn mặt bị cắt sát mép camera
KIOSK_UNKNOWN_CONFIRMATIONS = 2     # Số lần không khớp hợp lệ trước khi báo người lạ




# ============================================
# ĐƯỜNG DẪN LƯU TRỮ
# ============================================
DATA_FACES_DIR = "data/faces"
