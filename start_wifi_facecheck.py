"""Khoi dong FaceCheck voi camera ESP32-S3 truyen MJPEG qua Wi-Fi."""

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
except ImportError:  # pragma: no cover - fallback cho moi truong toi gian
    psutil = None


ROOT = Path(__file__).resolve().parent
DEFAULT_CAMERA_HOST = "facecheck-cam.local"
MINIMUM_DISPLAY_FPS = 5.0


def load_local_environment(environment: dict[str, str]) -> dict[str, str]:
    """Nap .env cuc bo ma khong ghi de bien da duoc dat tu shell."""
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


def camera_health(host: str, timeout: float = 0.7) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{host}/health", timeout=timeout) as response:
            payload = json.load(response)
        if payload.get("ok") is True and payload.get("camera") == "OV3660":
            return payload
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Lay tat ca mang IPv4 cuc bo dang hoat dong.

    May Windows co the dong thoi co Wi-Fi, Ethernet ao, VPN hoac chia se mang.
    Chi suy ra mang tu tuyen Internet se bo sot camera tren adapter khac.
    """
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
                # Khong quet hang chuc nghin dia chi cua VPN/mang doanh nghiep.
                # Camera gia dung nam trong cung /24 voi may chay FaceCheck.
                if network.num_addresses > 1024:
                    network = ipaddress.ip_network(f"{ip}/24", strict=False)
                if network not in networks:
                    networks.append(network)

    # Fallback khong can psutil, dong thoi bo sung adapter ma he dieu hanh
    # chua tra ve day du thong tin netmask.
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

    # Phuong an cuoi cung: lay dia chi cua tuyen mac dinh. UDP connect khong
    # gui goi tin va van hoat dong neu dich khong phan hoi.
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


def local_ipv4_network() -> ipaddress.IPv4Network | None:
    """Tuong thich voi ma cu: tra ve mang cuc bo dau tien neu co."""
    networks = local_ipv4_networks()
    return networks[0] if networks else None


def discover_camera(
    preferred_host: str | None,
    scan_lan: bool = True,
    environment: dict[str, str] | None = None,
) -> tuple[str, dict] | None:
    if environment is None:
        environment = os.environ
    candidates = []
    configured_host = environment.get("FACECHECK_CAMERA_HOST")
    for candidate in (preferred_host, configured_host, DEFAULT_CAMERA_HOST):
        candidate = (candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for host in candidates:
        # IP do nguoi dung chi dinh co the nam tren Wi-Fi truong hoc jitter cao;
        # cho du 5 giay thay vi ket luan camera offline qua som.
        is_configured = host in {
            (preferred_host or "").strip(),
            (configured_host or "").strip(),
        }
        timeout = 5.0 if is_configured else 1.5
        health = camera_health(host, timeout=timeout)
        if health:
            return host, health

    networks = local_ipv4_networks() if scan_lan else []
    if not networks:
        return None
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
    """Dem JPEG hoan chinh tren day truyen; khong can OpenCV hay model AI."""
    request = urllib.request.Request(stream_url, headers={"Cache-Control": "no-cache"})
    start = time.perf_counter()
    frames = 0
    buffer = b""
    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:
            while time.perf_counter() - start < duration:
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
    elapsed = time.perf_counter() - start
    return frames / elapsed if elapsed else 0.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Khoi dong FaceCheck voi camera ESP32-S3 qua Wi-Fi",
    )
    parser.add_argument("--camera-host", help="IP/hostname camera; mac dinh tu dong tim")
    parser.add_argument("--no-lan-scan", action="store_true", help="Khong quet mang LAN /24")
    parser.add_argument("--skip-fps-check", action="store_true", help="Bo qua phep do FPS ban dau")
    parser.add_argument(
        "--simulate-display", action="store_true",
        help="Mo cua so mo phong TFT 3.5 inch ben canh giao dien quan tri",
    )
    return parser.parse_args(argv)


def main(
    camera_host: str | None = None,
    simulate_display: bool = False,
    scan_lan: bool = True,
    check_fps: bool = True,
) -> int:
    environment = load_local_environment(os.environ.copy())
    print("[WIFI-CAMERA] Dang tim ESP32-S3 OV3660...")
    discovered = discover_camera(
        camera_host, scan_lan=scan_lan, environment=environment
    )
    if not discovered:
        print("[WIFI-CAMERA] Khong tim thay camera. Kiem tra Wi-Fi da cau hinh va log USB-UART 115200.")
        return 1

    host, health = discovered
    # Dung IP camera tra ve neu co, tranh mot so ban Windows/OpenCV khong giai
    # duoc ten .local trong backend FFmpeg.
    resolved_host = str(health.get("ip") or host)
    stream_url = f"http://{resolved_host}:81/stream"
    print(
        f"[WIFI-CAMERA] Da ket noi {resolved_host} | RSSI={health.get('rssi')} dBm "
        f"| muc tieu={health.get('target_fps', 10)} FPS"
    )

    if check_fps:
        fps = measure_mjpeg_fps(stream_url)
        print(f"[WIFI-CAMERA] Toc do nguon do duoc: {fps:.1f} FPS")
        if fps < MINIMUM_DISPLAY_FPS:
            print(
                f"[CANH BAO] Nguon anh dang duoi {MINIMUM_DISPLAY_FPS:.0f} FPS. "
                "Dat camera/router gan hon va dam bao dung Wi-Fi 2.4 GHz."
            )

    environment["FACECHECK_CAMERA_URL"] = stream_url
    environment["FACECHECK_CAMERA_HOST"] = resolved_host
    environment["FACECHECK_CAMERA_PROFILE"] = "embedded_ov3660"
    # Khong ep ``avioflags=direct``: tuy chon nay co the lam OpenCV/FFmpeg cho
    # vo han khi luong MJPEG di qua mang Wi-Fi co jitter cao. Pipeline camera
    # da dat CAP_PROP_BUFFERSIZE=1 de uu tien frame moi nhat.
    environment.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
    command = [sys.executable, str(ROOT / "web_admin.py")]
    if simulate_display:
        command.append("--simulate-display")
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(main(
        camera_host=arguments.camera_host,
        simulate_display=arguments.simulate_display,
        scan_lan=not arguments.no_lan_scan,
        check_fps=not arguments.skip_fps_check,
    ))
