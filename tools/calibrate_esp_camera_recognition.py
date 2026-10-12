"""Calibrate FaceCheck recognition after QVGA/JPEG camera degradation."""

from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision


BENCHMARK = Path("data/benchmark_faces")
MANIFEST = BENCHMARK / "manifest.json"
CACHE = BENCHMARK / "esp_qvga_evaluation_embeddings.pkl"
REPORT = BENCHMARK / "esp_qvga_calibration_report.json"
TARGET_JPEG_BYTES = 4045
FAST_THRESHOLD = 0.400
FAST_MARGIN = 0.120


def normalized(vector):
    value = np.asarray(vector, dtype=np.float32)
    return value / np.linalg.norm(value)


def camera_frame(image, jpeg_quality):
    height, width = image.shape[:2]
    scale = min(320 / width, 240 / height)
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    y = (240 - resized.shape[0]) // 2
    x = (320 - resized.shape[1]) // 2
    frame[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    ok, encoded = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    )
    if not ok:
        raise RuntimeError("JPEG encode failed")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return decoded, int(encoded.nbytes)


def detector():
    options = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(
            model_asset_path=str(Path("models/blaze_face_short_range.tflite").resolve())
        ),
        min_detection_confidence=0.45,
    )
    return mp_vision.FaceDetector.create_from_options(options)


def face_crop(image, face_detector):
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = face_detector.detect(
        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    )
    if len(result.detections) != 1:
        return None
    box = result.detections[0].bounding_box
    pad_x, pad_y = int(box.width * 0.1), int(box.height * 0.1)
    x1, y1 = max(0, box.origin_x - pad_x), max(0, box.origin_y - pad_y)
    x2 = min(image.shape[1], box.origin_x + box.width + pad_x)
    y2 = min(image.shape[0], box.origin_y + box.height + pad_y)
    crop = image[y1:y2, x1:x2]
    return crop if crop.size else None


def all_jobs(manifest):
    jobs = []
    for index, (person, info) in enumerate(sorted(manifest["enrolled"].items()), 1):
        identity = f"LFW{index:03d}"
        jobs.append(("gallery", identity, BENCHMARK / "enrolled" / person / "register.jpg"))
        for order, test in enumerate(info["tests"]):
            jobs.append(("known", identity, BENCHMARK / "enrolled" / person / "tests" / test["file"], order))
    for person, info in sorted(manifest["unknown"].items()):
        for order, test in enumerate(info["tests"]):
            jobs.append(("unknown", person, BENCHMARK / "unknown" / person / test["file"], order))
    return jobs


