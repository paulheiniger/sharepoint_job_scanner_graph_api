from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .calibration import clicked_known_length_calibration, detect_google_earth_scale_bar, feet_from_pixels, sqft_from_pixels
from .confidence import area_uncertainty_factor, confidence_components, measurement_warnings
from .geometry import polygon_area_pixels, polygon_perimeter_pixels, repair_polygon, straighten_architectural_ring
from .image_io import LoadedImage, image_to_array, load_image_bytes
from .models import CalibrationResult, MeasurementReport, MeasurementWarning, RoofMeasurement, RoofMeasureRequest, RoofSection
from .polygonize import section_from_polygon, sections_from_mask
from .segmentation import RoofSegmenter, SegmentationPrompts, choose_segmenter


@dataclass
class RoofMeasureResult:
    report: MeasurementReport
    selected_mask: np.ndarray | None
    candidate_count: int
    applied_footprint_polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    footprint_buffer_pixels: int = 0
    footprint_audit: list[dict[str, object]] = field(default_factory=list)
    applied_outline_prior_polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    outline_prior_buffer_pixels: int = 0
    deterministic_score: float = 0.0


def measure_roof_from_overhead_image(
    *,
    image_bytes: bytes,
    request: RoofMeasureRequest,
    segmenter: RoofSegmenter | None = None,
    selected_candidate_index: int = 0,
    storage_root: str = "output/roof_measure_uploads",
    mask_input_override: np.ndarray | None = None,
) -> RoofMeasureResult:
    loaded = load_image_bytes(image_bytes, file_name=request.overhead_image_name, storage_root=storage_root)
    segmenter = segmenter or choose_segmenter(request.segmenter_name)
    prompts = SegmentationPrompts(
        positive_points=request.positive_points,
        negative_points=request.negative_points,
        box=request.segmentation_box,
        mask_input=(
            np.asarray(mask_input_override, dtype=bool)
            if mask_input_override is not None
            else (
                _outline_prior_mask_prompt(
                    image_to_array(loaded.inference_image).shape[:2],
                    request.outline_prior_polygons,
                )
                if request.outline_prior_as_mask_prompt and request.outline_prior_polygons
                else None
            )
        ),
    )
    try:
        segmentation = segmenter.segment(image_to_array(loaded.inference_image), prompts)
    except Exception as exc:
        fallback = choose_segmenter("manual_fallback")
        segmentation = fallback.segment(image_to_array(loaded.inference_image), prompts)
        segmentation.warnings.append(f"Requested segmenter failed: {type(exc).__name__}: {exc}")
    candidates = segmentation.candidates
    selected_mask = None
    applied_footprint_polygons: list[list[tuple[float, float]]] = []
    footprint_buffer_pixels = 0
    applied_outline_prior_polygons: list[list[tuple[float, float]]] = []
    outline_prior_buffer_pixels = 0
    sections = []
    segmentation_score = 0.0
    if candidates:
        selected_index = min(max(selected_candidate_index, 0), len(candidates) - 1)
        candidate = candidates[selected_index]
        selected_mask = candidate.mask
        if request.footprint_polygons:
            buffer_pixels = _footprint_buffer_pixels(request)
            footprint_buffer_pixels = buffer_pixels
            constrained_mask = _constrain_mask_to_footprints(
                selected_mask,
                request.footprint_polygons,
                buffer_pixels=buffer_pixels,
            )
            if constrained_mask.any():
                retained_fraction = float(constrained_mask.sum()) / max(float(selected_mask.sum()), 1.0)
                selected_mask = constrained_mask
                applied_footprint_polygons = request.footprint_polygons
                segmentation.warnings.append(
                    "Segmentation constrained to selected building footprint(s) "
                    f"with a {float(request.footprint_buffer_feet):g} ft buffer; retained {retained_fraction:.0%} of mask pixels."
                )
            else:
                segmentation.warnings.append("Selected building footprint(s) did not overlap the segmentation mask; original mask retained.")
        if request.outline_prior_polygons:
            outline_prior_buffer_pixels = max(0, min(int(request.outline_prior_buffer_pixels), 48))
            constrained_mask = _constrain_mask_to_footprints(
                selected_mask,
                request.outline_prior_polygons,
                buffer_pixels=outline_prior_buffer_pixels,
            )
            if constrained_mask.any():
                retained_fraction = float(constrained_mask.sum()) / max(float(selected_mask.sum()), 1.0)
                selected_mask = constrained_mask
                applied_outline_prior_polygons = request.outline_prior_polygons
                segmentation.warnings.append(
                    "Segmentation constrained to the buffered AI roof outline prior; "
                    f"retained {retained_fraction:.0%} of mask pixels."
                )
            else:
                segmentation.warnings.append("AI roof outline prior did not overlap the segmentation mask; original mask retained.")
        segmentation_score = float(candidate.score)
        sections = sections_from_mask(
            selected_mask,
            simplification_tolerance=request.simplification_tolerance,
            minimum_section_area_pixels=request.minimum_section_area_pixels,
            edge_snap_strength=request.edge_snap_strength,
        )
    calibration = _metadata_calibration(request.metadata_pixels_per_foot)
    if not calibration.pixels_per_foot:
        calibration = clicked_known_length_calibration(
            point_a=request.calibration_point_a,
            point_b=request.calibration_point_b,
            length_feet=request.calibration_length_feet,
        )
    if not calibration.pixels_per_foot:
        calibration = detect_google_earth_scale_bar(
            loaded.inference_image,
            label_hint=request.scale_bar_label_hint,
            use_ai_fallback=request.use_ai_scale_reader,
        )
    for section in sections:
        section.area_sqft = sqft_from_pixels(section.area_pixels, calibration.pixels_per_foot)
        section.perimeter_ft = feet_from_pixels(section.perimeter_pixels, calibration.pixels_per_foot)
    total_area = _sum_optional([section.area_sqft for section in sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in sections])
    confidence = confidence_components(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=segmentation_score,
    )
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = measurement_warnings(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=segmentation_score,
        segmenter_warnings=segmentation.warnings,
    )
    measurement = RoofMeasurement(
        total_area_sqft=None if total_area is None else round(total_area, 2),
        total_perimeter_ft=None if total_perimeter is None else round(total_perimeter, 2),
        low_area_sqft=None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
        high_area_sqft=None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
        sections=sections,
        calibration=calibration,
        confidence=confidence,
        warnings=warnings,
        assumptions=[
            "Area is calculated in plan view from the uploaded overhead image.",
            "Oblique images are advisory only in Milestone 1 and are not used for primary measurement.",
            "This is an estimating-assistance measurement, not a survey-grade measurement.",
        ],
    )
    report = MeasurementReport(
        id=f"roof-measure-{uuid.uuid4().hex[:16]}",
        address=request.address,
        job_id=request.job_id,
        source_images=[loaded.metadata],
        calibration_method=calibration.calibration_type,
        pixels_per_foot=calibration.pixels_per_foot,
        measurement=measurement,
        model_name=segmentation.model_name,
        model_version=segmentation.model_version,
    )
    return RoofMeasureResult(
        report=report,
        selected_mask=selected_mask,
        candidate_count=len(candidates),
        applied_footprint_polygons=applied_footprint_polygons,
        footprint_buffer_pixels=footprint_buffer_pixels,
        footprint_audit=_applied_footprint_audit(request, applied_footprint_polygons, footprint_buffer_pixels),
        applied_outline_prior_polygons=applied_outline_prior_polygons,
        outline_prior_buffer_pixels=outline_prior_buffer_pixels,
        deterministic_score=score_roof_result(selected_mask, sections, request.footprint_polygons),
    )


