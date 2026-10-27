"""Compact Tk startup view, independent from the camera/AI process lifecycle."""

from pathlib import Path
import tkinter as tk
from tkinter import ttk
from branding import APP_NAME, APP_TAGLINE

BLUE = "#1570EF"
INK = "#101828"
MUTED = "#667085"
LINE = "#EAECF0"


class ActivityIndicator(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=64, height=64, bg="white", highlightthickness=0)
        self.job = None
        self.angle = 0
        self.failed = False
        self.draw()

    def draw(self):
        self.delete("all")
        tint = "#FEF3F2" if self.failed else "#EFF8FF"
        color = "#D92D20" if self.failed else BLUE
        self.create_oval(3, 3, 61, 61, fill=tint, outline=tint)
        if self.failed:
            self.create_text(32, 31, text="!", fill=color, font=("Segoe UI", 24, "bold"))
        else:
            self.create_rectangle(19, 25, 45, 42, outline=color, width=2)
            self.create_line(23, 25, 27, 20, 37, 20, 41, 25, fill=color, width=2)
            self.create_oval(27, 28, 37, 38, outline=color, width=2)
            self.create_arc(3, 3, 61, 61, start=self.angle, extent=95,
                            style="arc", outline=BLUE, width=2)

    def animate(self):
        self.angle = (self.angle - 12) % 360
        self.draw()
        self.job = self.after(65, self.animate)

    def start(self, _interval=12):
        self.stop()
        self.failed = False
        self.animate()

    def stop(self):
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None

    def show_error(self):
        self.stop()
        self.failed = True
        self.draw()


