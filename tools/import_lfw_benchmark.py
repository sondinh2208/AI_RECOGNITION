"""Nhập gallery LFW benchmark vào cơ sở dữ liệu nhận diện FaceCheck."""

from __future__ import annotations

import argparse
import json
import pickle
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


EMPLOYEE_PROFILES = (
    ("Kỹ sư phần mềm", "Phòng IT"),
    ("Kỹ sư hệ thống", "Phòng IT"),
    ("Chuyên viên dữ liệu", "Phòng IT"),
    ("Chuyên viên nhân sự", "Phòng Nhân sự"),
    ("Chuyên viên tuyển dụng", "Phòng Nhân sự"),
    ("Kế toán viên", "Phòng Tài chính"),
    ("Chuyên viên tài chính", "Phòng Tài chính"),
    ("Chuyên viên marketing", "Phòng Marketing"),
    ("Chuyên viên kinh doanh", "Phòng Kinh doanh"),
    ("Nhân viên hành chính", "Phòng Hành chính"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nhập 90 hồ sơ LFW vào FaceCheck")
    parser.add_argument(
        "--benchmark", type=Path, default=Path("data/benchmark_faces"),
    )
    parser.add_argument("--prefix", default="LFW")
    parser.add_argument(
        "--replace", action="store_true",
        help="Thay thế an toàn các hồ sơ benchmark đã nhập trước đó.",
    )
    return parser.parse_args()


def safe_component(value: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*@]', "", value.strip())
    value = re.sub(r"\s+", "_", value)
    return value.strip("_") or fallback


def save_embeddings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pkl.tmp")
    with temporary.open("wb") as file:
        pickle.dump(data, file)
    temporary.replace(path)


def load_face_detector():
    model_path = Path("models/blaze_face_short_range.tflite").resolve()
    options = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        min_detection_confidence=0.45,
    )
    return mp_vision.FaceDetector.create_from_options(options)


def crop_single_face(image, detector):
    """Crop vùng mặt với padding 10%, tương ứng luồng đăng ký của ứng dụng."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if len(result.detections) != 1:
        raise ValueError(f"Ảnh đăng ký có {len(result.detections)} khuôn mặt")
    box = result.detections[0].bounding_box
    height, width = image.shape[:2]
    pad_x = int(box.width * 0.1)
    pad_y = int(box.height * 0.1)
    x1 = max(0, box.origin_x - pad_x)
    y1 = max(0, box.origin_y - pad_y)
    x2 = min(width, box.origin_x + box.width + pad_x)
    y2 = min(height, box.origin_y + box.height + pad_y)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("Vùng crop khuôn mặt rỗng")
    return crop


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    benchmark = args.benchmark.resolve()
    manifest_path = benchmark / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Không tìm thấy manifest: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    enrolled = manifest.get("enrolled", {})
    if not enrolled:
        raise SystemExit("Manifest không có danh tính enrolled")

    embeddings_path = Path("data/embeddings.pkl")
    if embeddings_path.exists():
        with embeddings_path.open("rb") as file:
            embeddings = pickle.load(file)
    else:
        embeddings = {}

    from deepface import DeepFace
    detector = load_face_detector()

    destinations = (Path("data/faces"), Path("images"), Path("database/images"))
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)

    imported = 0
    skipped = 0
    total = len(enrolled)
    for index, (person_id, info) in enumerate(sorted(enrolled.items()), 1):
        employee_id = f"{args.prefix}{index:03d}"
        source_name = info.get("source_identity") or person_id
        employee_name = source_name.replace("_", " ").strip()
        role, department = EMPLOYEE_PROFILES[(index - 1) % len(EMPLOYEE_PROFILES)]
        source_image = benchmark / "enrolled" / person_id / "register.jpg"
        if not source_image.exists():
            print(f"[{index:02d}/{total}] Bỏ qua {employee_id}: thiếu register.jpg")
            skipped += 1
            continue

        existing_key = next(
            (
                key for key in embeddings
                if str(key).strip().casefold() == employee_id.casefold()
            ),
            None,
        )
        recognition_enabled = False
        if existing_key is not None:
            existing = embeddings[existing_key]
            if existing.get("source") == "LFW benchmark":
                recognition_enabled = bool(existing.get("recognition_enabled", False))
                if not args.replace:
                    print(f"[{index:02d}/{total}] Đã có {employee_id}, bỏ qua")
                    skipped += 1
                    continue
            else:
                raise RuntimeError(
                    f"Mã {employee_id} đã thuộc hồ sơ thật; dừng để tránh ghi đè."
                )

        image = cv2.imread(str(source_image))
        if image is None:
            print(f"[{index:02d}/{total}] Bỏ qua {employee_id}: ảnh không đọc được")
            skipped += 1
            continue

        face_crop = crop_single_face(image, detector)
        representations = DeepFace.represent(
            img_path=face_crop,
            model_name="ArcFace",
            detector_backend="skip",
            enforce_detection=False,
        )
        if not representations:
            print(f"[{index:02d}/{total}] Bỏ qua {employee_id}: không tạo được embedding")
            skipped += 1
            continue
        vector = representations[0].get("embedding")
        if vector is None:
            print(f"[{index:02d}/{total}] Bỏ qua {employee_id}: embedding rỗng")
            skipped += 1
            continue

        safe_name = safe_component(employee_name, person_id)
        safe_role = safe_component(role, "Nhan_vien")
        safe_department = safe_component(department, "Phong_ban")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = (
            f"{employee_id}@{safe_role}@{safe_department}@{safe_name}_"
            f"{timestamp}.jpg"
        )

        created_files = []
        try:
            for destination in destinations:
                target = destination / filename
                shutil.copy2(source_image, target)
                created_files.append(target)

            embeddings[employee_id] = {
                "id": employee_id,
                "name": employee_name,
                "role": role,
                "department": department,
                "embedding": vector,
                "image_path": str(Path("data/faces") / filename),
                "timestamp": timestamp,
                "source": "LFW benchmark",
                "benchmark_identity": person_id,
                "recognition_enabled": recognition_enabled,
            }
            save_embeddings(embeddings_path, embeddings)

            # Chỉ dọn ảnh benchmark cũ sau khi hồ sơ mới đã được ghi bền vững.
            if existing_key is not None and args.replace:
                created_set = {target.resolve() for target in created_files}
                for destination in destinations:
                    for old_file in destination.glob(f"{employee_id}@*.jpg"):
                        if old_file.resolve() not in created_set:
                            old_file.unlink(missing_ok=True)
        except Exception:
            embeddings.pop(employee_id, None)
            for target in created_files:
                target.unlink(missing_ok=True)
            raise

        imported += 1
        print(
            f"[{index:02d}/{total}] {employee_id} · {employee_name} · "
            f"{role} · {department}"
        )

    detector.close()
    print(f"Hoàn tất: nhập mới {imported}, bỏ qua {skipped}, tổng cache {len(embeddings)}")


if __name__ == "__main__":
    main()
