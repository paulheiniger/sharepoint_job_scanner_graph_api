from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from PIL import Image


def _install_streamlit_image_to_url_compat() -> None:
    try:
        import streamlit.elements.image as st_image
    except Exception:
        return
    if hasattr(st_image, "image_to_url"):
        return

    def image_to_url(image, width, clamp, channels, output_format, image_id):  # noqa: ARG001
        from io import BytesIO

        from streamlit.runtime import Runtime

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return Runtime.instance().media_file_mgr.add(
            buffer.getvalue(),
            "image/png",
            str(image_id),
            file_name=f"{image_id}.png",
        )

    st_image.image_to_url = image_to_url


_install_streamlit_image_to_url_compat()

try:
    from streamlit_drawable_canvas import st_canvas
except ImportError:  # pragma: no cover - exercised in environments without the optional component.
    st_canvas = None

from .ai_points import suggest_roof_prompt_points
from .exports import geojson_to_string, measurement_to_geojson, report_to_json
from .image_io import load_image_bytes, uploaded_file_bytes
from .map_reference import MapboxReferenceProvider
from .models import RoofMeasureRequest, RoofSection
from .service import RoofMeasureResult, measure_roof_from_overhead_image, recalculate_report_from_corrected_sections
from .visualization import annotated_overlay, image_png_bytes, prompt_points_overlay


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
- Can fetch calibrated Mapbox satellite imagery from an address or use uploaded screenshots with a visible scale bar.
- SAM 2 is behind a provider interface but is not required in this runtime.
- The current fallback mask is a prompt-centered candidate, not a roof-aware model.
- Oblique images are stored as references only in this phase.
            """.strip()
        )

    address = st.text_input("Address", key="roof_measure_address")
    job_id = st.text_input("Job ID (optional)", key="roof_measure_job_id")
    mapbox_token = (os.getenv("MAPBOX_TOKEN") or os.getenv("MAPBOX_ACCESS_TOKEN") or "").strip()
    extent_options = {
        "Whole site / school": 17.5,
        "Large campus / context": 16.5,
        "Single building detail": 19.0,
        "Close roof detail": 20.0,
    }
    map_col1, map_col2, map_col3, map_col4 = st.columns([1.4, 1, 1, 2])
    with map_col1:
        map_extent = st.selectbox(
            "Image extent",
            list(extent_options),
            index=0,
            help="Use wider extents for schools, campuses, and multi-building sites.",
            key="roof_measure_map_extent",
        )
    with map_col2:
        override_zoom = st.checkbox("Custom zoom", value=False, key="roof_measure_custom_zoom")
    with map_col3:
        preset_zoom = extent_options[map_extent]
        map_zoom = (
            st.number_input("Map zoom", min_value=14.0, max_value=22.0, value=preset_zoom, step=0.25, key="roof_measure_map_zoom")
            if override_zoom
            else preset_zoom
        )
        if not override_zoom:
            st.metric("Zoom", f"{map_zoom:g}")
    with map_col4:
        fetch_map = st.button(
            "Fetch Satellite Image",
            width="stretch",
            disabled=not bool(address.strip() and mapbox_token),
            help="Uses Mapbox and the address to fetch calibrated north-up satellite imagery.",
            key="roof_measure_fetch_mapbox",
        )
        if not mapbox_token:
            st.caption("Set MAPBOX_TOKEN or MAPBOX_ACCESS_TOKEN to fetch imagery by address.")
        elif not address.strip():
            st.caption("Enter an address to fetch calibrated satellite imagery.")
        else:
            st.caption("Use whole-site extent first. Refetch at building detail only after the full roof area is visible.")
    if fetch_map:
        provider = MapboxReferenceProvider(mapbox_token)
        fetched = provider.static_satellite_image(address, zoom=float(map_zoom))
        if not fetched.ok or not fetched.image_bytes:
            st.warning(fetched.warning or "Could not fetch Mapbox imagery.")
        else:
            st.session_state["roof_measure_mapbox_image_bytes"] = fetched.image_bytes
            st.session_state["roof_measure_mapbox_file_name"] = fetched.file_name
            st.session_state["roof_measure_mapbox_pixels_per_foot"] = fetched.pixels_per_foot
            st.session_state["roof_measure_mapbox_warning"] = fetched.warning
            st.success("Fetched calibrated satellite imagery from Mapbox.")
    overhead = st.file_uploader("Top-down or near-top-down aerial image", type=["jpg", "jpeg", "png"], key="roof_measure_overhead")
    oblique_files = st.file_uploader(
        "Optional oblique drone/reference images",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="roof_measure_oblique",
    )
    if oblique_files:
        st.caption(f"{len(oblique_files):,} oblique/reference image(s) attached. Milestone 1 does not use these for primary area.")

    mapbox_image_bytes = st.session_state.get("roof_measure_mapbox_image_bytes")
    mapbox_file_name = str(st.session_state.get("roof_measure_mapbox_file_name") or "mapbox-satellite.png")
    mapbox_pixels_per_foot = st.session_state.get("roof_measure_mapbox_pixels_per_foot")

    if overhead is None and not mapbox_image_bytes:
        st.info("Upload an overhead image or fetch calibrated satellite imagery by address to start.")
        return

    image_source = "uploaded"
    if overhead is not None:
        image_bytes = uploaded_file_bytes(overhead)
        image_name = overhead.name
        metadata_pixels_per_foot = None
    else:
        image_bytes = bytes(mapbox_image_bytes)
        image_name = mapbox_file_name
        metadata_pixels_per_foot = float(mapbox_pixels_per_foot or 0) or None
        image_source = "mapbox"
    try:
        loaded = load_image_bytes(image_bytes, file_name=image_name)
    except Exception as exc:
        st.error(f"Could not read overhead image: {exc}")
        return

    st.image(loaded.inference_image, caption=f"{image_name} ({loaded.metadata.inference_width} x {loaded.metadata.inference_height} inference pixels)")
    if image_source == "mapbox" and metadata_pixels_per_foot:
        st.caption(f"Mapbox metadata calibration: {metadata_pixels_per_foot:.4f} pixels per foot.")
    if loaded.metadata.quality_flags:
        st.warning("Image quality flags: " + ", ".join(loaded.metadata.quality_flags))

    st.subheader("Prompt and Calibration")
    ai_points_col1, ai_points_col2 = st.columns([1, 2])
    openai_available = bool(os.getenv("OPENAI_API_KEY"))
    with ai_points_col1:
        suggest_points = st.button(
            "Suggest Roof Points with AI",
            width="stretch",
            disabled=not openai_available,
            help="Sends the displayed overhead image to the configured OpenAI model to suggest SAM positive and exclude points.",
            key="roof_measure_suggest_ai_points",
        )
    with ai_points_col2:
        if openai_available:
            st.caption("AI suggestions fill the point fields below. Review them before measuring.")
        else:
            st.caption("Set OPENAI_API_KEY to enable AI point suggestions.")
    if suggest_points:
        suggestion = suggest_roof_prompt_points(loaded.inference_image, address=address)
        if suggestion.warnings:
            for warning in suggestion.warnings:
                st.warning(warning)
        if suggestion.positive_points:
            primary = suggestion.positive_points[0]
            st.session_state["roof_measure_prompt_x"] = int(round(primary[0]))
            st.session_state["roof_measure_prompt_y"] = int(round(primary[1]))
            st.session_state["roof_measure_extra_positive_points"] = _format_points_text(suggestion.positive_points[1:])
            st.session_state["roof_measure_negative_points"] = _format_points_text(suggestion.negative_points)
            st.session_state["roof_measure_ai_point_notes"] = {
                "confidence": suggestion.confidence,
                "notes": suggestion.notes,
                "positive_count": len(suggestion.positive_points),
                "negative_count": len(suggestion.negative_points),
            }
            st.rerun()
        else:
            st.warning("AI did not return usable roof points for this image.")
    ai_point_notes = st.session_state.get("roof_measure_ai_point_notes")
    if isinstance(ai_point_notes, dict):
        st.caption(
            "AI point suggestion: "
            f"{ai_point_notes.get('positive_count', 0)} roof point(s), "
            f"{ai_point_notes.get('negative_count', 0)} exclude point(s), "
            f"confidence {float(ai_point_notes.get('confidence') or 0):.2f}. "
            + str(ai_point_notes.get("notes") or "")
        )
    _render_prompt_point_picker(loaded.inference_image, image_key=loaded.metadata.image_id)
    st.caption(
        "If this is a Google Earth-style screenshot with a visible scale bar, leave manual calibration blank. "
        "The app will try to read the scale label and detect the scale bar automatically."
    )
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
    point_col1, point_col2 = st.columns(2)
    with point_col1:
        extra_positive_points_text = st.text_area(
            "Additional roof points",
            value="",
            placeholder="One x,y per line. Use this for multiple school roof sections.",
            height=90,
            key="roof_measure_extra_positive_points",
        )
    with point_col2:
        negative_points_text = st.text_area(
            "Exclude points",
            value="",
            placeholder="One x,y per line on parking, grass, courtyards, or wrong masks.",
            height=90,
            key="roof_measure_negative_points",
        )
    preview_positive_points = [
        (float(prompt_x), float(prompt_y)),
        *_parse_points_text(extra_positive_points_text),
    ]
    preview_negative_points = _parse_points_text(negative_points_text)
    if preview_positive_points or preview_negative_points:
        with st.expander("Review Prompt Points", expanded=True):
            st.caption("Green points should be inside roof surfaces. Red points should be inside areas to exclude, such as parking, grass, roads, or courtyards.")
            st.image(
                prompt_points_overlay(
                    loaded.inference_image,
                    positive_points=preview_positive_points,
                    negative_points=preview_negative_points,
                ),
                caption="SAM prompt points",
            )

    cal_col1, cal_col2, cal_col3 = st.columns(3)
    with cal_col1:
        known_length = st.number_input("Known length (ft)", min_value=0.0, value=0.0, step=1.0, key="roof_measure_known_length")
    with cal_col2:
        point_a_text = st.text_input("Calibration point A x,y", value="", placeholder="120, 400", key="roof_measure_cal_a")
    with cal_col3:
        point_b_text = st.text_input("Calibration point B x,y", value="", placeholder="620, 400", key="roof_measure_cal_b")
    scale_hint = st.text_input(
        "Scale label override (optional)",
        value="",
        placeholder="Example: 100 ft",
        help="Use this if the scale bar is visible but OCR cannot read the Google Earth label.",
        key="roof_measure_scale_hint",
    )
    use_ai_scale_reader = st.checkbox(
        "Use AI to read scale bar if OCR fails",
        value=True,
        help="Sends only the bottom scale-bar crop to the configured OpenAI model when local OCR/bar detection fails.",
        key="roof_measure_use_ai_scale_reader",
    )

    controls_col1, controls_col2, controls_col3 = st.columns(3)
    with controls_col1:
        simplification_tolerance = st.slider(
            "Boundary smoothing",
            min_value=0.0,
            max_value=40.0,
            value=15.0,
            step=1.0,
            help="Higher values remove noisy mask stair-steps and favor straight roof/building edges.",
            key="roof_measure_simplification_tolerance",
        )
    with controls_col2:
        minimum_section_area = st.number_input("Minimum section size (pixels)", min_value=1.0, value=400.0, step=100.0)
    with controls_col3:
        edge_snap_strength = st.slider("Edge snap strength", min_value=0.0, max_value=20.0, value=0.0, step=0.5)
    configured_segmenter = (os.getenv("ROOF_MEASURE_SEGMENTER") or "manual_fallback").strip().lower()
    segmenter_options = ["manual_fallback", "sam2_remote"]
    segmenter_index = 1 if configured_segmenter in {"sam2", "sam_2", "sam 2", "sam2_remote", "remote_sam2"} else 0
    segmenter_name = st.selectbox(
        "Segmentation provider",
        segmenter_options,
        index=segmenter_index,
        help="Use sam2_remote only when the local SAM2 segmentation service is running.",
        key="roof_measure_segmenter_name",
    )
    if segmenter_name == "sam2_remote":
        sam2_url = os.getenv("SAM2_SEGMENTATION_URL")
        st.caption(
            "SAM2 service: "
            + (sam2_url if sam2_url else "not configured. Set SAM2_SEGMENTATION_URL before using this provider.")
        )

    point_a = _parse_point(point_a_text)
    point_b = _parse_point(point_b_text)
    positive_points = [
        (float(prompt_x), float(prompt_y)),
        *_parse_points_text(extra_positive_points_text),
    ]
    negative_points = _parse_points_text(negative_points_text)
    request = RoofMeasureRequest(
        address=address,
        job_id=job_id or None,
        overhead_image_name=image_name,
        positive_points=positive_points,
        negative_points=negative_points,
        calibration_length_feet=known_length if known_length > 0 else None,
        calibration_point_a=point_a,
        calibration_point_b=point_b,
        metadata_pixels_per_foot=metadata_pixels_per_foot,
        scale_bar_label_hint=scale_hint.strip() or None,
        use_ai_scale_reader=use_ai_scale_reader,
        simplification_tolerance=simplification_tolerance,
        minimum_section_area_pixels=minimum_section_area,
        edge_snap_strength=edge_snap_strength,
        segmenter_name=segmenter_name,
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
        st.session_state["roof_measure_file_name"] = image_name

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


def _parse_points_text(value: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for raw_line in (value or "").replace(";", "\n").splitlines():
        point = _parse_point(raw_line)
        if point is not None:
            points.append(point)
    return points


def _format_points_text(points: list[tuple[float, float]]) -> str:
    return "\n".join(f"{int(round(x))},{int(round(y))}" for x, y in points)


def _render_prompt_point_picker(image: Image.Image, *, image_key: str) -> None:
    if st_canvas is None:
        st.info("Install streamlit-drawable-canvas to click roof and exclude points. Coordinate fields remain available below.")
        return
    with st.expander("Click Roof / Exclude Points", expanded=True):
        st.caption(
            "Click inside roof surfaces on the green canvas. Click parking, grass, roads, courtyards, or wrong areas on the red canvas. "
            "Then apply the clicked points before measuring."
        )
        canvas_width, canvas_height, scale_x, scale_y = _canvas_dimensions(image, max_width=780)
        current_positive = _current_prompt_positive_points(image)
        current_negative = _parse_points_text(str(st.session_state.get("roof_measure_negative_points") or ""))
        st.markdown("**Roof Points**")
        roof_canvas = st_canvas(
            fill_color="rgba(0, 151, 96, 0.85)",
            stroke_width=2,
            stroke_color="#009760",
            background_image=image,
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode="point",
            initial_drawing=_points_to_canvas_initial_drawing(current_positive, scale_x=scale_x, scale_y=scale_y, color="#009760"),
            display_toolbar=True,
            point_display_radius=8,
            key=f"roof_measure_positive_point_canvas_{image_key}",
        )
        st.markdown("**Exclude Points**")
        exclude_canvas = st_canvas(
            fill_color="rgba(229, 40, 40, 0.85)",
            stroke_width=2,
            stroke_color="#e52828",
            background_image=image,
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode="point",
            initial_drawing=_points_to_canvas_initial_drawing(current_negative, scale_x=scale_x, scale_y=scale_y, color="#e52828"),
            display_toolbar=True,
            point_display_radius=8,
            key=f"roof_measure_negative_point_canvas_{image_key}",
        )
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button("Apply Clicked Points", type="primary", width="stretch", key=f"roof_measure_apply_clicked_points_{image_key}"):
                positive_points = _canvas_json_to_points(getattr(roof_canvas, "json_data", None), scale_x=scale_x, scale_y=scale_y)
                negative_points = _canvas_json_to_points(getattr(exclude_canvas, "json_data", None), scale_x=scale_x, scale_y=scale_y)
                if not positive_points:
                    st.warning("Click at least one roof point before applying.")
                    return
                first = positive_points[0]
                st.session_state["roof_measure_prompt_x"] = int(round(first[0]))
                st.session_state["roof_measure_prompt_y"] = int(round(first[1]))
                st.session_state["roof_measure_extra_positive_points"] = _format_points_text(positive_points[1:])
                st.session_state["roof_measure_negative_points"] = _format_points_text(negative_points)
                st.session_state["roof_measure_ai_point_notes"] = {
                    "confidence": 1.0,
                    "notes": "Estimator clicked prompt points.",
                    "positive_count": len(positive_points),
                    "negative_count": len(negative_points),
                }
                st.rerun()
        with action_col2:
            if st.button("Clear Clicked Points", width="stretch", key=f"roof_measure_clear_clicked_points_{image_key}"):
                st.session_state["roof_measure_extra_positive_points"] = ""
                st.session_state["roof_measure_negative_points"] = ""
                st.session_state.pop("roof_measure_ai_point_notes", None)
                st.rerun()


def _current_prompt_positive_points(image: Image.Image) -> list[tuple[float, float]]:
    width, height = image.size
    explicit_extra_points = _parse_points_text(str(st.session_state.get("roof_measure_extra_positive_points") or ""))
    has_suggestion_or_clicks = isinstance(st.session_state.get("roof_measure_ai_point_notes"), dict)
    try:
        x = float(st.session_state.get("roof_measure_prompt_x", width / 2))
        y = float(st.session_state.get("roof_measure_prompt_y", height / 2))
    except (TypeError, ValueError):
        x, y = width / 2, height / 2
    is_default_center = abs(x - width / 2) < 1 and abs(y - height / 2) < 1
    if not has_suggestion_or_clicks and not explicit_extra_points and is_default_center:
        return []
    primary = (min(max(x, 0.0), width - 1.0), min(max(y, 0.0), height - 1.0))
    return [primary, *explicit_extra_points]


def _points_to_canvas_initial_drawing(
    points: list[tuple[float, float]],
    *,
    scale_x: float,
    scale_y: float,
    color: str,
) -> dict:
    objects = []
    for x, y in points:
        canvas_x = float(x) * scale_x
        canvas_y = float(y) * scale_y
        objects.append(
            {
                "type": "circle",
                "version": "4.4.0",
                "originX": "center",
                "originY": "center",
                "left": canvas_x,
                "top": canvas_y,
                "width": 16,
                "height": 16,
                "radius": 8,
                "fill": color,
                "stroke": color,
                "strokeWidth": 2,
                "scaleX": 1,
                "scaleY": 1,
            }
        )
    return {"version": "4.4.0", "objects": objects}


def _canvas_json_to_points(canvas_json: dict | None, *, scale_x: float, scale_y: float) -> list[tuple[float, float]]:
    if not isinstance(canvas_json, dict):
        return []
    points: list[tuple[float, float]] = []
    for obj in canvas_json.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        point = _canvas_object_center(obj)
        if point is None:
            continue
        x, y = point
        points.append((float(x) / scale_x if scale_x else float(x), float(y) / scale_y if scale_y else float(y)))
    return points


def _canvas_object_center(obj: dict) -> tuple[float, float] | None:
    try:
        left = float(obj.get("left") or 0)
        top = float(obj.get("top") or 0)
        origin_x = str(obj.get("originX") or "").lower()
        origin_y = str(obj.get("originY") or "").lower()
        width = float(obj.get("width") or 0) * float(obj.get("scaleX") or 1)
        height = float(obj.get("height") or 0) * float(obj.get("scaleY") or 1)
    except (TypeError, ValueError):
        return None
    x = left if origin_x == "center" else left + width / 2
    y = top if origin_y == "center" else top + height / 2
    return x, y


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
