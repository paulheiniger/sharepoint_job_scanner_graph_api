from __future__ import annotations

import pandas as pd
import streamlit as st
from PIL import Image

try:
    from streamlit_drawable_canvas import st_canvas
except ImportError:  # pragma: no cover - exercised in environments without the optional component.
    st_canvas = None

from .exports import geojson_to_string, measurement_to_geojson, report_to_json
from .image_io import load_image_bytes, uploaded_file_bytes
from .models import RoofMeasureRequest, RoofSection
from .service import RoofMeasureResult, measure_roof_from_overhead_image, recalculate_report_from_corrected_sections
from .visualization import annotated_overlay, image_png_bytes


def render_ai_roof_measure_page() -> None:
    st.title("AI Roof Measure")
    st.caption(
        "Experimental estimating-assistance measurement from uploaded imagery. "
        "Results are approximate and require estimator review."
    )

    with st.expander("Milestone 1 Scope", expanded=False):
        st.markdown(
            """
- Uses uploaded overhead imagery only for primary area calculation.
- Requires a user-supplied known length and two calibration points before square footage is reported.
- SAM 2 is behind a provider interface but is not required in this runtime.
- The current fallback mask is a prompt-centered candidate, not a roof-aware model.
- Oblique images are stored as references only in this phase.
            """.strip()
        )

    address = st.text_input("Address", key="roof_measure_address")
    job_id = st.text_input("Job ID (optional)", key="roof_measure_job_id")
    overhead = st.file_uploader("Top-down or near-top-down aerial image", type=["jpg", "jpeg", "png"], key="roof_measure_overhead")
    oblique_files = st.file_uploader(
        "Optional oblique drone/reference images",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="roof_measure_oblique",
    )
    if oblique_files:
        st.caption(f"{len(oblique_files):,} oblique/reference image(s) attached. Milestone 1 does not use these for primary area.")

    if overhead is None:
        st.info("Upload an overhead image to start.")
        return

    image_bytes = uploaded_file_bytes(overhead)
    try:
        loaded = load_image_bytes(image_bytes, file_name=overhead.name)
    except Exception as exc:
        st.error(f"Could not read overhead image: {exc}")
        return

    st.image(loaded.inference_image, caption=f"{overhead.name} ({loaded.metadata.inference_width} x {loaded.metadata.inference_height} inference pixels)")
    if loaded.metadata.quality_flags:
        st.warning("Image quality flags: " + ", ".join(loaded.metadata.quality_flags))

    st.subheader("Prompt and Calibration")
    prompt_col1, prompt_col2, prompt_col3 = st.columns(3)
    with prompt_col1:
        prompt_x = st.number_input(
            "Roof interior X pixel",
            min_value=0,
            max_value=max(loaded.metadata.inference_width - 1, 0),
            value=loaded.metadata.inference_width // 2,
            step=1,
            key="roof_measure_prompt_x",
        )
    with prompt_col2:
        prompt_y = st.number_input(
            "Roof interior Y pixel",
            min_value=0,
            max_value=max(loaded.metadata.inference_height - 1, 0),
            value=loaded.metadata.inference_height // 2,
            step=1,
            key="roof_measure_prompt_y",
        )
    with prompt_col3:
        candidate_index = st.selectbox("Candidate mask", [1, 2, 3], index=0, key="roof_measure_candidate_index")

    cal_col1, cal_col2, cal_col3 = st.columns(3)
    with cal_col1:
        known_length = st.number_input("Known length (ft)", min_value=0.0, value=0.0, step=1.0, key="roof_measure_known_length")
    with cal_col2:
        point_a_text = st.text_input("Calibration point A x,y", value="", placeholder="120, 400", key="roof_measure_cal_a")
    with cal_col3:
        point_b_text = st.text_input("Calibration point B x,y", value="", placeholder="620, 400", key="roof_measure_cal_b")

    controls_col1, controls_col2, controls_col3 = st.columns(3)
    with controls_col1:
        simplification_tolerance = st.slider("Simplification tolerance", min_value=0.0, max_value=20.0, value=2.0, step=0.5)
    with controls_col2:
        minimum_section_area = st.number_input("Minimum section size (pixels)", min_value=1.0, value=400.0, step=100.0)
    with controls_col3:
        edge_snap_strength = st.slider("Edge snap strength", min_value=0.0, max_value=20.0, value=0.0, step=0.5)

    point_a = _parse_point(point_a_text)
    point_b = _parse_point(point_b_text)
    request = RoofMeasureRequest(
        address=address,
        job_id=job_id or None,
        overhead_image_name=overhead.name,
        positive_points=[(float(prompt_x), float(prompt_y))],
        calibration_length_feet=known_length if known_length > 0 else None,
        calibration_point_a=point_a,
        calibration_point_b=point_b,
        simplification_tolerance=simplification_tolerance,
        minimum_section_area_pixels=minimum_section_area,
        edge_snap_strength=edge_snap_strength,
        segmenter_name="manual_fallback",
    )
    if st.button("Measure Roof", type="primary", width="stretch"):
        try:
            result = measure_roof_from_overhead_image(
                image_bytes=image_bytes,
                request=request,
                selected_candidate_index=int(candidate_index) - 1,
            )
        except Exception as exc:
            st.error(f"Roof measurement failed: {type(exc).__name__}: {exc}")
            return
        st.session_state["roof_measure_result"] = result
        st.session_state["roof_measure_original_result"] = result
        st.session_state["roof_measure_image_bytes"] = image_bytes
        st.session_state["roof_measure_file_name"] = overhead.name

    stored_result = st.session_state.get("roof_measure_result")
    stored_image_bytes = st.session_state.get("roof_measure_image_bytes")
    stored_file_name = st.session_state.get("roof_measure_file_name")
    if stored_result is not None and stored_image_bytes and stored_file_name:
        _render_measurement_result(
            stored_result,
            image_bytes=stored_image_bytes,
            file_name=stored_file_name,
        )