def choose_jpeg_quality(paths):
    sample = paths[::max(1, len(paths) // 80)][:80]
    choices = []
    for quality in range(15, 66, 5):
        sizes = []
        for path in sample:
            image = cv2.imread(str(path))
            if image is not None:
                _, size = camera_frame(image, quality)
                sizes.append(size)
        mean_size = float(np.mean(sizes))
        choices.append((abs(mean_size - TARGET_JPEG_BYTES), quality, mean_size))
    return min(choices), choices


def build_cache(manifest, jpeg_quality):
    from deepface import DeepFace

    face_detector = detector()
    records, failures, sizes = [], [], []
    jobs = all_jobs(manifest)
    for number, job in enumerate(jobs, 1):
        kind, identity, path, *rest = job
        image = cv2.imread(str(path))
        degraded, size = camera_frame(image, jpeg_quality)
        sizes.append(size)
        crop = face_crop(degraded, face_detector)
        if crop is None:
            failures.append({"kind": kind, "identity": identity, "path": str(path)})
            continue
        reps = DeepFace.represent(
            img_path=crop,
            model_name="ArcFace",
            detector_backend="skip",
            enforce_detection=False,
        )
        records.append({
            "kind": kind,
            "identity": identity,
            "order": rest[0] if rest else 0,
            "path": str(path),
            "embedding": reps[0]["embedding"],
        })
        if number % 25 == 0 or number == len(jobs):
            print(f"[EMBED] {number}/{len(jobs)}", flush=True)
    face_detector.close()
    with CACHE.open("wb") as file:
        pickle.dump({"jpeg_quality": jpeg_quality, "records": records, "failures": failures, "sizes": sizes}, file)
    return records, failures, sizes


def session_result(samples, threshold, margin):
    candidate = None
    strict = []
    for sample in sorted(samples, key=lambda item: item["order"]):
        eligible = sample["distance"] <= threshold and sample["margin"] >= margin
        if not eligible:
            candidate, strict = None, []
            continue
        if candidate != sample["predicted"]:
            candidate, strict = sample["predicted"], []
        strict.append(sample)
        if len(strict) >= 2:
            recent = strict[-2:]
            if (
                np.median([item["distance"] for item in recent]) <= FAST_THRESHOLD
                and np.median([item["margin"] for item in recent]) >= FAST_MARGIN
            ):
                return candidate
        if len(strict) >= 3:
            recent = strict[-3:]
            if (
                np.median([item["distance"] for item in recent]) <= threshold
                and np.median([item["margin"] for item in recent]) >= margin
            ):
                return candidate
    return None


def evaluate(grouped, threshold, margin):
    counts = {"known_correct": 0, "known_wrong": 0, "known_reject": 0,
              "unknown_false_accept": 0, "unknown_reject": 0}
    for (kind, identity), samples in grouped.items():
        result = session_result(samples, threshold, margin)
        if kind == "known":
            if result is None:
                counts["known_reject"] += 1
            elif result == identity:
                counts["known_correct"] += 1
            else:
                counts["known_wrong"] += 1
        elif result is None:
            counts["unknown_reject"] += 1
        else:
            counts["unknown_false_accept"] += 1
    counts.update({"threshold": round(threshold, 3), "margin": round(margin, 3)})
    return counts


def evaluate_aggregated(grouped, gallery_ids, matrix, threshold, margin):
    """Evaluate the production policy: average two consecutive embeddings first."""
    counts = {"known_correct": 0, "known_wrong": 0, "known_reject": 0,
              "unknown_false_accept": 0, "unknown_reject": 0}
    for (kind, identity), samples in grouped.items():
        ordered = sorted(samples, key=lambda item: item["order"])
        result = None
        for index in range(len(ordered) - 1):
            query = normalized(np.mean(np.stack([
                normalized(item["embedding"])
                for item in ordered[index:index + 2]
            ]), axis=0))
            distances = 1.0 - matrix @ query
            ranking = np.argsort(distances)[:2]
            if (
                distances[ranking[0]] <= threshold
                and distances[ranking[1]] - distances[ranking[0]] >= margin
            ):
                result = gallery_ids[int(ranking[0])]
                break
        if kind == "known":
            if result is None:
                counts["known_reject"] += 1
            elif result == identity:
                counts["known_correct"] += 1
            else:
                counts["known_wrong"] += 1
        elif result is None:
            counts["unknown_reject"] += 1
        else:
            counts["unknown_false_accept"] += 1
    counts.update({"threshold": round(threshold, 3), "margin": round(margin, 3)})
    return counts


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    jobs = all_jobs(manifest)
    selected, candidates = choose_jpeg_quality([job[2] for job in jobs])
    _, jpeg_quality, calibrated_mean = selected
    print(f"[JPEG] OpenCV q{jpeg_quality}, mean={calibrated_mean:.0f} B, target={TARGET_JPEG_BYTES} B")
    if CACHE.exists():
        cached = pickle.loads(CACHE.read_bytes())
    else:
        cached = None
    if cached and cached.get("jpeg_quality") == jpeg_quality:
        records, failures, sizes = cached["records"], cached["failures"], cached["sizes"]
        print(f"[CACHE] {len(records)} embeddings")
    else:
        records, failures, sizes = build_cache(manifest, jpeg_quality)

    gallery = {item["identity"]: normalized(item["embedding"])
               for item in records if item["kind"] == "gallery"}
    gallery_ids = sorted(gallery)
    matrix = np.stack([gallery[key] for key in gallery_ids])
    grouped = {}
    for item in records:
        if item["kind"] == "gallery" or item["kind"] == "known" and item["identity"] not in gallery:
            continue
        query = normalized(item["embedding"])
        distances = 1.0 - matrix @ query
        order = np.argsort(distances)[:2]
        sample = dict(item)
        sample.update({
            "predicted": gallery_ids[int(order[0])],
            "distance": float(distances[order[0]]),
            "margin": float(distances[order[1]] - distances[order[0]]),
        })
        grouped.setdefault((item["kind"], item["identity"]), []).append(sample)

    results = []
    for threshold in np.arange(0.40, 0.551, 0.005):
        for margin in np.arange(0.05, 0.151, 0.005):
            results.append(evaluate(grouped, float(threshold), float(margin)))
    secure = [item for item in results if item["known_wrong"] == 0 and item["unknown_false_accept"] == 0]
    recommended = max(secure, key=lambda item: (item["known_correct"], item["margin"], -item["threshold"]))
    current = evaluate(grouped, 0.480, 0.075)
    aggregated = evaluate_aggregated(grouped, gallery_ids, matrix, 0.460, 0.120)
    report = {
        "camera_profile": {"resolution": "320x240", "sensor_jpeg_quality": 30,
                           "measured_jpeg_mean_bytes": TARGET_JPEG_BYTES},
        "simulation": {"opencv_jpeg_quality": jpeg_quality,
                       "mean_jpeg_bytes": round(float(np.mean(sizes))),
                       "gallery_identities": len(gallery), "embedding_records": len(records),
                       "detection_failures": len(failures)},
        "current": current,
        "recommended": recommended,
        "production_two_frame_aggregation": aggregated,
        "jpeg_candidates": [{"quality": q, "mean_bytes": round(mean)} for _, q, mean in candidates],
        "failures": failures,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