class LauncherView:
    def __init__(self, root, *, assets: Path, on_retry, on_log, on_close):
        self.root = root
        self.step = 0
        self.failed = False
        root.configure(bg="white")
        root.resizable(False, False)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("FC.Primary.TButton", font=("Segoe UI", 10, "bold"),
                        foreground="white", background=BLUE, borderwidth=0,
                        padding=(16, 9), focuscolor=BLUE)
        style.map("FC.Primary.TButton", background=[("active", "#175CD3")],
                  foreground=[("disabled", "#98A2B3")])
        style.configure("FC.Secondary.TButton", font=("Segoe UI", 10),
                        foreground="#344054", background="white", borderwidth=1,
                        bordercolor="#D0D5DD", padding=(14, 8), focuscolor="white")
        style.map("FC.Secondary.TButton", background=[("active", "#F2F4F7")])

        header = tk.Frame(root, bg="white", padx=24, pady=14)
        header.pack(fill="x")
        logo_path = assets / "facecheck-id-icon-filled-256.png"
        if logo_path.is_file():
            from PIL import Image, ImageTk
            with Image.open(logo_path) as image:
                self.logo = ImageTk.PhotoImage(image.resize((44, 44), Image.Resampling.LANCZOS))
            tk.Label(header, image=self.logo, bg="white").pack(side="left", padx=(0, 12))
        brand = tk.Frame(header, bg="white")
        brand.pack(side="left")
        tk.Label(brand, text=APP_NAME, font=("Segoe UI", 18, "bold"),
                 fg=INK, bg="white").pack(anchor="w")
        tk.Label(brand, text=APP_TAGLINE,
                 font=("Segoe UI", 9), fg=MUTED, bg="white").pack(anchor="w", pady=(1, 0))
        tk.Frame(root, bg=LINE, height=1).pack(fill="x")

        content = tk.Frame(root, bg="white", padx=24, pady=16)
        content.pack(fill="both", expand=True)
        hero = tk.Frame(content, bg="white")
        hero.pack(fill="x")
        self.progress = ActivityIndicator(hero)
        self.progress.pack(side="left", anchor="n", padx=(0, 16))
        copy = tk.Frame(hero, bg="white")
        copy.pack(side="left", fill="both", expand=True, pady=3)
        self.status = tk.StringVar(value="Đang chuẩn bị…")
        self.detail = tk.StringVar(value=f"Chỉ một chút nữa, {APP_NAME} sẽ sẵn sàng.")
        tk.Label(copy, textvariable=self.status, font=("Segoe UI", 13, "bold"),
                 fg=INK, bg="white", anchor="w").pack(fill="x")
        tk.Label(copy, textvariable=self.detail, font=("Segoe UI", 10),
                 fg=MUTED, bg="white", wraplength=390, justify="left",
                 anchor="w").pack(fill="x", pady=(6, 0))

        self.steps = tk.Canvas(content, width=512, height=55, highlightthickness=0, bg="white")
        self.steps.pack(fill="x", pady=(14, 10))
        self.steps.bind("<Configure>", lambda _event: self.draw_steps())
        self.connection = tk.StringVar(value="Camera Wi-Fi  ·  Chờ kết nối")
        connection = tk.Frame(content, bg="#F8FAFC", padx=12, pady=7)
        connection.pack(fill="x")
        tk.Label(connection, textvariable=self.connection, font=("Segoe UI", 9),
                 bg="#F8FAFC", fg="#475467", anchor="w", wraplength=475,
                 justify="left").pack(fill="x")
        tk.Frame(root, bg=LINE, height=1).pack(fill="x")
        footer = tk.Frame(root, bg="white", padx=24, pady=12)
        footer.pack(fill="x")
        tk.Button(footer, text="Chi tiết kỹ thuật ↗", command=on_log, relief="flat",
                  borderwidth=0, bg="white", fg=MUTED, activebackground="white",
                  activeforeground=BLUE, cursor="hand2", font=("Segoe UI", 9),
                  padx=0).pack(side="left")
        self.close_button = ttk.Button(footer, text="Hủy", style="FC.Secondary.TButton", command=on_close)
        self.close_button.pack(side="right")
        self.retry = ttk.Button(footer, text="Tìm lại camera", style="FC.Primary.TButton", command=on_retry)
        self.retry.state(["disabled"])
        self.fit_job = None
        self.fit_window(center=True)
        self.detail.trace_add("write", lambda *_args: self.schedule_fit())

    def schedule_fit(self):
        if self.fit_job is None:
            self.fit_job = self.root.after_idle(self.fit_window)

    def fit_window(self, center=False):
        self.fit_job = None
        self.root.update_idletasks()
        width = max(560, self.root.winfo_reqwidth())
        # Leave a little breathing room for Windows DPI/font metric rounding.
        height = self.root.winfo_reqheight() + 12
        geometry = f"{width}x{height}"
        if center:
            x = max(0, (self.root.winfo_screenwidth() - width) // 2)
            y = max(0, (self.root.winfo_screenheight() - height) // 2)
            geometry += f"+{x}+{y}"
        self.root.geometry(geometry)

    def draw_steps(self):
        self.steps.delete("all")
        width = max(480, self.steps.winfo_width())
        centers = (50, width / 2, width - 50)
        labels = ("Tìm camera", "Tải AI", "Mở giao diện")
        for i in range(2):
            self.steps.create_line(centers[i] + 15, 13, centers[i + 1] - 15, 13,
                                   fill=BLUE if i < self.step else LINE, width=2)
        for i, (x, label) in enumerate(zip(centers, labels)):
            done = i < self.step
            active = i == self.step
            color = "#D92D20" if active and self.failed else (BLUE if active or done else "#D0D5DD")
            self.steps.create_oval(x - 11, 2, x + 11, 24, fill=color, outline=color)
            self.steps.create_text(x, 13, text="✓" if done else str(i + 1),
                                   fill="white", font=("Segoe UI", 9, "bold"))
            self.steps.create_text(x, 43, text=label,
                                   fill=INK if active else MUTED,
                                   font=("Segoe UI", 9, "bold" if active else "normal"))

    def set_stage(self, stage, *, failed=False):
        if stage is not None:
            self.step = {"camera": 0, "ai": 1, "opening": 2}[stage]
        self.failed = failed
        if failed:
            self.progress.show_error()
            self.retry.pack(side="right", padx=(0, 10))
            if self.step == 0:
                self.connection.set("Camera Wi-Fi  ·  Chưa kết nối")
        else:
            self.retry.pack_forget()
        self.draw_steps()
        self.schedule_fit()

    def set_camera(self, host):
        self.connection.set(f"Camera · {host}  |  Đã tìm thấy trên mạng Wi-Fi")

    def reset_connection(self):
        self.connection.set("Camera Wi-Fi  ·  Đang tìm trên mạng của laptop…")