def _render_measurement_result(result: RoofMeasureResult, *, image_bytes: bytes, file_name: str) -> None:
    report = result.report
    measurement = report.measurement
    st.subheader("Measurement Result")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Total Area", "-" if measurement.total_area_sqft is None else f"{measurement.total_area_sqft:,.0f} sf")
    metric_col2.metric("Low / High", "-" if measurement.low_area_sqft is None else f"{measurement.low_area_sqft:,.0f} - {measurement.high_area_sqft:,.0f} sf")
    metric_col3.metric("Perimeter", "-" if measurement.total_perimeter_ft is None else f"{measurement.total_perimeter_ft:,.0f} ft")
    metric_col4.metric("Confidence", f"{measurement.confidence.get('overall_estimating', 0):.2f}")

    loaded = load_image_bytes(image_bytes, file_name=file_name)
    overlay = annotated_overlay(loaded.inference_image, mask=result.selected_mask, sections=measurement.sections)
    st.image(overlay, caption="Annotated inference-image overlay")

    if measurement.warnings:
        for warning in measurement.warnings:
            if warning.severity == "error":
                st.error(warning.message)
            else:
                st.warning(warning.message)

    if measurement.sections:
        section_rows = [
            {
                "section": section.section_id,
                "area_sqft": section.area_sqft,
                "perimeter_ft": section.perimeter_ft,
                "area_pixels": section.area_pixels,
                "confidence": section.confidence,
            }
            for section in measurement.sections
        ]
        st.dataframe(section_rows, width="stretch", hide_index=True)

    _render_visual_polygon_editor(result, image=loaded.inference_image)
    _render_polygon_editor(result)

    geojson = measurement_to_geojson(measurement)
    report_json = report_to_json(report)
    st.download_button("Download measurement report JSON", report_json, "roof_measurement_report.json", "application/json")
    st.download_button("Download polygon GeoJSON", geojson_to_string(geojson), "roof_measurement.geojson", "application/geo+json")
    st.download_button("Download annotated PNG", image_png_bytes(overlay), "roof_measurement_overlay.png", "image/png")


