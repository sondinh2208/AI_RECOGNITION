"""Tải và chuẩn bị bộ benchmark khuôn mặt tách biệt dữ liệu sản phẩm.

Ảnh đăng ký được chọn tự động theo độ chính diện, độ nét, ánh sáng và vị trí
khuôn mặt. Các ảnh còn lại được lấy trải đều từ dễ đến khó để làm probe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


LFW_URL = "https://ndownloader.figshare.com/files/5976015"
LFW_SHA256 = "b47c8422c8cded889dc5a13418c4bc2abbda121092b3533a83306f90d900100a"
MANUAL_GALLERY_EXCLUSIONS = {
    # Landmark vẫn hợp lệ nhưng ảnh tốt nhất bị bàn tay che vùng nhận dạng.
    "Jose_Manuel_Durao_Barroso",
    "John_Ashcroft",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chuẩn bị benchmark LFW cho FaceCheck")
    parser.add_argument("--output", type=Path, default=Path("data/benchmark_faces"))
    parser.add_argument("--enrolled", type=int, default=90)
    parser.add_argument("--unknown", type=int, default=30)
    parser.add_argument("--probes", type=int, default=5)
    parser.add_argument("--unknown-probes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "FaceCheck-Benchmark/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as file:
        total = int(response.headers.get("Content-Length", "0"))
        received = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)
            received += len(chunk)
            if total:
                print(f"\rĐang tải LFW: {received / total:6.1%}", end="", flush=True)
    print()


def safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise RuntimeError(f"Đường dẫn không an toàn trong archive: {member.name}")
        tar.extractall(destination)


def load_face_detector():
    model_path = Path("models/blaze_face_short_range.tflite")
    if not model_path.exists():
        raise RuntimeError(f"Không tìm thấy MediaPipe model: {model_path}")
    options = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path.resolve())),
        min_detection_confidence=0.45,
    )
    return mp_vision.FaceDetector.create_from_options(options)


def image_quality(path: Path, detector) -> float | None:
    """Chấm chất lượng khi và chỉ khi ảnh chứa đúng một khuôn mặt."""
    image = cv2.imread(str(path))
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if len(result.detections) != 1:
        return None

    detection = result.detections[0]
    box = detection.bounding_box
    x, y, fw, fh = box.origin_x, box.origin_y, box.width, box.height
    center_error = math.hypot(
        (x + fw / 2 - width / 2) / width,
        (y + fh / 2 - height / 2) / height,
    )
    face_ratio = (fw * fh) / float(width * height)
    exposure = max(0.0, 1.0 - abs(brightness - 132.0) / 132.0)
    clarity = min(2.5, math.log1p(sharpness) / 3.0)
    scale_score = max(0.0, 1.0 - abs(face_ratio - 0.30) / 0.30)

    eye_score = 0.0
    pose_score = -6.0
    if len(detection.keypoints) >= 4:
        eyes = sorted(detection.keypoints[:2], key=lambda point: point.x)
        left, right = eyes[0], eyes[1]
        nose, mouth = detection.keypoints[2], detection.keypoints[3]
        separation = abs(right.x - left.x) * width / max(1.0, fw)
        tilt = abs(right.y - left.y) * height / max(1.0, fh)
        roll_degrees = math.degrees(
            math.atan2((right.y - left.y) * height, (right.x - left.x) * width)
        )
        eye_score = 2.0 + min(1.0, separation * 2.0) - min(1.5, tilt * 8.0)

        nose_left = math.hypot(nose.x - left.x, nose.y - left.y)
        nose_right = math.hypot(nose.x - right.x, nose.y - right.y)
        eye_y = (left.y + right.y) / 2.0
        eye_nose = nose.y - eye_y
        nose_mouth = mouth.y - nose.y
        if nose_left > 0 and nose_right > 0 and eye_nose > 0 and nose_mouth > 0:
            yaw_ratio = nose_left / nose_right
            pitch_ratio = eye_nose / nose_mouth
            yaw_error = abs(math.log(yaw_ratio))
            pitch_error = abs(math.log(pitch_ratio / 1.05))
            pose_score = 4.0 - min(8.0, yaw_error * 12.0) - min(6.0, pitch_error * 5.0)

            # Loại mạnh ảnh không đạt chính diện theo cùng giới hạn KYC của app.
            if yaw_ratio < 0.88 or yaw_ratio > 1.14:
                pose_score -= 30.0
            if pitch_ratio < 0.80 or pitch_ratio > 1.40:
                pose_score -= 20.0
            if abs(roll_degrees) > 7.0:
                pose_score -= 20.0

            # Hai điểm tragion (gần tai) ổn định hơn mắt khi người trong ảnh
            # nhìn sang bên nhưng đầu vẫn gần chính diện.
            if len(detection.keypoints) >= 6:
                ears = sorted(detection.keypoints[4:6], key=lambda point: point.x)
                ear_left, ear_right = ears[0], ears[1]
                nose_ear_left = math.hypot(nose.x - ear_left.x, nose.y - ear_left.y)
                nose_ear_right = math.hypot(nose.x - ear_right.x, nose.y - ear_right.y)
                if nose_ear_left > 0 and nose_ear_right > 0:
                    ear_ratio = nose_ear_left / nose_ear_right
                    pose_score -= min(8.0, abs(math.log(ear_ratio)) * 8.0)
                    if ear_ratio < 0.78 or ear_ratio > 1.28:
                        pose_score -= 35.0

    confidence = detection.categories[0].score if detection.categories else 0.0
    return (
        5.0 + clarity + exposure + scale_score + eye_score + pose_score
        + confidence * 2.0 - center_error * 4.0
    )


def evenly_spaced(items: list[tuple[Path, float]], count: int) -> list[tuple[Path, float]]:
    if len(items) <= count:
        return items
    ordered = sorted(items, key=lambda item: item[1])
    indices = [round(i * (len(ordered) - 1) / (count - 1)) for i in range(count)]
    return [ordered[index] for index in indices]


def copy_identity(
    source_dir: Path,
    destination: Path,
    detector,
    probe_count: int,
    include_register: bool,
) -> dict:
    scored = [
        (path, score)
        for path in sorted(source_dir.glob("*.jpg"))
        if (score := image_quality(path, detector)) is not None
    ]
    required = probe_count + (1 if include_register else 0)
    if len(scored) < required:
        raise ValueError(
            f"{source_dir.name} chỉ có {len(scored)} ảnh đúng một khuôn mặt; "
            f"cần tối thiểu {required}"
        )
    scored.sort(key=lambda item: item[1], reverse=True)
    destination.mkdir(parents=True, exist_ok=True)

    manifest = {"source_identity": source_dir.name, "register": None, "tests": []}
    remaining = scored
    if include_register:
        register, register_score = scored[0]
        shutil.copy2(register, destination / "register.jpg")
        manifest["register"] = {
            "source": register.name,
            "quality_score": round(register_score, 4),
        }
        remaining = scored[1:]

    tests_dir = destination / "tests" if include_register else destination
    tests_dir.mkdir(parents=True, exist_ok=True)
    for index, (source, score) in enumerate(evenly_spaced(remaining, probe_count), 1):
        target_name = f"test_{index:02d}.jpg"
        shutil.copy2(source, tests_dir / target_name)
        manifest["tests"].append({
            "file": target_name,
            "source": source.name,
            "quality_score": round(score, 4),
        })
    return manifest


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    output = args.output.resolve()
    prepared_paths = (output / "enrolled", output / "unknown", output / "manifest.json")
    if any(path.exists() for path in prepared_paths) and not args.force:
        raise SystemExit(f"{output} đã có dữ liệu. Dùng --force nếu muốn tạo lại.")
    if args.force:
        for path in prepared_paths:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    output.mkdir(parents=True, exist_ok=True)

    cache_dir = output / ".cache"
    cache_dir.mkdir(exist_ok=True)
    archive = cache_dir / "lfw-funneled.tgz"
    if not archive.exists() or sha256(archive) != LFW_SHA256:
        print("Tải bộ LFW funneled từ bản sao được scikit-learn xác minh...")
        download(LFW_URL, archive)
    else:
        print("Dùng archive LFW đã có trong cache.")
    actual_hash = sha256(archive)
    if actual_hash != LFW_SHA256:
        raise RuntimeError(f"Checksum LFW không đúng: {actual_hash}")

    with tempfile.TemporaryDirectory(prefix="facecheck_lfw_") as temp_name:
        temp = Path(temp_name)
        print("Checksum hợp lệ. Đang giải nén...")
        safe_extract(archive, temp / "extracted")

        source_root = temp / "extracted" / "lfw_funneled"
        identities = [path for path in source_root.iterdir() if path.is_dir()]
        enrolled_candidates_raw = [
            path for path in identities
            if len(list(path.glob("*.jpg"))) >= args.probes + 1
            and path.name not in MANUAL_GALLERY_EXCLUSIONS
        ]

        detector = load_face_detector()
        print("Đang loại ảnh không có hoặc có nhiều khuôn mặt...")
        score_cache = {}

        def valid_scores(identity_dir: Path) -> list[tuple[Path, float]]:
            if identity_dir not in score_cache:
                items = []
                for image_path in identity_dir.glob("*.jpg"):
                    score = image_quality(image_path, detector)
                    if score is not None:
                        items.append((image_path, score))
                score_cache[identity_dir] = items
            return score_cache[identity_dir]

        enrolled_candidates = [
            path for path in enrolled_candidates_raw
            if len(valid_scores(path)) >= args.probes + 1
        ]

        print("Đang xếp hạng ảnh chính diện của các danh tính hợp lệ...")
        ranked_candidates = []
        for candidate in enrolled_candidates:
            best_score = max(score for _, score in valid_scores(candidate))
            ranked_candidates.append((candidate, best_score))
        ranked_candidates.sort(key=lambda item: item[1], reverse=True)
        selected_enrolled = [item[0] for item in ranked_candidates[:args.enrolled]]

        randomizer = random.Random(args.seed)
        selected_names = {path.name for path in selected_enrolled}
        unknown_pool = [path for path in identities if path.name not in selected_names]
        unknown_pool = [
            path for path in unknown_pool
            if len(list(path.glob("*.jpg"))) >= args.unknown_probes
            and len(valid_scores(path)) >= args.unknown_probes
        ]
        randomizer.shuffle(unknown_pool)
        selected_unknown = unknown_pool[:args.unknown]

        if len(selected_enrolled) < args.enrolled or len(selected_unknown) < args.unknown:
            raise RuntimeError("LFW không đủ danh tính theo cấu hình đã yêu cầu")

        manifest = {
            "dataset": "Labeled Faces in the Wild - funneled",
            "source": LFW_URL,
            "sha256": LFW_SHA256,
            "seed": args.seed,
            "usage": "Internal research/testing only; do not ship with the product.",
            "face_count_policy": "Every image contains exactly one detected face.",
            "enrolled": {},
            "unknown": {},
        }

        print(f"Đang chọn ảnh cho {args.enrolled} người đăng ký...")
        for index, source in enumerate(selected_enrolled, 1):
            identity = f"person_{index:03d}"
            manifest["enrolled"][identity] = copy_identity(
                source, output / "enrolled" / identity,
                detector, args.probes, True,
            )

        print(f"Đang chọn ảnh cho {args.unknown} người lạ...")
        for offset, source in enumerate(selected_unknown, args.enrolled + 1):
            identity = f"person_{offset:03d}"
            manifest["unknown"][identity] = copy_identity(
                source, output / "unknown" / identity,
                detector, args.unknown_probes, False,
            )

        detector.close()

        with (output / "manifest.json").open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)

    enrolled_images = args.enrolled * (args.probes + 1)
    unknown_images = args.unknown * args.unknown_probes
    print(f"Hoàn tất: {enrolled_images + unknown_images} ảnh tại {output}")
    print(f"- Enrolled: {args.enrolled} người, 1 register + {args.probes} test/người")
    print(f"- Unknown: {args.unknown} người, {args.unknown_probes} test/người")


if __name__ == "__main__":
    main()