def _constrain_mask_to_footprints(mask: np.ndarray, polygons: list[list[tuple[float, float]]], *, buffer_pixels: int = 8) -> np.ndarray:
    footprint_mask = footprint_constraint_mask(mask.shape[:2], polygons, buffer_pixels=buffer_pixels)
    return np.asarray(mask, dtype=bool) & footprint_mask


def _outline_prior_mask_prompt(shape: tuple[int, int], polygons: list[list[tuple[float, float]]]) -> np.ndarray:
    """Create a full-resolution binary prior; the SAM2 service converts it to 256px logits."""
    return footprint_constraint_mask(shape, polygons, buffer_pixels=0)


def _footprint_buffer_pixels(request: RoofMeasureRequest) -> int:
    feet = max(0.0, min(float(request.footprint_buffer_feet), 30.0))
    pixels_per_foot = float(request.metadata_pixels_per_foot or 0)
    if pixels_per_foot > 0:
        return max(0, int(round(feet * pixels_per_foot)))
    return 8


def footprint_constraint_mask(
    shape: tuple[int, int],
    polygons: list[list[tuple[float, float]]],
    *,
    buffer_pixels: int = 0,
) -> np.ndarray:
    if buffer_pixels > 0:
        shapely_mask = _shapely_constraint_mask(shape, polygons, buffer_pixels=buffer_pixels)
        if shapely_mask is not None:
            return shapely_mask
    height, width = shape
    footprint_image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(footprint_image)
    for polygon in polygons:
        if len(polygon) >= 3:
            draw.polygon([(float(x), float(y)) for x, y in polygon], fill=255)
    footprint_mask = np.asarray(footprint_image, dtype=bool)
    return _dilate_mask(footprint_mask, radius=buffer_pixels) if buffer_pixels > 0 else footprint_mask


