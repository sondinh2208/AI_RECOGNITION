"""Face matching rules, independent of camera, UI, door commands and Telegram."""

from dataclasses import dataclass

import numpy as np

from ai_engine import calculate_cosine_distance
from config import ARCFACE_THRESHOLD, ARCFACE_TOP2_MARGIN, ARCFACE_UNCERTAIN_THRESHOLD


def classify_recognition_score(distance, margin):
    """Accept only a close top1 match that is clearly separated from top2."""
    if distance <= ARCFACE_THRESHOLD and margin >= ARCFACE_TOP2_MARGIN:
        return "matched"
    if distance <= ARCFACE_UNCERTAIN_THRESHOLD:
        return "ambiguous"
    return "unknown"

def profile_is_live_recognition_enabled(profile):
    """Benchmark profiles never participate in real attendance matching."""
    return bool(
        profile.get("recognition_enabled", True)
        and profile.get("source") != "LFW benchmark"
    )


def calculate_profile_distance(query_vector, profile):
    """Compare against the normalized centroid, not the most permissive sample."""
    vectors = profile.get("embeddings") or []
    if not vectors and profile.get("embedding") is not None:
        vectors = [profile["embedding"]]
    normalized = []
    for vector in vectors:
        value = np.asarray(vector, dtype=np.float32).flatten()
        norm = float(np.linalg.norm(value))
        if value.size and norm > 1e-12:
            normalized.append(value / norm)
    if not normalized:
        return float("inf")
    centroid = np.mean(np.stack(normalized), axis=0)
    return calculate_cosine_distance(query_vector, centroid)


def aggregate_query_embeddings(vectors):
    """Normalize then average consecutive frame embeddings to suppress JPEG noise."""
    normalized = []
    for vector in vectors:
        value = np.asarray(vector, dtype=np.float32).flatten()
        norm = float(np.linalg.norm(value))
        if value.size and norm > 1e-12:
            normalized.append(value / norm)
    if not normalized:
        return None
    centroid = np.mean(np.stack(normalized), axis=0)
    norm = float(np.linalg.norm(centroid))
    return centroid / norm if norm > 1e-12 else None


@dataclass(frozen=True)
class FaceMatchResult:
    """Top two identities and their distances; a match does not itself open a door."""

    best_profile: dict | None
    best_distance: float
    second_profile: dict | None
    second_distance: float

    @property
    def margin(self):
        return self.second_distance - self.best_distance

    @property
    def decision(self):
        return classify_recognition_score(self.best_distance, self.margin)


def match_face_profiles(query_vector, profiles, *, live_only=True):
    """Rank identities, excluding disabled/benchmark profiles in live operation."""
    ranked = []
    for profile in profiles.values():
        if live_only and not profile_is_live_recognition_enabled(profile):
            continue
        distance = calculate_profile_distance(query_vector, profile)
        if np.isfinite(distance):
            ranked.append((distance, profile))
    ranked.sort(key=lambda item: item[0])
    best_distance, best_profile = ranked[0] if ranked else (999.0, None)
    second_distance, second_profile = (
        ranked[1] if len(ranked) > 1 else (float("inf"), None)
    )
    return FaceMatchResult(
        best_profile=best_profile,
        best_distance=best_distance,
        second_profile=second_profile,
        second_distance=second_distance,
    )
