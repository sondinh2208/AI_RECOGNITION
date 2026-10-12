"""Do luong MJPEG ESP32 va chon FPS on dinh thay vi FPS dinh cao nhat."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Measurement:
    target_fps: int
    actual_fps: float
    p95_gap_ms: float
    max_gap_ms: float
    jitter_percent: float
    stalls: int
    average_jpeg_bytes: int

    @property
    def delivery_ratio(self) -> float:
        return self.actual_fps / self.target_fps if self.target_fps else 0.0

    @property
    def stable(self) -> bool:
        expected_gap_ms = 1000.0 / self.target_fps
        return (
            self.delivery_ratio >= 0.82
            and self.p95_gap_ms <= expected_gap_ms * 2.2
            and self.jitter_percent <= 55.0
            and self.stalls == 0
        )


def read_json(url: str, timeout: float = 3.0) -> dict:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def set_target_fps(host: str, fps: int) -> dict:
    query = urllib.parse.urlencode({"fps": fps})
    return read_json(f"http://{host}/config?{query}")


def wait_for_stream_idle(host: str, timeout: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            if not read_json(f"http://{host}/health", timeout=1.0).get("stream_active"):
                return True
        except (OSError, TimeoutError):
            pass
        time.sleep(0.25)
    return False


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[max(0, index)]


def measure_stream(host: str, target_fps: int, duration: float, warmup: float) -> Measurement:
    request = urllib.request.Request(
        f"http://{host}:81/stream",
        headers={"Cache-Control": "no-cache", "Connection": "close"},
    )
    timestamps: list[float] = []
    jpeg_sizes: list[int] = []
    connected_at = time.perf_counter()
    deadline = connected_at + warmup + duration

    with urllib.request.urlopen(request, timeout=4.0) as response:
        while time.perf_counter() < deadline:
            content_length = 0
            while time.perf_counter() < deadline:
                line = response.readline()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
                if line in {b"\r\n", b"\n"} and content_length:
                    break
            if not content_length:
                break
            jpeg = response.read(content_length)
            if len(jpeg) != content_length:
                break
            now = time.perf_counter()
            if now - connected_at >= warmup:
                timestamps.append(now)
                jpeg_sizes.append(content_length)

    gaps_ms = [
        (current - previous) * 1000.0
        for previous, current in zip(timestamps, timestamps[1:])
    ]
    elapsed = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else duration
    actual_fps = (len(timestamps) - 1) / elapsed if elapsed > 0 and len(timestamps) > 1 else 0.0
    mean_gap = statistics.fmean(gaps_ms) if gaps_ms else math.inf
    jitter = (
        statistics.pstdev(gaps_ms) / mean_gap * 100.0
        if len(gaps_ms) > 1 and mean_gap > 0
        else 0.0
    )
    stall_limit_ms = max(500.0, (1000.0 / target_fps) * 4.0)
    return Measurement(
        target_fps=target_fps,
        actual_fps=actual_fps,
        p95_gap_ms=percentile(gaps_ms, 0.95),
        max_gap_ms=max(gaps_ms, default=math.inf),
        jitter_percent=jitter,
        stalls=sum(gap > stall_limit_ms for gap in gaps_ms),
        average_jpeg_bytes=round(statistics.fmean(jpeg_sizes)) if jpeg_sizes else 0,
    )


def choose_fps(measurements: list[Measurement], max_useful_fps: int = 8) -> Measurement:
    if not measurements:
        raise ValueError("Can it nhat mot ket qua do FPS")

    # Mot muc chi duoc coi la on dinh khi tat ca cac lan lap deu dat. Dieu nay
    # tranh chon nham muc sat nguong vi mot lan do dep bat thuong.
    grouped: dict[int, list[Measurement]] = defaultdict(list)
    for item in measurements:
        grouped[item.target_fps].append(item)
    eligible_targets = [
        target for target, trials in grouped.items()
        if target <= max_useful_fps and all(item.stable for item in trials)
    ]
    if eligible_targets:
        selected_target = max(eligible_targets)
        return min(
            grouped[selected_target],
            key=lambda item: (item.delivery_ratio, -item.jitter_percent),
        )
    return max(
        measurements,
        key=lambda item: (
            item.target_fps <= max_useful_fps,
            item.actual_fps - item.stalls * 2.0 - item.jitter_percent / 100.0,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark FPS camera ESP32 qua Wi-Fi")
    parser.add_argument("--host", required=True, help="IP camera, vi du 192.168.1.12")
    parser.add_argument("--fps", nargs="+", type=int, default=[5, 6, 7, 8, 9, 10])
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--warmup", type=float, default=1.5)
    parser.add_argument(
        "--repeats", type=int, default=3,
        help="So lan lap cho moi muc; muc FPS chi dat neu moi lan lap deu on dinh",
    )
    parser.add_argument("--max-useful-fps", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    health = read_json(f"http://{args.host}/health")
    print(
        f"[CAMERA] {health.get('camera')} | RSSI={health.get('rssi')} dBm | "
        f"target hien tai={health.get('target_fps')} FPS"
    )
    measurements: list[Measurement] = []
    if args.repeats < 1:
        raise SystemExit("--repeats phai lon hon hoac bang 1")
    for target in args.fps:
        for trial in range(1, args.repeats + 1):
            set_target_fps(args.host, target)
            time.sleep(1.0)
            try:
                result = measure_stream(args.host, target, args.duration, args.warmup)
            except (OSError, TimeoutError) as error:
                print(f"[FPS {target:2d} #{trial}] LOI: {error}")
                result = Measurement(target, 0.0, math.inf, math.inf, 100.0, 1, 0)
            measurements.append(result)
            status = "DAT" if result.stable else "KHONG DAT"
            print(
                f"[FPS {target:2d} #{trial}] thuc={result.actual_fps:4.1f} | "
                f"p95={result.p95_gap_ms:5.0f} ms | max={result.max_gap_ms:5.0f} ms | "
                f"jitter={result.jitter_percent:4.0f}% | stall={result.stalls} | "
                f"JPEG={result.average_jpeg_bytes / 1024:4.1f} KB | {status}"
            )
            if not wait_for_stream_idle(args.host):
                print("[CANH BAO] Stream cu chua dong han; cho them truoc phep do tiep theo.")
                time.sleep(2.0)

    selected = choose_fps(measurements, args.max_useful_fps)
    set_target_fps(args.host, selected.target_fps)
    print(
        f"[KET QUA] Chon {selected.target_fps} FPS; firmware da duoc dat ve muc nay "
        "cho den lan khoi dong lai."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