def _shapely_constraint_mask(
    shape: tuple[int, int],
    polygons: list[list[tuple[float, float]]],
    *,
    buffer_pixels: int,
) -> np.ndarray | None:
    """Union and mitre-buffer polygon priors when Shapely is available."""
    try:
        from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
        from shapely.ops import unary_union
    except ImportError:
        return None
    geometries = []
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        geometry = Polygon([(float(x), float(y)) for x, y in polygon])
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if not geometry.is_empty:
            geometries.append(geometry)
    if not geometries:
        return None
    merged = unary_union(geometries).buffer(float(buffer_pixels), join_style=2)
    if merged.is_empty:
        return None
    if isinstance(merged, Polygon):
        drawable = [merged]
    elif isinstance(merged, (MultiPolygon, GeometryCollection)):
        drawable = [geometry for geometry in merged.geoms if isinstance(geometry, Polygon)]
    else:
        return None
    height, width = shape
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for geometry in drawable:
        draw.polygon(list(geometry.exterior.coords), fill=255)
        for interior in geometry.interiors:
            draw.polygon(list(interior.coords), fill=0)
    return np.asarray(image, dtype=bool)


def _applied_footprint_audit(
    request: RoofMeasureRequest,
    applied_polygons: list[list[tuple[float, float]]],
    buffer_pixels: int,
) -> list[dict[str, object]]:
    if not applied_polygons:
        return []
    audit: list[dict[str, object]] = []
    for source in request.footprint_source_records:
        image_polygons = source.get("image_polygons") if isinstance(source, dict) else None
        if not isinstance(image_polygons, list):
            continue
        if any(_same_ring(candidate, applied) for candidate in image_polygons for applied in applied_polygons):
            audit.append({
                "footprint_id": str(source.get("footprint_id") or ""),
                "label": str(source.get("label") or ""),
                "provider": str(source.get("provider") or ""),
                "attribution": str(source.get("attribution") or ""),
                "geographic_rings": source.get("geographic_rings") or [],
                "image_polygons": image_polygons,
                "buffer_pixels": buffer_pixels,
                "buffer_feet": request.footprint_buffer_feet,
            })
    return audit


def _same_ring(first: object, second: list[tuple[float, float]]) -> bool:
    if not isinstance(first, list) or len(first) != len(second):
        return False
    try:
        return all(abs(float(a[0]) - float(b[0])) < 0.01 and abs(float(a[1]) - float(b[1])) < 0.01 for a, b in zip(first, second))
    except (TypeError, ValueError, IndexError):
        return False


