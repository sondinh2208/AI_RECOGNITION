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

FACE_CONFIDENCE = 0.45
PERSON_CONFIDENCE = 0.5
PERSON_CLASS_ID = 0

# Ràng buộc khuôn mặt
FACE_MIN_SIZE_RATIO = 0.55    # face_w >= 50% box_w
FACE_MAX_SIZE_RATIO = 0.9    # face_w <= 90% box_w
FACE_MAX_TILT_ANGLE = 10     # Góc nghiêng tối đa (độ)


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
# CẤU HÌNH GIAO DIỆN ADMIN PANEL (CustomTkinter)
# ============================================
CTK_BG_DARK = ("#ffffff", "#0b111e")        # Sidebar background
CTK_BG_MAIN = ("#f8fafc", "#060b13")        # Main window background
CTK_ACCENT = ("#e2e8f0", "#1e293b")         # Borders & dividers
CTK_PRIMARY = ("#2563eb", "#38bdf8")        # Brand / Highlight blue
CTK_SUCCESS = ("#16a34a", "#22c55e")        # Success green
CTK_DANGER = ("#ef4444", "#f87171")         # Danger red
CTK_WARNING = ("#f59e0b", "#fbbf24")        # Warning yellow / amber
CTK_TEXT = ("#0f172a", "#f8fafc")           # Text primary (Dark slate in light / Crisp white in dark)
CTK_TEXT_DIM = ("#64748b", "#94a3b8")       # Text secondary (Muted slate)
CTK_CARD = ("#ffffff", "#0d1522")           # Card surface (White in light / Dark navy in dark)
CTK_SIDEBAR_HOVER = ("#f1f5f9", "#172033")  # Hover on sidebar items
CTK_BTN_ACTIVE = ("#2563eb", "#2563eb")     # Active nav pill



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
ADMIN_CAMERA_FPS_DELAY = 15    # ms giữa mỗi frame




# ============================================
# ĐƯỜNG DẪN LƯU TRỮ
# ============================================
DATA_FACES_DIR = "data/faces"
