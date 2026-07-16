from __future__ import annotations

from pydantic import BaseModel


class RegistrationQuality(BaseModel):
    matched_keypoints: int = 0
    inlier_ratio: float = 0.0
    reprojection_error: float | None = None
    accepted: bool = False
    reason: str = "Oblique registration is not used for primary area calculation in Milestone 1."


def evaluate_oblique_registration_placeholder() -> RegistrationQuality:
    return RegistrationQuality()

