"""Hiệu chỉnh threshold và top-2 margin cho gallery ArcFace của FaceCheck."""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hiệu chỉnh quyết định nhận diện ArcFace")
    parser.add_argument("--benchmark", type=Path, default=Path("data/benchmark_faces"))
    parser.add_argument("--embeddings", type=Path, default=Path("data/embeddings.pkl"))
    parser.add_argument(
        "--cache", type=Path,
        default=Path("data/benchmark_faces/evaluation_embeddings.pkl"),
    )
    parser.add_argument(
        "--report", type=Path,
        default=Path("data/benchmark_faces/calibration_report.json"),
    )
    parser.add_argument("--rebuild-cache", action="store_true")
    return parser.parse_args()


def normalized(vector) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("Embedding có norm bằng 0")
    return value / norm


def percentile(values: list[float], points=(0, 25, 50, 75, 90, 95, 99, 100)) -> dict:
    if not values:
        return {}
    data = np.asarray(values, dtype=np.float32)
    return {str(point): round(float(np.percentile(data, point)), 6) for point in points}


def load_face_detector():
    model_path = Path("models/blaze_face_short_range.tflite").resolve()
    options = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        min_detection_confidence=0.45,
    )
    return mp_vision.FaceDetector.create_from_options(options)


def crop_single_face(image, detector):
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if len(result.detections) != 1:
        raise ValueError(f"Ảnh kiểm thử có {len(result.detections)} khuôn mặt")
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


def build_probe_cache(benchmark: Path, manifest: dict, cache_path: Path) -> list[dict]:
    from deepface import DeepFace

    probes = []
    detector = load_face_detector()
    jobs = []
    for index, (person_id, info) in enumerate(sorted(manifest["enrolled"].items()), 1):
        for test in info["tests"]:
            jobs.append({
                "kind": "known",
                "expected_id": f"LFW{index:03d}",
                "person_id": person_id,
                "path": benchmark / "enrolled" / person_id / "tests" / test["file"],
            })
    for person_id, info in sorted(manifest["unknown"].items()):
        for test in info["tests"]:
            jobs.append({
                "kind": "unknown",
                "expected_id": None,
                "person_id": person_id,
                "path": benchmark / "unknown" / person_id / test["file"],
            })

    for number, job in enumerate(jobs, 1):
        image = cv2.imread(str(job["path"]))
        if image is None:
            raise RuntimeError(f"Không đọc được ảnh: {job['path']}")
        face_crop = crop_single_face(image, detector)
        result = DeepFace.represent(
            img_path=face_crop,
            model_name="ArcFace",
            detector_backend="skip",
            enforce_detection=False,
        )
        if not result or result[0].get("embedding") is None:
            raise RuntimeError(f"Không tạo được embedding: {job['path']}")
        probes.append({
            "kind": job["kind"],
            "expected_id": job["expected_id"],
            "person_id": job["person_id"],
            "path": str(job["path"]),
            "embedding": result[0]["embedding"],
        })
        if number % 25 == 0 or number == len(jobs):
            print(f"Đã trích xuất {number}/{len(jobs)} ảnh kiểm thử", flush=True)

    detector.close()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".pkl.tmp")
    with temporary.open("wb") as file:
        pickle.dump(probes, file)
    temporary.replace(cache_path)
    return probes