def _dilate_mask(mask: np.ndarray, *, radius: int) -> np.ndarray:
    expanded = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(radius))):
        padded = np.pad(expanded, 1, mode="constant", constant_values=False)
        expanded = np.zeros_like(expanded)
        for y_offset in range(3):
            for x_offset in range(3):
                expanded |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return expanded


def score_roof_result(
    mask: np.ndarray | None,
    sections: list[RoofSection],
    footprint_polygons: list[list[tuple[float, float]]],
    outline_prior_polygons: list[list[tuple[float, float]]] | None = None,
) -> float:
    """Score only deterministic properties used to reject a weaker correction pass."""
    if mask is None or not np.asarray(mask, dtype=bool).any() or not sections:
        return 0.0
    components = len(sections)
    fragmentation = 1.0 / max(components, 1)
    validity = sum(1 for section in sections if len(repair_polygon(section.polygon)) >= 4) / len(sections)
    regularity = sum(_ring_axis_regularity(section.polygon) for section in sections) / len(sections)
    core_retention = _mask_core_retention(np.asarray(mask, dtype=bool), sections)
    mask_iou = _section_mask_iou(np.asarray(mask, dtype=bool), sections)
    footprint_overlap = 1.0
    if footprint_polygons:
        footprint = _constrain_mask_to_footprints(np.ones_like(mask, dtype=bool), footprint_polygons, buffer_pixels=0)
        footprint_overlap = float((np.asarray(mask, dtype=bool) & footprint).sum()) / max(float(np.asarray(mask, dtype=bool).sum()), 1.0)
    prior_agreement = 1.0
    if outline_prior_polygons:
        prior = footprint_constraint_mask(mask.shape, outline_prior_polygons, buffer_pixels=16)
        section_mask = sections_mask(mask.shape, sections)
        prior_agreement = float((section_mask & prior).sum()) / max(float(section_mask.sum()), 1.0)
    return round(
        0.18 * footprint_overlap
        + 0.18 * validity
        + 0.13 * regularity
        + 0.08 * fragmentation
        + 0.25 * core_retention
        + 0.12 * mask_iou
        + 0.06 * prior_agreement,
        4,
    )


def finalize_roof_sections(
    mask: np.ndarray | None,
    sections: list[RoofSection],
    *,
    footprint_polygons: list[list[tuple[float, float]]] | None = None,
    outline_prior_polygons: list[list[tuple[float, float]]] | None = None,
) -> tuple[list[RoofSection], dict[str, object]]:
    """Choose the cleanest geometry that does not lose the observed roof core."""
    baseline = [section.model_copy(deep=True) for section in sections]
    if mask is None or not baseline:
        return baseline, {"candidate": "raw_mask", "accepted": False, "reason": "no mask or sections"}
    footprint_polygons = footprint_polygons or []
    outline_prior_polygons = outline_prior_polygons or []
    baseline_score = score_roof_result(mask, baseline, footprint_polygons, outline_prior_polygons)
    baseline_area = sum(section.area_pixels for section in baseline)
    candidates = [
        ("topology_clean", _topology_cleaned_sections(baseline)),
        ("architectural_fit", _architectural_sections(baseline)),
    ]
    best_sections = baseline
    best_score = baseline_score
    best_record: dict[str, object] = {
        "candidate": "raw_mask",
        "score": baseline_score,
        "accepted": True,
        "reason": "raw constrained SAM contour retained",
    }
    for name, candidate in candidates:
        if not candidate:
            continue
        candidate_score = score_roof_result(mask, candidate, footprint_polygons, outline_prior_polygons)
        candidate_area = sum(polygon_area_pixels(section.polygon, section.holes) for section in candidate)
        area_drift = abs(candidate_area - baseline_area) / max(baseline_area, 1.0)
        candidate_iou = _section_mask_iou(np.asarray(mask, dtype=bool), candidate)
        candidate_core = _mask_core_retention(np.asarray(mask, dtype=bool), candidate)
        candidate_prior = _section_prior_agreement(np.asarray(mask, dtype=bool), candidate, outline_prior_polygons)
        accepted = (
            area_drift <= 0.04
            and candidate_iou >= 0.88
            and candidate_core >= 0.96
            and candidate_prior >= 0.92
            and candidate_score > best_score
        )
        if accepted:
            best_sections = candidate
            best_score = candidate_score
            best_record = {
                "candidate": name,
                "score": candidate_score,
                "mask_iou": round(candidate_iou, 3),
                "core_retention": round(candidate_core, 3),
                "prior_agreement": round(candidate_prior, 3),
                "area_drift": round(area_drift, 3),
                "accepted": True,
                "reason": "cleaner candidate preserved constrained mask and priors",
            }
    return best_sections, best_record


