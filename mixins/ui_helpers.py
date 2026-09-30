"""
==============================================================
UI HELPERS & AVATAR RENDERING
Hỗ trợ hiển thị ảnh đại diện dạng tròn và avatar mặc định.
==============================================================
"""

from PIL import Image, ImageDraw


def make_circular_avatar(pil_img, size=(90, 90), border_color="#10b981", border_width=3):
    """Cắt ảnh thành hình tròn với viền màu hiện đại (Anti-aliased)."""
    try:
        w, h = pil_img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        cropped = pil_img.crop((left, top, left + min_dim, top + min_dim)).resize(size, Image.LANCZOS)
        
        mask = Image.new("L", size, 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0, size[0], size[1]), fill=255)
        
        circular = Image.new("RGBA", size, (0, 0, 0, 0))
        circular.paste(cropped.convert("RGBA"), (0, 0), mask=mask)
        
        if border_width > 0 and border_color:
            draw_b = ImageDraw.Draw(circular)
            draw_b.ellipse(
                (border_width // 2, border_width // 2, size[0] - border_width // 2 - 1, size[1] - border_width // 2 - 1),
                outline=border_color, width=border_width
            )
        return circular
    except Exception:
        return create_default_avatar(size=size, bg_color="#e2e8f0", border_color=border_color)


def create_default_avatar(size=(90, 90), bg_color="#f1f5f9", border_color="#cbd5e1"):
    """Tạo avatar mặc định hình tròn dạng Vector khi chưa có ảnh."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size[0] - 3, size[1] - 3), fill=bg_color, outline=border_color, width=2)
    cx, cy = size[0] // 2, size[1] // 2
    r_head = size[0] // 5
    draw.ellipse((cx - r_head, cy - r_head - 7, cx + r_head, cy + r_head - 7), fill="#94a3b8")
    draw.chord((cx - size[0]//3, cy + 4, cx + size[0]//3, cy + size[0]//2 + 8), 0, 180, fill="#94a3b8")
    return img
