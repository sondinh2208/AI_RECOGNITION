"""Launcher duy nhất cho FaceCheck: webcam, ESP32 USB hoặc ESP32 Wi-Fi."""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - fallback cho môi trường tối giản
    psutil = None


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


ROOT = Path(__file__).resolve().parent
ADMIN_HEALTH_URL = "http://127.0.0.1:8765/api/health"
USB_HEALTH_URL = "http://127.0.0.1:8766/health"
USB_STREAM_URL = "http://127.0.0.1:8766/stream"
WS_CAMERA_HEALTH_URL = "http://127.0.0.1:8769/health"
WS_CAMERA_STREAM_URL = "http://127.0.0.1:8769/stream"
DEFAULT_CAMERA_HOST = "facecheck-cam.local"
MINIMUM_DISPLAY_FPS = 5.0


def load_local_environment(environment: dict[str, str]) -> dict[str, str]:
    """Nạp .env cục bộ nhưng không ghi đè biến đã đặt từ shell."""
    path = ROOT / ".env"
    if not path.exists():
        return environment
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environment.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return environment


def service_health(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def facecheck_running() -> bool:
    status = service_health(ADMIN_HEALTH_URL)
    return bool(
        status
        and status.get("ok") is True
        and status.get("service") == "FaceCheck Web UI"
    )


def camera_health(host: str, timeout: float = 0.7) -> dict | None:
    status = service_health(f"http://{host}/health", timeout=timeout)
    if status and status.get("ok") is True and status.get("camera") == "OV3660":
        return status
    return None


def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Lấy các mạng IPv4 đang hoạt động, giới hạn tối đa theo từng /24."""
    networks: list[ipaddress.IPv4Network] = []
    if psutil is not None:
        stats = psutil.net_if_stats()
        for interface, addresses in psutil.net_if_addrs().items():
            if not stats.get(interface) or not stats[interface].isup:
                continue
            for address in addresses:
                if address.family != socket.AF_INET or not address.netmask:
                    continue
                ip = ipaddress.ip_address(address.address)
                if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                    continue
                network = ipaddress.ip_network(
                    f"{address.address}/{address.netmask}", strict=False
                )
                if network.num_addresses > 1024:
                    network = ipaddress.ip_network(f"{ip}/24", strict=False)
                if network not in networks:
                    networks.append(network)

    try:
        local_addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        local_addresses = []
    for raw_ip in local_addresses:
        ip = ipaddress.ip_address(raw_ip)
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            continue
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
        if not any(ip in existing for existing in networks):
            networks.append(network)

    if networks:
        return networks

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        local_ip = ipaddress.ip_address(probe.getsockname()[0])
        if not local_ip.is_loopback and not local_ip.is_link_local:
            return [ipaddress.ip_network(f"{local_ip}/24", strict=False)]
    except OSError:
        pass
    finally:
        probe.close()
    return []


def discover_camera(
    preferred_host: str | None,
    scan_lan: bool = True,
    environment: dict[str, str] | None = None,
) -> tuple[str, dict] | None:
    if environment is None:
        environment = os.environ
    candidates: list[str] = []
    configured_host = environment.get("FACECHECK_CAMERA_HOST")
    for candidate in (preferred_host, configured_host, DEFAULT_CAMERA_HOST):
        candidate = (candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    configured = {(preferred_host or "").strip(), (configured_host or "").strip()}
    for host in candidates:
        health = camera_health(host, timeout=5.0 if host in configured else 1.5)
        if health:
            return host, health

    networks = local_ipv4_networks() if scan_lan else []
    hosts = list(dict.fromkeys(
        str(host) for network in networks for host in network.hosts()
    ))
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as executor:
        futures = {executor.submit(camera_health, host, 0.8): host for host in hosts}
        for future in concurrent.futures.as_completed(futures):
            health = future.result()
            if health:
                return futures[future], health
    return None


def measure_mjpeg_fps(stream_url: str, duration: float = 3.0) -> float:
    request = urllib.request.Request(stream_url, headers={"Cache-Control": "no-cache"})
    started_at = time.perf_counter()
    frames = 0
    buffer = b""
    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:
            while time.perf_counter() - started_at < duration:
                chunk = response.read(8192)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    end = buffer.find(b"\xff\xd9")
                    if end < 0:
                        if len(buffer) > 512_000:
                            buffer = buffer[-512_000:]
                        break
                    frames += 1
                    buffer = buffer[end + 2 :]
    except (OSError, TimeoutError):
        return 0.0
    elapsed = time.perf_counter() - started_at
    return frames / elapsed if elapsed else 0.0


def start_usb_bridge(port: str | None = None) -> subprocess.Popen | None:
    status = service_health(USB_HEALTH_URL)
    if status and status.get("ok"):
        print("[USB-CAMERA] Tái sử dụng cầu nối USB đang chạy.")
        return None

    command = [sys.executable, "-u", str(ROOT / "tools" / "usb_camera_bridge.py")]
    if port:
        command.extend(["--port", port])
    bridge = subprocess.Popen(command, cwd=ROOT)
    print("[USB-CAMERA] Đang chờ ESP32-S3 qua USB-OTG...")
    for _ in range(30):
        if bridge.poll() is not None:
            raise RuntimeError("Cầu nối USB đã dừng bất thường.")
        status = service_health(USB_HEALTH_URL)
        if status and status.get("ok"):
            return bridge
        time.sleep(0.5)
    bridge.terminate()
    raise RuntimeError("Không nhận được camera USB sau 15 giây.")


def start_wifi_bridge(environment: dict[str, str]) -> subprocess.Popen | None:
    status = service_health(WS_CAMERA_HEALTH_URL)
    if status and status.get("service") == "FaceCheck WebSocket Camera":
        print("[WS-CAMERA] Tai su dung cau WebSocket dang chay.")
        return None

    token = environment.get("FACECHECK_CAMERA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("FACECHECK_CAMERA_TOKEN chua duoc cau hinh trong .env")
    command = [sys.executable, "-u", str(ROOT / "tools" / "ws_camera_bridge.py")]
    bridge = subprocess.Popen(command, cwd=ROOT, env=environment)
    for _ in range(30):
        if bridge.poll() is not None:
            raise RuntimeError("Cau WebSocket camera da dung bat thuong.")
        status = service_health(WS_CAMERA_HEALTH_URL)
        if status and status.get("service") == "FaceCheck WebSocket Camera":
            return bridge
        time.sleep(0.2)
    bridge.terminate()
    raise RuntimeError("Khong mo duoc WebSocket camera server tai cong 8768.")


def wait_for_wifi_frames(timeout: float = 30.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service_health(WS_CAMERA_HEALTH_URL)
        if status and status.get("ok"):
            return status
        time.sleep(0.25)
    return None


def configure_wifi_camera(
    environment: dict[str, str],
    camera_host: str | None,
    scan_lan: bool,
    check_fps: bool,
) -> None:
    print("[WIFI-CAMERA] Đang tìm ESP32-S3 OV3660...")
    discovered = discover_camera(camera_host, scan_lan=scan_lan, environment=environment)
    if not discovered:
        raise RuntimeError(
            "Không tìm thấy camera Wi-Fi. Kiểm tra nguồn, Wi-Fi 2.4 GHz và log USB-UART."
        )

    host, health = discovered
    resolved_host = str(health.get("ip") or host)
    stream_url = f"http://{resolved_host}:81/stream"
    print(
        f"[WIFI-CAMERA] Đã kết nối {resolved_host} | "
        f"RSSI={health.get('rssi')} dBm | mục tiêu={health.get('target_fps', 10)} FPS"
    )
    if check_fps:
        fps = measure_mjpeg_fps(stream_url)
        print(f"[WIFI-CAMERA] Tốc độ nguồn đo được: {fps:.1f} FPS")
        if fps < MINIMUM_DISPLAY_FPS:
            print("[CẢNH BÁO] Nguồn ảnh dưới 5 FPS; hãy đặt camera gần router hơn.")

    environment["FACECHECK_CAMERA_URL"] = WS_CAMERA_STREAM_URL
    environment["FACECHECK_CAMERA_HOST"] = resolved_host
    environment["FACECHECK_CAMERA_PROFILE"] = "embedded_ov3660"
    environment.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Khởi động hệ thống FaceCheck")
    parser.add_argument(
        "camera",
        nargs="?",
        default="webcam",
        choices=("webcam", "usb", "wifi"),
        help="Nguồn camera (mặc định: webcam)",
    )
    parser.add_argument("--simulate-display", "--simulate-pi", action="store_true")
    parser.add_argument("--camera-index", type=int, help="ID webcam, ví dụ 0 hoặc 1")
    parser.add_argument("--usb-port", help="Cổng ESP32 USB-OTG, ví dụ COM12")
    parser.add_argument("--camera-host", help="IP/hostname camera ESP32 Wi-Fi")
    parser.add_argument("--no-lan-scan", action="store_true")
    parser.add_argument("--skip-fps-check", action="store_true")
    return parser.parse_args(argv)


def run(arguments) -> int:
    if facecheck_running():
        print("[FACECHECK] Hệ thống đã chạy. Hãy đóng cửa sổ hiện tại trước khi đổi camera.")
        return 2

    environment = load_local_environment(os.environ.copy())
    bridge = None
    try:
        if arguments.camera == "webcam":
            environment.pop("FACECHECK_CAMERA_URL", None)
            environment["FACECHECK_CAMERA_PROFILE"] = "webcam"
            if arguments.camera_index is not None:
                environment["FACECHECK_CAMERA_INDEX"] = str(arguments.camera_index)
        elif arguments.camera == "usb":
            bridge = start_usb_bridge(arguments.usb_port)
            environment["FACECHECK_CAMERA_URL"] = USB_STREAM_URL
            environment["FACECHECK_CAMERA_PROFILE"] = "embedded_ov3660"
        else:
            bridge = start_wifi_bridge(environment)
            configure_wifi_camera(
                environment,
                camera_host=arguments.camera_host,
                scan_lan=not arguments.no_lan_scan,
                check_fps=False,
            )
            status = wait_for_wifi_frames()
            if not status:
                raise RuntimeError(
                    "ESP32 chua ket noi WebSocket. Kiem tra FACECHECK_SERVER_HOST "
                    "trong secrets.h va Windows Firewall cho cong 8768."
                )
            print(
                f"[WS-CAMERA] Da nhan frame binary | "
                f"FPS trung binh={status.get('average_fps', 0):.1f} | "
                f"do tuoi frame={status.get('frame_age_ms')} ms"
            )

        command = [sys.executable, str(ROOT / "web_admin.py")]
        if arguments.simulate_display:
            command.append("--simulate-display")
        return subprocess.call(command, cwd=ROOT, env=environment)
    except (OSError, RuntimeError) as error:
        print(f"[FACECHECK] {error}")
        return 1
    finally:
        if bridge is not None and bridge.poll() is None:
            bridge.terminate()
            try:
                bridge.wait(timeout=3)
            except subprocess.TimeoutExpired:
                bridge.kill()


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