def _topology_cleaned_sections(sections: list[RoofSection]) -> list[RoofSection]:
    cleaned: list[RoofSection] = []
    for section in sections:
        candidate = section.model_copy(deep=True)
        candidate.polygon = _shapely_clean_ring(candidate.polygon, tolerance=1.5)
        candidate.holes = [_shapely_clean_ring(hole, tolerance=1.5) for hole in candidate.holes]
        candidate.holes = [hole for hole in candidate.holes if hole]
        if len(candidate.polygon) >= 4:
            cleaned.append(candidate)
    return cleaned


def _architectural_sections(sections: list[RoofSection]) -> list[RoofSection]:
    straightened: list[RoofSection] = []
    for section in _topology_cleaned_sections(sections):
        candidate = section.model_copy(deep=True)
        candidate.polygon = straighten_architectural_ring(candidate.polygon)
        candidate.holes = [straighten_architectural_ring(hole) for hole in candidate.holes]
        if len(candidate.polygon) >= 4:
            straightened.append(candidate)
    return straightened


def _shapely_clean_ring(ring: list[tuple[float, float]], *, tolerance: float) -> list[tuple[float, float]]:
    repaired = repair_polygon(ring)
    if len(repaired) < 4:
        return []
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return repaired
    geometry = Polygon(repaired)
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty or geometry.geom_type != "Polygon":
        return repaired
    simplified = geometry.simplify(tolerance, preserve_topology=True)
    if simplified.is_empty or simplified.geom_type != "Polygon":
        return repaired
    return repair_polygon([(float(x), float(y)) for x, y in simplified.exterior.coords])


def _section_mask_iou(mask: np.ndarray, sections: list[RoofSection]) -> float:
    candidate = sections_mask(mask.shape, sections)
    union = mask | candidate
    return float((mask & candidate).sum()) / max(float(union.sum()), 1.0)


def _section_prior_agreement(
    mask: np.ndarray,
    sections: list[RoofSection],
    outline_prior_polygons: list[list[tuple[float, float]]],
) -> float:
    if not outline_prior_polygons:
        return 1.0
    candidate = sections_mask(mask.shape, sections)
    prior = footprint_constraint_mask(mask.shape, outline_prior_polygons, buffer_pixels=16)
    return float((candidate & prior).sum()) / max(float(candidate.sum()), 1.0)


def _mask_core_retention(mask: np.ndarray, sections: list[RoofSection], *, max_depth: int = 16) -> float:
    """Penalize final polygons that cut through the interior of the SAM roof mask."""
    if not mask.any():
        return 0.0
    section_mask = sections_mask(mask.shape, sections)
    weights = _mask_core_weights(mask, max_depth=max_depth)
    total_weight = float(weights.sum())
    if total_weight <= 0:
        return 0.0
    return float(weights[section_mask].sum()) / total_weight


def sections_mask(shape: tuple[int, int], sections: list[RoofSection]) -> np.ndarray:
    height, width = shape
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for section in sections:
        if len(section.polygon) >= 3:
            draw.polygon([(float(x), float(y)) for x, y in section.polygon], fill=255)
        for hole in section.holes:
            if len(hole) >= 3:
                draw.polygon([(float(x), float(y)) for x, y in hole], fill=0)
    return np.asarray(image, dtype=bool)