def _parse_point(value: str) -> tuple[float, float] | None:
    text = (value or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _render_polygon_editor(result: RoofMeasureResult) -> None:
    measurement = result.report.measurement
    if not measurement.sections:
        return
    with st.expander("Coordinate table fallback", expanded=False):
        st.caption(
            "Edit inference-image pixel coordinates for each roof section. "
            "Use this when the proposed mask clips an edge, includes extra pavement, or misses a small roof area."
        )
        rows = _section_vertex_rows(measurement.sections)
        edited_df = st.data_editor(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            key=f"roof_measure_vertex_editor_{result.report.id}",
            column_config={
                "section_id": st.column_config.TextColumn("Section"),
                "vertex_index": st.column_config.NumberColumn("Vertex", min_value=0, step=1),
                "x": st.column_config.NumberColumn("X pixel", step=1.0, format="%.2f"),
                "y": st.column_config.NumberColumn("Y pixel", step=1.0, format="%.2f"),
            },
        )
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button("Apply Polygon Edits", type="primary", width="stretch", key=f"roof_measure_apply_polygon_{result.report.id}"):
                try:
                    corrected_sections = _sections_from_vertex_rows(
                        measurement.sections,
                        edited_df.to_dict(orient="records"),
                    )
                    corrected_report = recalculate_report_from_corrected_sections(
                        result.report,
                        corrected_sections,
                    )
                except Exception as exc:
                    st.error(f"Could not apply polygon edits: {type(exc).__name__}: {exc}")
                    return
                st.session_state["roof_measure_result"] = RoofMeasureResult(
                    report=corrected_report,
                    selected_mask=None,
                    candidate_count=result.candidate_count,
                )
                st.rerun()
        with action_col2:
            if st.button("Reset to Mask Result", width="stretch", key=f"roof_measure_reset_polygon_{result.report.id}"):
                original = st.session_state.get("roof_measure_original_result")
                if original is not None:
                    st.session_state["roof_measure_result"] = original
                    st.rerun()


def _render_visual_polygon_editor(result: RoofMeasureResult, *, image: Image.Image) -> None:
    measurement = result.report.measurement
    if not measurement.sections:
        return
    with st.expander("Visual polygon editor", expanded=True):
        if st_canvas is None:
            st.info("Install streamlit-drawable-canvas to edit polygons visually. The coordinate table remains available below.")
            return
        st.caption(
            "Use transform mode to move or resize the current outline. Use replacement mode to draw a new roof polygon on the image. "
            "Apply the canvas outline to recalculate the measurement."
        )
        canvas_width, canvas_height, scale_x, scale_y = _canvas_dimensions(image)
        editor_mode = st.radio(
            "Editor mode",
            ["Transform current outline", "Draw replacement outline"],
            horizontal=True,
            key=f"roof_measure_canvas_mode_{result.report.id}",
        )
        drawing_mode = "transform" if editor_mode == "Transform current outline" else "polygon"
        initial_drawing = (
            _sections_to_canvas_initial_drawing(measurement.sections, scale_x=scale_x, scale_y=scale_y)
            if drawing_mode == "transform"
            else {"version": "4.4.0", "objects": []}
        )
        canvas_result = st_canvas(
            fill_color="rgba(229, 40, 40, 0.20)",
            stroke_width=3,
            stroke_color="#009760",
            background_image=image,
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode=drawing_mode,
            initial_drawing=initial_drawing,
            display_toolbar=True,
            key=f"roof_measure_canvas_{result.report.id}_{drawing_mode}",
        )
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button("Apply Canvas Outline", type="primary", width="stretch", key=f"roof_measure_apply_canvas_{result.report.id}"):
                canvas_json = getattr(canvas_result, "json_data", None)
                try:
                    corrected_sections = _canvas_json_to_sections(
                        canvas_json,
                        original_sections=measurement.sections,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    corrected_report = recalculate_report_from_corrected_sections(
                        result.report,
                        corrected_sections,
                        correction_note=f"Estimator applied visual canvas edits in {editor_mode.lower()}.",
                    )
                except Exception as exc:
                    st.error(f"Could not apply canvas outline: {type(exc).__name__}: {exc}")
                    return
                st.session_state["roof_measure_result"] = RoofMeasureResult(
                    report=corrected_report,
                    selected_mask=None,
                    candidate_count=result.candidate_count,
                )
                st.rerun()
        with action_col2:
            if st.button("Reset Visual Edits", width="stretch", key=f"roof_measure_canvas_reset_{result.report.id}"):
                original = st.session_state.get("roof_measure_original_result")
                if original is not None:
                    st.session_state["roof_measure_result"] = original
                    st.rerun()


def _section_vertex_rows(sections: list[RoofSection]) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    for section in sections:
        polygon = section.polygon[:-1] if section.polygon and section.polygon[0] == section.polygon[-1] else section.polygon
        for index, (x, y) in enumerate(polygon):
            rows.append(
                {
                    "section_id": section.section_id,
                    "vertex_index": index,
                    "x": float(x),
                    "y": float(y),
                }
            )
    return rows


def _canvas_dimensions(image: Image.Image, *, max_width: int = 1000) -> tuple[int, int, float, float]:
    width, height = image.size
    if width <= 0 or height <= 0:
        return 1, 1, 1.0, 1.0
    canvas_width = min(width, max_width)
    scale = canvas_width / width
    canvas_height = max(1, round(height * scale))
    return int(canvas_width), int(canvas_height), scale, scale


def _sections_to_canvas_initial_drawing(sections: list[RoofSection], *, scale_x: float, scale_y: float) -> dict:
    objects = []
    for section in sections:
        points = section.polygon[:-1] if section.polygon and section.polygon[0] == section.polygon[-1] else section.polygon
        if len(points) < 3:
            continue
        path = []
        for index, (x, y) in enumerate(points):
            command = "M" if index == 0 else "L"
            path.append([command, float(x) * scale_x, float(y) * scale_y])
        path.append(["Z"])
        objects.append(
            {
                "type": "path",
                "version": "4.4.0",
                "originX": "left",
                "originY": "top",
                "left": 0,
                "top": 0,
                "fill": "rgba(229, 40, 40, 0.20)",
                "stroke": "#009760",
                "strokeWidth": 3,
                "strokeLineCap": "round",
                "strokeLineJoin": "round",
                "path": path,
                "objectCaching": False,
                "selectable": True,
            }
        )
    return {"version": "4.4.0", "objects": objects}


def _canvas_json_to_sections(
    canvas_json: dict | None,
    *,
    original_sections: list[RoofSection],
    scale_x: float,
    scale_y: float,
) -> list[RoofSection]:
    objects = list((canvas_json or {}).get("objects") or [])
    if not objects:
        raise ValueError("No canvas polygon was found. Draw or transform an outline before applying.")
    section_templates = original_sections or []
    corrected_sections: list[RoofSection] = []
    for index, obj in enumerate(objects, start=1):
        points = _object_points_from_canvas(obj)
        if len(points) < 3:
            continue
        image_points = [
            (
                float(x) / scale_x if scale_x else float(x),
                float(y) / scale_y if scale_y else float(y),
            )
            for x, y in points
        ]
        template = section_templates[min(index - 1, len(section_templates) - 1)] if section_templates else None
        section_id = template.section_id if template else f"section-{index}"
        if template is None:
            template = RoofSection(
                section_id=section_id,
                polygon=[],
                area_pixels=0.0,
                perimeter_pixels=0.0,
                confidence=0.65,
            )
        corrected_sections.append(
            template.model_copy(
                deep=True,
                update={
                    "section_id": section_id,
                    "polygon": image_points,
                    "holes": [],
                },
            )
        )
    if not corrected_sections:
        raise ValueError("No usable canvas polygon was found. Use the polygon tool or coordinate table fallback.")
    return corrected_sections


def _object_points_from_canvas(obj: dict) -> list[tuple[float, float]]:
    object_type = str(obj.get("type") or "").lower()
    if object_type == "rect":
        return _rect_points_from_canvas(obj)
    if object_type == "polygon":
        return _polygon_points_from_canvas(obj)
    if object_type == "path":
        return _path_points_from_canvas(obj)
    return []


def _rect_points_from_canvas(obj: dict) -> list[tuple[float, float]]:
    left = float(obj.get("left") or 0)
    top = float(obj.get("top") or 0)
    width = float(obj.get("width") or 0) * float(obj.get("scaleX") or 1)
    height = float(obj.get("height") or 0) * float(obj.get("scaleY") or 1)
    if width <= 0 or height <= 0:
        return []
    return [(left, top), (left + width, top), (left + width, top + height), (left, top + height)]


def _polygon_points_from_canvas(obj: dict) -> list[tuple[float, float]]:
    raw_points = obj.get("points") or []
    left = float(obj.get("left") or 0)
    top = float(obj.get("top") or 0)
    scale_x = float(obj.get("scaleX") or 1)
    scale_y = float(obj.get("scaleY") or 1)
    path_offset = obj.get("pathOffset") or {}
    offset_x = float(path_offset.get("x") or 0)
    offset_y = float(path_offset.get("y") or 0)
    points: list[tuple[float, float]] = []
    for point in raw_points:
        try:
            raw_x = float(point.get("x"))
            raw_y = float(point.get("y"))
        except (AttributeError, TypeError, ValueError):
            continue
        # Fabric polygon JSON stores points in object-local space. The path
        # offset branch handles the common serialized shape; when absent this
        # still gives a sensible approximation for simple polygon objects.
        x = left + (raw_x - offset_x) * scale_x
        y = top + (raw_y - offset_y) * scale_y
        points.append((x, y))
    return points


def _path_points_from_canvas(obj: dict) -> list[tuple[float, float]]:
    path = obj.get("path") or []
    scale_x = float(obj.get("scaleX") or 1)
    scale_y = float(obj.get("scaleY") or 1)
    points: list[tuple[float, float]] = []
    for command in path:
        if not isinstance(command, list) or not command:
            continue
        op = str(command[0]).upper()
        if op in {"M", "L"} and len(command) >= 3:
            try:
                points.append((float(command[1]) * scale_x, float(command[2]) * scale_y))
            except (TypeError, ValueError):
                continue
        elif op in {"Q", "C"} and len(command) >= 3:
            try:
                points.append((float(command[-2]) * scale_x, float(command[-1]) * scale_y))
            except (TypeError, ValueError):
                continue
    return points


def _sections_from_vertex_rows(original_sections: list[RoofSection], rows: list[dict]) -> list[RoofSection]:
    by_section: dict[str, list[tuple[int, float, float]]] = {}
    for row in rows:
        section_id = str(row.get("section_id") or "").strip()
        if not section_id:
            continue
        try:
            vertex_index = int(row.get("vertex_index"))
            x = float(row.get("x"))
            y = float(row.get("y"))
        except (TypeError, ValueError):
            continue
        by_section.setdefault(section_id, []).append((vertex_index, x, y))
    original_by_id = {section.section_id: section for section in original_sections}
    corrected_sections: list[RoofSection] = []
    for section_id, vertices in by_section.items():
        points = [(x, y) for _, x, y in sorted(vertices, key=lambda item: item[0])]
        if len(points) < 3:
            continue
        original = original_by_id.get(section_id)
        if original is None:
            original = RoofSection(
                section_id=section_id,
                polygon=[],
                area_pixels=0.0,
                perimeter_pixels=0.0,
                confidence=0.65,
            )
        corrected_sections.append(
            original.model_copy(
                deep=True,
                update={
                    "polygon": points,
                    "holes": [],
                },
            )
        )
    if not corrected_sections:
        raise ValueError("At least one section must have three valid vertices.")
    return corrected_sections
