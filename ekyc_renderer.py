"""
============================================
eKYC RENDERER - Vẽ giao diện HUD Camera
============================================
Module chứa toàn bộ hàm render UI lên frame OpenCV:
  - Tính layout vùng quét
  - Tạo mask rounded rectangle
  - Dark overlay masking
  - HUD Corner Brackets
  - Bottom Status Bar
============================================
"""

import cv2
import numpy as np

from config import (
    SHAPE_W_RATIO, SHAPE_H_RATIO, CORNER_RADIUS_RATIO,
    OVERLAY_COLOR_DARK, OVERLAY_ALPHA_DARK,
    HUD_CORNER_LENGTH, HUD_CORNER_THICKNESS,
    STATUS_BAR_HEIGHT, STATUS_FONT_SCALE, STATUS_FONT_THICKNESS,
)


# ============================================
# LAYOUT
# ============================================
def compute_ekyc_layout(frame_width, frame_height):
    """
    Tính toán layout Kiosk: vùng quét hình chữ nhật bo góc trung tâm.
    
    Returns:
        shape_rect: (x1, y1, x2, y2) bounding rect
        corner_radius: bán kính bo góc
    """
    shape_w = int(frame_width * SHAPE_W_RATIO)
    shape_h = int(frame_height * SHAPE_H_RATIO)
    corner_r = int(shape_w * CORNER_RADIUS_RATIO)
    
    cx = frame_width // 2
    cy = frame_height // 2
    
    x1 = cx - shape_w // 2
    y1 = cy - shape_h // 2
    x2 = cx + shape_w // 2
    y2 = cy + shape_h // 2
    
    return (x1, y1, x2, y2), corner_r


# ============================================
# MASK
# ============================================
def create_rounded_rect_mask(frame_width, frame_height, shape_rect, corner_radius):
    """
    Tạo mask nhị phân hình chữ nhật bo góc (Rounded Rectangle).
    Vùng shape = trắng (255), vùng ngoài = đen (0).
    
    Kỹ thuật: 2 hình chữ nhật chéo + 4 hình tròn góc.
    """
    x1, y1, x2, y2 = shape_rect
    r = corner_radius
    
    mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
    
    # Thân chính: 2 rect chéo nhau
    cv2.rectangle(mask, (x1 + r, y1), (x2 - r, y2), 255, -1)
    cv2.rectangle(mask, (x1, y1 + r), (x2, y2 - r), 255, -1)
    
    # 4 góc bo tròn
    cv2.circle(mask, (x1 + r, y1 + r), r, 255, -1)  # Trên-trái
    cv2.circle(mask, (x2 - r, y1 + r), r, 255, -1)  # Trên-phải
    cv2.circle(mask, (x1 + r, y2 - r), r, 255, -1)  # Dưới-trái
    cv2.circle(mask, (x2 - r, y2 - r), r, 255, -1)  # Dưới-phải
    
    return mask


# ============================================
# RENDER
# ============================================
def render_ekyc_frame(frame, mask):
    """
    Áp dụng Image Masking phong cách Kiosk Công nghiệp:
      - Lớp phủ tối bán trong suốt (Dark Translucent Overlay)
      - Đục lỗ rounded rect để lộ camera thật sáng rõ
    """
    # 1. Tạo overlay đen
    overlay_bg = np.full_like(frame, OVERLAY_COLOR_DARK, dtype=np.uint8)
    # 2. Blend overlay với frame (bán trong suốt)
    blended_bg = cv2.addWeighted(
        overlay_bg, OVERLAY_ALPHA_DARK,
        frame, 1.0 - OVERLAY_ALPHA_DARK, 0
    )
    # 3. Ghép: vùng mask = sáng rõ, ngoài = mờ tối
    mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    output = np.where(mask_3ch == 255, frame, blended_bg)
    
    return output


def draw_hud_corners(frame, shape_rect, color,
                     length=HUD_CORNER_LENGTH,
                     thickness=HUD_CORNER_THICKNESS):
    """
    Vẽ 4 góc HUD (Corner Brackets) kiểu khóa mục tiêu công nghệ cao.
    """
    x1, y1, x2, y2 = shape_rect
    # Nới lỏng brackets ra ngoài 1 chút
    p = 5
    x1, y1 = x1 - p, y1 - p
    x2, y2 = x2 + p, y2 + p
    
    # Góc trên-trái
    cv2.line(frame, (x1, y1), (x1 + length, y1), color, thickness)
    cv2.line(frame, (x1, y1), (x1, y1 + length), color, thickness)
    # Góc trên-phải
    cv2.line(frame, (x2, y1), (x2 - length, y1), color, thickness)
    cv2.line(frame, (x2, y1), (x2, y1 + length), color, thickness)
    # Góc dưới-trái
    cv2.line(frame, (x1, y2), (x1 + length, y2), color, thickness)
    cv2.line(frame, (x1, y2), (x1, y2 - length), color, thickness)
    # Góc dưới-phải
    cv2.line(frame, (x2, y2), (x2 - length, y2), color, thickness)
    cv2.line(frame, (x2, y2), (x2, y2 - length), color, thickness)


def draw_bottom_status_bar(frame, text, status_color, frame_width, frame_height):
    """
    Vẽ thanh trạng thái (Bottom Status Bar) dưới cùng khung camera.
    Nền: Xám đậm bán trong suốt.
    Chữ & accent line: Đồng bộ theo trạng thái (status_color).
    """
    bar_h = STATUS_BAR_HEIGHT
    bar_y = frame_height - bar_h
    
    # 1. Nền xám đậm bán trong suốt
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, bar_y), (frame_width, frame_height), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
    
    # 2. Accent line cạnh trên
    cv2.line(frame, (0, bar_y), (frame_width, bar_y), status_color, 2, cv2.LINE_AA)
    
    # 3. Chữ in hoa căn giữa
    text = text.upper()
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size = cv2.getTextSize(text, font, STATUS_FONT_SCALE, STATUS_FONT_THICKNESS)[0]
    
    text_x = (frame_width - text_size[0]) // 2
    text_y = bar_y + (bar_h + text_size[1]) // 2 - 2
    
    cv2.putText(frame, text, (text_x, text_y), font, STATUS_FONT_SCALE,
                status_color, STATUS_FONT_THICKNESS, cv2.LINE_AA)


def render_full_hud(frame, ekyc_mask, shape_rect, current_color, status_text,
                    frame_width, frame_height):
    """
    Render toàn bộ HUD trong 1 lần gọi (tiện dùng cho cả kiosk và admin).
    
    Args:
        frame: frame camera gốc (BGR)
        ekyc_mask: mask nhị phân rounded rect
        shape_rect: (x1, y1, x2, y2) bounding rect
        current_color: màu trạng thái (BGR)
        status_text: dòng chữ trạng thái
        frame_width, frame_height: kích thước frame
    Returns:
        output: frame đã render HUD
    """
    # 1. Dark overlay + đục lỗ
    output = render_ekyc_frame(frame, ekyc_mask)
    
    # 2. 4 góc HUD
    draw_hud_corners(output, shape_rect, current_color)
    
    # 3. Bottom status bar
    draw_bottom_status_bar(output, status_text, current_color,
                           frame_width, frame_height)
    
    return output
