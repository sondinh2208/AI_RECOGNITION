"""
==============================================================
HỆ THỐNG QUẢN TRỊ AI - Admin Panel
Entry point kết hợp các Mixin modules cho FaceCheck
==============================================================
Chức năng:
  - Dashboard tổng quan (DashboardMixin)
  - Quét khuôn mặt / Thêm nhân viên mới (EnrollmentMixin)
  - Nhận diện điểm danh Kiosk Mode (AttendanceMixin)
  - Quản lý Database người đăng ký (DatabaseMixin)
  - Lịch sử ra vào (HistoryMixin)
  - Camera Pipeline & OpenCV non-blocking (CameraMixin)
  - Navigation Sidebar & Top Header Bar (NavigationMixin)
  - Core Lifecyle & AI Models (CoreMixin)

Kiến trúc: Modular Mixin-based Architecture
Tích hợp: AI Engine + eKYC Renderer (shared modules)
==============================================================
"""

import sys
import customtkinter as ctk

# Đảm bảo UTF-8 encoding trên Windows Terminal để DeepFace/Logging không bị lỗi charmap
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ============================================
# CustomTkinter Theme
# ============================================
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ============================================
# Import các Mixin Modules
# ============================================
from mixins import (
    CoreMixin,
    CommonMixin,
    CameraMixin,
    NavigationMixin,
    AttendanceMixin,
    EnrollmentMixin,
    DatabaseMixin,
    HistoryMixin,
    DashboardMixin,
)


# ============================================
# LỚP CHÍNH: AdminPanel
# ============================================
class AdminPanel(
    CoreMixin,
    CommonMixin,
    CameraMixin,
    NavigationMixin,
    AttendanceMixin,
    EnrollmentMixin,
    DatabaseMixin,
    HistoryMixin,
    DashboardMixin,
    ctk.CTk,
):
    """
    Cửa sổ chính của hệ thống Quản trị AI (FaceCheck).
    Kế thừa và kết hợp các Mixin module chức năng:
      - CoreMixin: Quản lý vòng đời ứng dụng, nạp AI models, embeddings cache, closing.
      - CommonMixin: Các hàm bổ trợ dùng chung (_safe_after).
      - CameraMixin: Quản lý kết nối camera OpenCV, luồng quét khuôn mặt enrollment, hiển thị frame.
      - NavigationMixin: Sidebar điều hướng, Top Header Bar, clock, quản lý chuyển trang.
      - AttendanceMixin: Toàn bộ Kiosk Mode nhận diện điểm danh, ArcFace AI pipeline, result card.
      - EnrollmentMixin: Đăng ký nhân viên mới, 1-click capture, trích xuất và lưu vector.
      - DatabaseMixin: Quản lý danh sách nhân viên đã đăng ký, tìm kiếm, hiển thị, xóa.
      - HistoryMixin: Bảng lịch sử điểm danh đầy đủ.
      - DashboardMixin: Thống kê số lượng, tổng quan hệ thống.
    """
    pass


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    print("=" * 50)
    print("  ADMIN PANEL - HỆ THỐNG QUẢN TRỊ AI")
    print("  CustomTkinter + OpenCV + AI Pipeline")
    print("=" * 50)
    print()
    
    app = AdminPanel()
    app.mainloop()