def _mask_core_weights(mask: np.ndarray, *, max_depth: int) -> np.ndarray:
    remaining = np.asarray(mask, dtype=bool).copy()
    weights = np.zeros(remaining.shape, dtype=float)
    for _ in range(max(1, int(max_depth))):
        if not remaining.any():
            break
        weights[remaining] += 1.0
        padded = np.pad(remaining, 1, mode="constant", constant_values=False)
        eroded = np.ones_like(remaining)
        for y_offset in range(3):
            for x_offset in range(3):
                eroded &= padded[y_offset : y_offset + remaining.shape[0], x_offset : x_offset + remaining.shape[1]]
        remaining = eroded
    return weights


def _ring_axis_regularity(ring: list[tuple[float, float]]) -> float:
    repaired = repair_polygon(ring)
    edges = list(zip(repaired, repaired[1:]))
    if not edges:
        return 0.0
    aligned = 0
    for (x1, y1), (x2, y2) in edges:
        if min(abs(x2 - x1), abs(y2 - y1)) <= max(abs(x2 - x1), abs(y2 - y1)) * 0.25:
            aligned += 1
    return aligned / len(edges)


def measure_roof_from_outline_polygons(
    *,
    image_bytes: bytes,
    request: RoofMeasureRequest,
    polygons: list[list[tuple[float, float]]],
    model_name: str = "openai_roof_outline",
    model_version: str = "",
    outline_confidence: float = 0.65,
    outline_notes: str = "",
    storage_root: str = "output/roof_measure_uploads",
) -> RoofMeasureResult:
    loaded = load_image_bytes(image_bytes, file_name=request.overhead_image_name, storage_root=storage_root)
    sections: list[RoofSection] = []
    for index, polygon in enumerate(polygons, start=1):
        try:
            section = section_from_polygon(f"section-{index}", polygon)
        except Exception:
            continue
        if section.area_pixels <= 0:
            continue
        section.confidence = max(0.0, min(float(outline_confidence or 0), 1.0))
        sections.append(section)

    calibration = _metadata_calibration(request.metadata_pixels_per_foot)
    if not calibration.pixels_per_foot:
        calibration = clicked_known_length_calibration(
            point_a=request.calibration_point_a,
            point_b=request.calibration_point_b,
            length_feet=request.calibration_length_feet,
        )
    if not calibration.pixels_per_foot:
        calibration = detect_google_earth_scale_bar(
            loaded.inference_image,
            label_hint=request.scale_bar_label_hint,
            use_ai_fallback=request.use_ai_scale_reader,
        )
    for section in sections:
        section.area_sqft = sqft_from_pixels(section.area_pixels, calibration.pixels_per_foot)
        section.perimeter_ft = feet_from_pixels(section.perimeter_pixels, calibration.pixels_per_foot)
    total_area = _sum_optional([section.area_sqft for section in sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in sections])
    confidence = confidence_components(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=max(0.0, min(float(outline_confidence or 0), 1.0)),
    )
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = measurement_warnings(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=max(0.0, min(float(outline_confidence or 0), 1.0)),
        segmenter_warnings=[],
    )
    warnings.append(
        MeasurementWarning(
            code="ai_outline_review",
            message="AI-suggested roof outline must be reviewed and adjusted before final estimating use.",
            severity="info",
        )
    )
    if outline_notes:
        warnings.append(
            MeasurementWarning(
                code="ai_outline_notes",
                message=outline_notes,
                severity="info",
            )
        )
    measurement = RoofMeasurement(
        total_area_sqft=None if total_area is None else round(total_area, 2),
        total_perimeter_ft=None if total_perimeter is None else round(total_perimeter, 2),
        low_area_sqft=None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
        high_area_sqft=None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
        sections=sections,
        calibration=calibration,
        confidence=confidence,
        warnings=warnings,
        assumptions=[
            "Area is calculated in plan view from the uploaded overhead image.",
            "Roof polygons were suggested by AI and require estimator review.",
            "This is an estimating-assistance measurement, not a survey-grade measurement.",
        ],
    )
    report = MeasurementReport(
        id=f"roof-measure-{uuid.uuid4().hex[:16]}",
        address=request.address,
        job_id=request.job_id,
        source_images=[loaded.metadata],
        calibration_method=calibration.calibration_type,
        pixels_per_foot=calibration.pixels_per_foot,
        measurement=measurement,
        model_name=model_name,
        model_version=model_version or "roof-measure-ai-outline-v1",
        user_corrections=[
            {
                "type": "ai_outline_seed",
                "note": outline_notes,
                "section_count": len(sections),
            }
        ],
    )
    return RoofMeasureResult(report=report, selected_mask=None, candidate_count=0)


