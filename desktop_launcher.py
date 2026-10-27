"""Windows desktop launcher. AI stays in its own logged child process."""

from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import webbrowser

from facecheck import facecheck_running, load_local_environment, wait_for_camera
from branding import APP_NAME

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
ADMIN_URL = "http://127.0.0.1:8765/?view=admin#dashboard"


def python_executable():
    python = ROOT / "venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"Không tìm thấy môi trường Python của {APP_NAME} trong thư mục venv.")
    return str(python)


def backend_environment(host, health):
    environment = load_local_environment(os.environ.copy())
    stream_host = host if host.lower().endswith(".local") else str(health.get("ip") or host)
    environment.update({
        "FACECHECK_CAMERA_URL": f"http://{stream_host}:81/stream",
        "FACECHECK_CAMERA_HOST": stream_host,
        "FACECHECK_CAMERA_PROFILE": "embedded_ov3660",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
    environment.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
    return environment


def probe_camera():
    # stdout contains only the small result; diagnostics go to the log via stderr.
    with contextlib.redirect_stdout(sys.stderr):
        result = wait_for_camera(None, environment=load_local_environment(os.environ.copy()),
                                 timeout=40)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result else 1


def hidden_process(command, **kwargs):
    return subprocess.Popen(
        command, cwd=ROOT, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs,
    )


def stop_owned_process(process):
    """Stop only our startup child and its venv redirector children on Cancel."""
    if process is None or process.poll() is not None:
        return
    import psutil
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        for child in reversed(descendants):
            with contextlib.suppress(psutil.NoSuchProcess):
                child.terminate()
        with contextlib.suppress(psutil.NoSuchProcess):
            parent.terminate()
        _, alive = psutil.wait_procs(descendants + [parent], timeout=3)
        for child in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
    except psutil.NoSuchProcess:
        pass
    process.wait(timeout=5)


class LauncherMutex:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        identity = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:20]
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\FaceCheckDesktop-" + identity)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def activate_existing():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    matches = []

    @callback_type
    def visit(window, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title, len(title))
        if user32.IsWindowVisible(window) and (
            title.value.startswith(APP_NAME + " -") or
            title.value.startswith("Khởi động " + APP_NAME) or
            title.value.startswith("FaceCheck -") or
            title.value.startswith("Khởi động FaceCheck")
        ):
            matches.append(window)
        return True

    user32.EnumWindows(visit, 0)
    if matches:
        user32.ShowWindow(matches[0], 9)
        user32.SetForegroundWindow(matches[0])
        return True
    if facecheck_running():
        webbrowser.open(ADMIN_URL)
        return True
    return False


class DesktopLauncher:
    def __init__(self, root):
        from launcher_ui import LauncherView
        self.root = root
        self.process = None
        self.phase = "idle"
        self.started = 0.0
        self.closed = False
        LOGS.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.log_path = LOGS / f"facetouch-pro-{stamp}.log"
        self.log = self.log_path.open("a", encoding="utf-8", buffering=1)
        self.root.title("Khởi động " + APP_NAME)
        icon = ROOT / "assets" / "facecheck-logo.ico"
        if not icon.is_file():
            icon = ROOT / "assets" / "facecheck.ico"
        if icon.is_file():
            self.root.iconbitmap(str(icon))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.view = LauncherView(root, assets=ROOT / "assets", on_retry=self.start,
                                 on_log=self.open_log, on_close=self.close)
        for name in ("status", "detail", "progress", "retry", "close_button"):
            setattr(self, name, getattr(self.view, name))

    def show_stage(self, stage, *, failed=False):
        if hasattr(self, "view"):
            self.view.set_stage(stage, failed=failed)

    def open_log(self):
        self.log.flush()
        os.startfile(str(self.log_path))

    def error(self, title, detail):
        self.phase = "error"
        self.progress.stop()
        self.root.deiconify()
        self.status.set(title)
        self.detail.set(detail)
        self.show_stage(None, failed=True)
        self.retry.state(["!disabled"])
        self.close_button.configure(text="Đóng")

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        if facecheck_running():
            activate_existing()
            self.close()
            return
        self.retry.state(["disabled"])
        self.close_button.configure(text="Hủy")
        self.progress.start(12)
        self.show_stage("camera")
        self.view.reset_connection()
        self.status.set("Đang tìm camera Wi-Fi…")
        self.detail.set("Đang kiểm tra kết nối. Hãy bật nguồn camera và dùng cùng Wi-Fi với laptop.")
        self.log.write("\n=== Tìm camera ===\n")
        try:
            self.process = hidden_process(
                [python_executable(), "-u", str(ROOT / "desktop_launcher.py"), "--probe"],
                stdout=subprocess.PIPE, stderr=self.log,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
            self.phase = "probe"
            self.started = time.monotonic()
            self.root.after(200, self.tick)
        except Exception:
            traceback.print_exc(file=self.log)
            self.error("Không thể khởi động", "Mở Chi tiết kỹ thuật để xem lỗi, rồi thử lại.")

    def tick(self):
        if self.closed:
            return
        try:
            code = self.process.poll()
            if self.phase == "probe":
                if code is None and time.monotonic() - self.started > 75:
                    stop_owned_process(self.process)
                    self.error("Chưa tìm thấy camera", "Kiểm tra nguồn camera và bảo đảm camera cùng Wi-Fi với laptop, rồi tìm lại.")
                    return
                if code is not None:
                    output, _ = self.process.communicate()
                    if code != 0:
                        self.error("Chưa tìm thấy camera", "Kiểm tra nguồn và Wi-Fi của camera. Nếu vừa đổi mạng, thử nhấn RESET một lần rồi tìm lại.")
                        return
                    host, health = json.loads(output.decode("utf-8"))
                    if facecheck_running():
                        activate_existing()
                        self.close()
                        return
                    self.log.write(f"\n=== Khởi động giao diện; camera {host} ===\n")
                    self.process = hidden_process(
                        [python_executable(), "-u", str(ROOT / "web_admin.py")],
                        stdout=self.log, stderr=subprocess.STDOUT,
                        env=backend_environment(host, health),
                    )
                    self.phase = "backend"
                    self.started = time.monotonic()
                    self.show_stage("ai")
                    if hasattr(self, "view"):
                        self.view.set_camera(str(health.get("ip") or host))
                    self.status.set("Đang chuẩn bị nhận diện…")
                    self.detail.set("Camera đã sẵn sàng. Lần tải AI đầu tiên có thể mất vài phút.")
            elif self.phase == "backend":
                if code is not None:
                    self.error(f"{APP_NAME} chưa mở được", "Mở Chi tiết kỹ thuật để xem lỗi, rồi thử lại.")
                    return
                if facecheck_running():
                    self.phase = "running"
                    self.progress.stop()
                    self.show_stage("opening")
                    self.status.set(f"Đang mở {APP_NAME}…")
                    self.detail.set("Kết nối đã sẵn sàng. Đang chuyển sang giao diện chính.")
                    self.root.after(350, self.root.withdraw)
                elif time.monotonic() - self.started > 180:
                    self.detail.set("AI đang tải lâu hơn bình thường. Mở Chi tiết kỹ thuật để kiểm tra, hoặc nhấn Hủy.")
            elif self.phase == "running" and code is not None:
                if code == 0:
                    self.close()
                else:
                    self.error(f"{APP_NAME} đã dừng bất thường", "Mở Chi tiết kỹ thuật để xem lỗi, hoặc thử khởi động lại.")
                return
            self.root.after(500, self.tick)
        except Exception:
            traceback.print_exc(file=self.log)
            stop_owned_process(self.process)
            self.error("Không thể khởi động", "Mở Chi tiết kỹ thuật để xem lỗi, rồi thử lại.")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.progress.stop()
        if self.phase in {"probe", "backend"}:
            stop_owned_process(self.process)
        self.log.close()
        self.root.destroy()


def main():
    if "--probe" in sys.argv:
        raise SystemExit(probe_camera())
    import tkinter as tk
    from tkinter import messagebox
    mutex = None
    root = None
    try:
        mutex = LauncherMutex()
        preview = "--preview" in sys.argv or "--preview-error" in sys.argv
        if "--smoke-test" not in sys.argv and not preview and (mutex.already_running or facecheck_running()):
            if not activate_existing():
                messagebox.showinfo(APP_NAME, f"{APP_NAME} đang khởi động. Vui lòng đợi cửa sổ hiện có.")
            return 0
        root = tk.Tk()
        app = DesktopLauncher(root)
        if "--smoke-test" in sys.argv:
            root.update()
            assert app.root.winfo_viewable()
            assert app.retry.instate(["disabled"])
            app.error("Chưa tìm thấy camera", "Kiểm tra nguồn và Wi-Fi của camera, rồi tìm lại.")
            root.update()
            assert app.retry.instate(["!disabled"])
            assert app.close_button.winfo_rooty() + app.close_button.winfo_height() <= (
                root.winfo_rooty() + root.winfo_height()
            )
            root.after(1500, app.close)
        elif preview:
            root.title(f"Khởi động {APP_NAME} — Xem trước")
            app.show_stage("camera")
            app.progress.start()
            app.status.set("Đang tìm camera Wi-Fi…")
            app.detail.set("Đang kiểm tra kết nối. Hãy bật nguồn camera và dùng cùng Wi-Fi với laptop.")
            app.view.reset_connection()
            if "--preview-error" in sys.argv:
                app.error("Chưa tìm thấy camera", "Kiểm tra nguồn và Wi-Fi của camera. Nếu vừa đổi mạng, thử nhấn RESET một lần rồi tìm lại.")
            root.after(180000, app.close)
        else:
            root.after(150, app.start)
        root.mainloop()
    except Exception:
        LOGS.mkdir(exist_ok=True)
        with (LOGS / "desktop-error.log").open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        messagebox.showerror(APP_NAME, f"Không mở được {APP_NAME}. Xem logs/desktop-error.log trong thư mục dự án.")
        return 1
    finally:
        if mutex:
            mutex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