def evaluate(samples: list[dict], threshold: float, margin: float) -> dict:
    counters = {
        "known_total": 0,
        "correct_accept": 0,
        "wrong_identity": 0,
        "false_reject": 0,
        "unknown_total": 0,
        "false_accept": 0,
        "correct_reject": 0,
    }
    for sample in samples:
        accepted = sample["best_distance"] <= threshold and sample["margin"] >= margin
        if sample["kind"] == "known":
            counters["known_total"] += 1
            if not accepted:
                counters["false_reject"] += 1
            elif sample["predicted_id"] == sample["expected_id"]:
                counters["correct_accept"] += 1
            else:
                counters["wrong_identity"] += 1
        else:
            counters["unknown_total"] += 1
            if accepted:
                counters["false_accept"] += 1
            else:
                counters["correct_reject"] += 1

    known = max(1, counters["known_total"])
    unknown = max(1, counters["unknown_total"])
    counters.update({
        "threshold": round(float(threshold), 3),
        "margin": round(float(margin), 3),
        "true_accept_rate": round(counters["correct_accept"] / known, 6),
        "wrong_identity_rate": round(counters["wrong_identity"] / known, 6),
        "false_reject_rate": round(counters["false_reject"] / known, 6),
        "false_accept_rate": round(counters["false_accept"] / unknown, 6),
    })
    return counters


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    benchmark = args.benchmark.resolve()
    with (benchmark / "manifest.json").open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    with args.embeddings.open("rb") as file:
        profiles = pickle.load(file)

    gallery_ids = [f"LFW{index:03d}" for index in range(1, 91)]
    missing = [emp_id for emp_id in gallery_ids if emp_id not in profiles]
    if missing:
        raise RuntimeError(f"Thiếu hồ sơ gallery: {', '.join(missing)}")
    gallery = np.stack([normalized(profiles[emp_id]["embedding"]) for emp_id in gallery_ids])

    if args.cache.exists() and not args.rebuild_cache:
        with args.cache.open("rb") as file:
            probes = pickle.load(file)
        print(f"Dùng cache {len(probes)} embedding kiểm thử.")
    else:
        probes = build_probe_cache(benchmark, manifest, args.cache)

    samples = []
    genuine_distances = []
    unknown_best_distances = []
    top2_margins = []
    for probe in probes:
        query = normalized(probe["embedding"])
        distances = 1.0 - gallery @ query
        order = np.argsort(distances)[:2]
        best_index, second_index = int(order[0]), int(order[1])
        best_distance = float(distances[best_index])
        second_distance = float(distances[second_index])
        sample = {
            "kind": probe["kind"],
            "expected_id": probe["expected_id"],
            "predicted_id": gallery_ids[best_index],
            "best_distance": best_distance,
            "second_distance": second_distance,
            "margin": second_distance - best_distance,
            "path": probe["path"],
        }
        samples.append(sample)
        top2_margins.append(sample["margin"])
        if probe["kind"] == "known":
            expected_index = gallery_ids.index(probe["expected_id"])
            genuine_distances.append(float(distances[expected_index]))
        else:
            unknown_best_distances.append(best_distance)

    results = []
    for threshold in np.arange(0.30, 0.7001, 0.005):
        for margin in np.arange(0.0, 0.2001, 0.005):
            results.append(evaluate(samples, float(threshold), float(margin)))

    secure = [
        result for result in results
        if result["wrong_identity"] == 0 and result["false_accept"] == 0
    ]
    if secure:
        recommended = max(
            secure,
            key=lambda item: (
                item["correct_accept"], item["margin"], -item["threshold"]
            ),
        )
        selection = "zero_wrong_identity_and_zero_false_accept_then_maximize_correct_accept"
    else:
        recommended = min(
            results,
            key=lambda item: (
                item["wrong_identity"] + item["false_accept"],
                -item["correct_accept"],
            ),
        )
        selection = "minimize_dangerous_errors_then_maximize_correct_accept"

    baseline = evaluate(samples, 0.68, 0.0)
    accepted_errors = [
        sample for sample in samples
        if sample["best_distance"] <= recommended["threshold"]
        and sample["margin"] >= recommended["margin"]
        and (
            sample["kind"] == "unknown"
            or sample["predicted_id"] != sample["expected_id"]
        )
    ]
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": "ArcFace",
        "metric": "cosine_distance",
        "gallery_size": len(gallery_ids),
        "known_probes": sum(item["kind"] == "known" for item in samples),
        "unknown_probes": sum(item["kind"] == "unknown" for item in samples),
        "selection_policy": selection,
        "baseline": baseline,
        "recommended": recommended,
        "distributions": {
            "genuine_distance": percentile(genuine_distances),
            "unknown_best_distance": percentile(unknown_best_distances),
            "top2_margin_all": percentile(top2_margins),
        },
        "dangerous_errors_at_recommended": accepted_errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print("\nBaseline threshold=0.68, margin=0:")
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    print("\nCấu hình đề xuất:")
    print(json.dumps(recommended, ensure_ascii=False, indent=2))
    print(f"\nĐã lưu báo cáo: {args.report}")


if __name__ == "__main__":
    main()
