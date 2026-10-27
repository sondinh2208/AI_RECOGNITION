"""Offline look-alike test using fresh embeddings and an isolated LFW gallery.

Never imports profiles into the live database or calls door/Telegram services.
Photo pairs are different views of one labelled identity, not live video frames.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_engine import (  # noqa: E402
    check_gpu_quick, detect_faces, load_face_model, load_mediapipe_detector,
    validate_kiosk_face_candidate,
)
from config import (  # noqa: E402
    ARCFACE_THRESHOLD, ARCFACE_TOP2_MARGIN, FACE_CROP_PADDING_RATIO, KIOSK_MIN_FACE_WIDTH,
    KIOSK_PRIMARY_FACE_MIN_AREA_RATIO,
)
from mixins.attendance import AttendanceMixin  # noqa: E402
from recognition_policy import (  # noqa: E402
    aggregate_query_embeddings, calculate_profile_distance, match_face_profiles,
)


def extract(path, runtime, model, device, *, check_quality=False):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("image_unreadable")
    faces = detect_faces(model, image, device)
    if not faces:
        raise ValueError("no_face_detected")
    # Match live selection: a clearly larger primary face may be selected.
    faces.sort(key=lambda face: (face[2]-face[0]) * (face[3]-face[1]), reverse=True)
    if len(faces) > 1:
        area = lambda face: (face[2]-face[0]) * (face[3]-face[1])
        if area(faces[0]) < KIOSK_PRIMARY_FACE_MIN_AREA_RATIO * area(faces[1]):
            raise ValueError("ambiguous_primary_face")
    x1, y1, x2, y2, confidence = faces[0]
    quality = (True, "not_checked")
    if check_quality:
        if x2 - x1 < KIOSK_MIN_FACE_WIDTH:
            quality = (False, "face_too_small")
        else:
            quality = validate_kiosk_face_candidate(
                (x1, y1, x2, y2), confidence, image, runtime.face_detector
            )
    height, width = image.shape[:2]
    px, py = int((x2 - x1) * FACE_CROP_PADDING_RATIO), int((y2 - y1) * FACE_CROP_PADDING_RATIO)
    crop = image[max(0, y1-py):min(height, y2+py), max(0, x1-px):min(width, x2+px)]
    embedding = runtime._extract_face_embedding(crop)
    if embedding is None:
        raise ValueError("embedding_missing")
    return embedding, {"passed": bool(quality[0]), "message": quality[1]}


def describe_match(query, gallery, expected_id):
    match = match_face_profiles(query, gallery)
    predicted = match.best_profile["id"] if match.best_profile else None
    outcome = "rejected"
    if match.decision == "matched":
        outcome = "correct" if predicted == expected_id else "wrong_identity"
    return {
        "predicted_id": predicted, "outcome": outcome,
        "distance": match.best_distance, "margin": match.margin,
        "decision": match.decision,
        "second_id": match.second_profile["id"] if match.second_profile else None,
    }


def paired_queries(vectors, gallery, person_id):
    rows = []
    without_owner = {key: value for key, value in gallery.items() if key != person_id}
    for index in range(0, len(vectors)-1, 2):
        first, second = vectors[index:index+2]
        query = aggregate_query_embeddings([first[0], second[0]])
        rows.append({
            "expected_id": person_id, "files": [first[1]["file"], second[1]["file"]],
            "quality_passed": first[1]["quality"]["passed"] and second[1]["quality"]["passed"],
            **describe_match(query, gallery, person_id),
            "owner_removed": describe_match(query, without_owner, None),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--all-people", action="store_true", help="Test every enrolled LFW identity, including gallery extraction failures.")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    benchmark = ROOT / "data/benchmark_faces"
    manifest = json.loads((benchmark / "manifest.json").read_text(encoding="utf-8"))
    device = check_gpu_quick()
    model, device = load_face_model(device)
    runtime = AttendanceMixin.__new__(AttendanceMixin)
    runtime.face_detector = load_mediapipe_detector()
    gallery, failures = {}, []
    try:
        for number, (person_id, info) in enumerate(sorted(manifest["enrolled"].items()), 1):
            path = benchmark / "enrolled" / person_id / "register.jpg"
            try:
                embedding, _ = extract(path, runtime, model, device)
                gallery[person_id] = {"id": person_id, "name": info["source_identity"], "embedding": embedding}
            except Exception as exc:
                failures.append({"path": str(path), "error": str(exc)})
            if number % 15 == 0:
                print(f"Gallery {number}/90, extracted={len(gallery)}", flush=True)

        identities = list(gallery)
        ranked_pairs = []
        for index, first in enumerate(identities):
            for second in identities[index+1:]:
                distance = calculate_profile_distance(gallery[first]["embedding"], gallery[second])
                ranked_pairs.append((distance, first, second))
        ranked_pairs.sort()
        selected, used = [], set()
        for distance, first, second in ranked_pairs:
            if first in used or second in used:
                continue
            selected.append({"first": first, "second": second, "distance": distance,
                             "names": [gallery[first]["name"], gallery[second]["name"]]})
            used.update((first, second))
            if len(selected) >= args.pairs:
                break
        if args.all_people:
            used = set(manifest["enrolled"])
            print(f"Testing all {len(used)} enrolled identities; gallery={len(gallery)}", flush=True)
        else:
            print("Selected pairs: " + json.dumps(selected), flush=True)

        singles, pairs, quality_filtered = [], [], []
        for person_id in sorted(used):
            vectors = []
            for test in manifest["enrolled"][person_id]["tests"]:
                path = benchmark / "enrolled" / person_id / "tests" / test["file"]
                try:
                    embedding, quality = extract(path, runtime, model, device, check_quality=True)
                    row = {"expected_id": person_id, "file": str(path), "quality": quality,
                           **describe_match(embedding, gallery, person_id)}
                    singles.append(row)
                    vectors.append((embedding, row))
                except Exception as exc:
                    failures.append({"path": str(path), "error": str(exc)})
            # Non-overlapping photo pairs, avoiding duplicate-image pseudo-confirmation.
            pairs.extend(paired_queries(vectors, gallery, person_id))
            valid = [item for item in vectors if item[1]["quality"]["passed"]]
            quality_filtered.extend(paired_queries(valid, gallery, person_id))
            print(f"Probes {person_id}: {len(vectors)} extracted", flush=True)

        summary = {
            "gallery_size": len(gallery), "selected_people": len(used),
            "single_photo_outcomes": dict(Counter(row["outcome"] for row in singles)),
            "two_photo_outcomes": dict(Counter(row["outcome"] for row in pairs)),
            "two_photo_quality_passed": sum(row["quality_passed"] for row in pairs),
            "owner_removed_outcomes": dict(Counter(row["owner_removed"]["outcome"] for row in pairs)),
            "quality_filtered_outcomes": dict(Counter(row["outcome"] for row in quality_filtered)),
            "quality_filtered_owner_removed": dict(Counter(row["owner_removed"]["outcome"] for row in quality_filtered)),
            "extraction_failures": len(failures),
            "registered_gallery_missing": sorted(set(manifest["enrolled"]) - set(gallery)),
            "single_photos_extracted": len(singles),
            "single_photos_quality_passed": sum(row["quality"]["passed"] for row in singles),
            "people_with_valid_two_photo_query": len({row["expected_id"] for row in quality_filtered}),
            "people_with_accepted_wrong_identity": sorted({row["expected_id"] for row in quality_filtered if row["outcome"] == "wrong_identity"}),
        }
        report = {
            "method": "Fresh YOLO crops + alignment + ArcFace; isolated LFW gallery; current matching policy",
            "limitations": "Offline LFW benchmark only, not real employee validation. Photo pairs are NOT consecutive video frames. Quality reported separately; no live temporal/liveness/door test. Closest-pair selection is by embedding similarity, not verified siblings.",
            "mode": "all_enrolled" if args.all_people else "closest_pairs",
            "threshold": ARCFACE_THRESHOLD, "margin": ARCFACE_TOP2_MARGIN,
            "summary": summary, "selected_pairs": selected,
            "single_photos": singles, "two_photo_queries": pairs,
            "quality_filtered_queries": quality_filtered, "failures": failures,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RESULT " + json.dumps(summary), flush=True)
        print("REPORT " + str(args.report), flush=True)
    finally:
        if runtime.face_detector is not None:
            runtime.face_detector.close()


if __name__ == "__main__":
    main()
