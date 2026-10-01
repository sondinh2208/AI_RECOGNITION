"""
==============================================================
UI HELPERS & AVATAR RENDERING
Hỗ trợ hiển thị ảnh đại diện dạng tròn và avatar mặc định.
==============================================================
"""

from PIL import Image, ImageDraw


def paginate_items(items, current_page=1, page_size=10):
    """Return a safe page slice and normalized pagination metadata."""
    try:
        page_size = max(1, int(page_size))
    except (TypeError, ValueError):
        page_size = 10

    total_items = len(items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    try:
        current_page = int(current_page)
    except (TypeError, ValueError):
        current_page = 1
    current_page = min(max(1, current_page), total_pages)

    start_index = (current_page - 1) * page_size
    end_index = min(start_index + page_size, total_items)
    return items[start_index:end_index], current_page, total_pages


def pagination_page_items(current_page, total_pages):
    """Build the compact page-number list used by the pagination bar."""
    total_pages = max(1, int(total_pages))
    current_page = min(max(1, int(current_page)), total_pages)

    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    if current_page <= 3:
        return [1, 2, 3, 4, "...", total_pages]
    if current_page >= total_pages - 2:
        return [1, "...", total_pages - 3, total_pages - 2, total_pages - 1, total_pages]
    return [1, "...", current_page - 1, current_page, current_page + 1, "...", total_pages]


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


def render_pagination_controls(
    parent,
    current_page,
    total_pages,
    on_page_change,
    page_size=10,
    page_size_options=None,
    on_size_change=None,
):
    """
    Render thanh phân trang chuẩn giao diện SaaS:
    [Hiển thị [10 v] kết quả]                     [< [1] 2 3 ... 13 >]
    """
    import customtkinter as ctk
    from config import CTK_TEXT, CTK_TEXT_DIM, CTK_ACCENT, CTK_SIDEBAR_HOVER

    if parent is None or not parent.winfo_exists():
        return

    for child in parent.winfo_children():
        child.destroy()

    if page_size_options is None:
        page_size_options = ["5", "10", "20", "50"]

    # 1. Khối bên trái: Hiển thị [dropdown] kết quả
    show_frame = ctk.CTkFrame(parent, fg_color="transparent")
    show_frame.pack(side="left")

    ctk.CTkLabel(
        show_frame,
        text="Hiển thị",
        text_color=CTK_TEXT_DIM,
        font=ctk.CTkFont(size=13),
    ).pack(side="left", padx=(0, 8))

    if on_size_change and page_size_options:
        def _on_dropdown_select(choice):
            try:
                new_sz = int(choice)
                on_size_change(new_sz)
            except Exception:
                pass

        dropdown = ctk.CTkOptionMenu(
            show_frame,
            values=[str(x) for x in page_size_options],
            command=_on_dropdown_select,
            width=64,
            height=28,
            corner_radius=6,
            fg_color=("#F1F5F9", "#111C2E"),
            button_color=CTK_ACCENT,
            text_color=CTK_TEXT,
            font=ctk.CTkFont(size=12),
        )
        dropdown.set(str(page_size))
        dropdown.pack(side="left")
    else:
        ctk.CTkLabel(
            show_frame,
            text=str(page_size),
            text_color=CTK_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

    ctk.CTkLabel(
        show_frame,
        text="kết quả",
        text_color=CTK_TEXT_DIM,
        font=ctk.CTkFont(size=13),
    ).pack(side="left", padx=(8, 0))

    # 2. Khối bên phải: Nút chuyển trang < [1] 2 3 ... 13 >
    page_frame = ctk.CTkFrame(parent, fg_color="transparent")
    page_frame.pack(side="right")

    total_pages = max(1, int(total_pages))
    current_page = min(max(1, int(current_page)), total_pages)

    # Nút Previous (<)
    can_prev = current_page > 1
    btn_prev = ctk.CTkButton(
        page_frame,
        text="<",
        width=28,
        height=28,
        corner_radius=6,
        fg_color="transparent",
        text_color=CTK_TEXT if can_prev else CTK_TEXT_DIM,
        hover_color=CTK_SIDEBAR_HOVER,
        font=ctk.CTkFont(size=12, weight="bold"),
        state="normal" if can_prev else "disabled",
        command=lambda: on_page_change(current_page - 1) if can_prev else None,
    )
    btn_prev.pack(side="left", padx=2)

    # Danh sách các nút số trang
    page_items = pagination_page_items(current_page, total_pages)

    for item in page_items:
        if item == "...":
            ctk.CTkLabel(
                page_frame,
                text="...",
                width=24,
                height=28,
                text_color=CTK_TEXT_DIM,
                font=ctk.CTkFont(size=12),
            ).pack(side="left", padx=2)
        else:
            p = int(item)
            is_active = (p == current_page)
            btn_p = ctk.CTkButton(
                page_frame,
                text=str(p),
                width=28,
                height=28,
                corner_radius=6,
                fg_color="#2563EB" if is_active else "transparent",
                text_color="#FFFFFF" if is_active else CTK_TEXT,
                hover_color="#1D4ED8" if is_active else CTK_SIDEBAR_HOVER,
                font=ctk.CTkFont(size=12, weight="bold" if is_active else "normal"),
                command=lambda page_num=p: on_page_change(page_num),
            )
            btn_p.pack(side="left", padx=2)

    # Nút Next (>)
    can_next = current_page < total_pages
    btn_next = ctk.CTkButton(
        page_frame,
        text=">",
        width=28,
        height=28,
        corner_radius=6,
        fg_color="transparent",
        text_color=CTK_TEXT if can_next else CTK_TEXT_DIM,
        hover_color=CTK_SIDEBAR_HOVER,
        font=ctk.CTkFont(size=12, weight="bold"),
        state="normal" if can_next else "disabled",
        command=lambda: on_page_change(current_page + 1) if can_next else None,
    )
    btn_next.pack(side="left", padx=2)
