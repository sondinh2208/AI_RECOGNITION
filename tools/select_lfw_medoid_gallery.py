"""Chọn duy nhất một ảnh đăng ký đại diện nhất cho mỗi danh tính LFW."""

from __future__ import annotations

import json
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


BENCHMARK = Path("data/benchmark_faces")
MANIFEST = BENCHMARK / "manifest.json"
PROBE_CACHE = BENCHMARK / "evaluation_embeddings.pkl"
EMBEDDINGS = Path("data/embeddings.pkl")


def normalized(vector) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    return value / np.linalg.norm(value)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with MANIFEST.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    with PROBE_CACHE.open("rb") as file:
        probes = pickle.load(file)
    with EMBEDDINGS.open("rb") as file:
        profiles = pickle.load(file)

    probes_by_id = defaultdict(list)
    for probe in probes:
        if probe.get("kind") == "known":
            probes_by_id[probe["expected_id"]].append(probe)

    changed = 0
    choices = []
    for index, (person_id, info) in enumerate(sorted(manifest["enrolled"].items()), 1):
        employee_id = f"LFW{index:03d}"
        person_probes = sorted(probes_by_id[employee_id], key=lambda item: item["path"])
        if len(person_probes) != len(info["tests"]):
            raise RuntimeError(f"Cache không khớp manifest tại {employee_id}")

        vectors = [normalized(profiles[employee_id]["embedding"])]
        vectors.extend(normalized(item["embedding"]) for item in person_probes)
        matrix = np.stack(vectors)
        distances = 1.0 - matrix @ matrix.T
        medoid_index = int(np.argmin(distances.sum(axis=1)))
        choices.append({
            "employee_id": employee_id,
            "person_id": person_id,
            "selected_index": medoid_index,
            "mean_distance": round(float(distances[medoid_index].mean()), 6),
        })
        if medoid_index == 0:
            info["register_selection"] = {
                "method": "arcface_embedding_medoid",
                "candidate_count": len(vectors),
                "mean_distance": round(float(distances[medoid_index].mean()), 6),
            }
            continue

        test_index = medoid_index - 1
        identity_dir = BENCHMARK / "enrolled" / person_id
        register_path = identity_dir / "register.jpg"
        test_path = identity_dir / "tests" / info["tests"][test_index]["file"]
        temporary = identity_dir / ".register_medoid_swap.jpg"
        if temporary.exists():
            temporary.unlink()
        os.replace(register_path, temporary)
        os.replace(test_path, register_path)
        os.replace(temporary, test_path)

        old_register = dict(info["register"])
        selected_test = dict(info["tests"][test_index])
        info["register"] = {
            "source": selected_test["source"],
            "quality_score": selected_test["quality_score"],
        }
        info["tests"][test_index] = {
            "file": selected_test["file"],
            "source": old_register["source"],
            "quality_score": old_register["quality_score"],
        }
        info["register_selection"] = {
            "method": "arcface_embedding_medoid",
            "candidate_count": len(vectors),
            "mean_distance": round(float(distances[medoid_index].mean()), 6),
        }
        changed += 1

    manifest["gallery_selection"] = {
        "method": "arcface_embedding_medoid",
        "candidate_images_per_identity": 6,
        "changed_identities": changed,
        "choices": choices,
    }
    temporary_manifest = MANIFEST.with_suffix(".json.tmp")
    with temporary_manifest.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
    temporary_manifest.replace(MANIFEST)

    # Các cache này mô tả cách chia register/test cũ, bắt buộc tạo lại.
    PROBE_CACHE.unlink(missing_ok=True)
    (BENCHMARK / "calibration_report.json").unlink(missing_ok=True)
    print(f"Đã chọn medoid cho 90 danh tính; thay ảnh đăng ký của {changed}/90 người.")


if __name__ == "__main__":
    main()