def _sum_optional(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _metadata_calibration(pixels_per_foot: float | None) -> CalibrationResult:
    try:
        value = float(pixels_per_foot or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return CalibrationResult(calibration_type="none", confidence="none")
    return CalibrationResult(
        calibration_type="metadata",
        pixels_per_foot=value,
        confidence="medium",
        warning=(
            "Calibrated from map imagery metadata. Verify against a known roof dimension before final use."
        ),
    )


def load_overhead_image_for_overlay(image_bytes: bytes, file_name: str) -> LoadedImage:
    return load_image_bytes(image_bytes, file_name=file_name)


def recalculate_report_from_corrected_sections(
    report: MeasurementReport,
    sections: list[RoofSection],
    *,
    correction_note: str = "Estimator corrected roof polygon vertices.",
) -> MeasurementReport:
    calibration = report.measurement.calibration
    corrected_sections: list[RoofSection] = []
    for section in sections:
        corrected = section.model_copy(deep=True)
        corrected.polygon = repair_polygon(corrected.polygon)
        corrected.holes = [repair_polygon(hole) for hole in corrected.holes if repair_polygon(hole)]
        corrected.area_pixels = polygon_area_pixels(corrected.polygon, corrected.holes)
        corrected.perimeter_pixels = polygon_perimeter_pixels(corrected.polygon, corrected.holes)
        corrected.area_sqft = sqft_from_pixels(corrected.area_pixels, calibration.pixels_per_foot)
        corrected.perimeter_ft = feet_from_pixels(corrected.perimeter_pixels, calibration.pixels_per_foot)
        corrected.confidence = max(float(corrected.confidence or 0), 0.75)
        corrected_sections.append(corrected)

    total_area = _sum_optional([section.area_sqft for section in corrected_sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in corrected_sections])
    confidence = dict(report.measurement.confidence)
    if corrected_sections:
        confidence["polygon_quality"] = round(
            sum(section.confidence for section in corrected_sections) / len(corrected_sections),
            3,
        )
        if calibration.pixels_per_foot:
            confidence["overall_estimating"] = round(min(0.9, max(float(confidence.get("overall_estimating") or 0), 0.72)), 3)
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = [
        warning
        for warning in report.measurement.warnings
        if warning.code not in {"no_roof_sections"}
    ]
    warnings.append(
        MeasurementWarning(
            code="manual_polygon_correction",
            message=correction_note,
            severity="info",
        )
    )
    measurement = report.measurement.model_copy(
        deep=True,
        update={
            "total_area_sqft": None if total_area is None else round(total_area, 2),
            "total_perimeter_ft": None if total_perimeter is None else round(total_perimeter, 2),
            "low_area_sqft": None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
            "high_area_sqft": None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
            "sections": corrected_sections,
            "confidence": confidence,
            "warnings": warnings,
        },
    )
    return report.model_copy(
        deep=True,
        update={
            "measurement": measurement,
            "user_corrections": [
                *report.user_corrections,
                {
                    "type": "polygon_vertices",
                    "note": correction_note,
                    "section_count": len(corrected_sections),
                },
            ],
        },
    )
