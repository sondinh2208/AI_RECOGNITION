"""
==============================================================
FACECHECK MIXINS PACKAGE
Đóng gói các Mixin module chức năng cho AdminPanel:
- common: Các hàm dùng chung (Thread-safe _safe_after)
- ui_helpers: Vẽ avatar tròn, vector avatar mặc định
- core: Vòng đời ứng dụng, khởi tạo biến chung, AI models, embeddings cache
- navigation: Sidebar, Header Bar, clock, chuyển trang
- attendance: Toàn bộ Kiosk Mode nhận diện điểm danh ArcFace
- enrollment: Đăng ký nhân viên mới, chụp ảnh, lưu khuôn mặt
- camera: Kết nối camera OpenCV, luồng quét khuôn mặt enrollment
- database: Quản lý danh sách người đăng ký, tìm kiếm, hiển thị, xóa
- history: Toàn bộ lịch sử điểm danh nhân sự
- dashboard: Bảng điều khiển, thống kê tổng quan
==============================================================
"""

from .common import CommonMixin
from .ui_helpers import make_circular_avatar, create_default_avatar
from .core import CoreMixin
from .navigation import NavigationMixin
from .attendance import AttendanceMixin
from .enrollment import EnrollmentMixin
from .camera import CameraMixin
from .database import DatabaseMixin
from .history import HistoryMixin
from .dashboard import DashboardMixin

__all__ = [
    "CommonMixin",
    "make_circular_avatar",
    "create_default_avatar",
    "CoreMixin",
    "NavigationMixin",
    "AttendanceMixin",
    "EnrollmentMixin",
    "CameraMixin",
    "DatabaseMixin",
    "HistoryMixin",
    "DashboardMixin",
]
