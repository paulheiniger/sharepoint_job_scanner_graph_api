from __future__ import annotations

import math
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

from .ai_polygons import suggest_refined_roof_polygons, suggest_roof_polygons
from .ai_points import suggest_roof_prompt_points
from .exports import geojson_to_string, measurement_to_geojson, report_to_json
from .image_io import load_image_bytes, uploaded_file_bytes
from .map_reference import (
    BuildingFootprint,
    MapboxReferenceProvider,
    footprint_rings_to_image_pixels,
    geojson_building_footprints,
    openstreetmap_building_footprints,
    postgres_building_footprints,
)
from .geometry import straighten_architectural_ring
from .models import RoofMeasureRequest, RoofSection
from .polygonize import section_from_polygon
from .service import RoofMeasureResult, measure_roof_from_outline_polygons, measure_roof_from_overhead_image, recalculate_report_from_corrected_sections
from .visualization import annotated_overlay, footprint_overlay, image_png_bytes, prompt_points_overlay


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
- Oblique images are reference-only context for AI; measurements still come from the calibrated overhead image.
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
        measure_address = st.button(
            "Measure Roof",
            type="primary",
            width="stretch",
            disabled=not bool(address.strip() and mapbox_token),
            help="Fetches imagery, finds roof prompts, segments, smooths the outline, and opens the editable result.",
            key="roof_measure_address_auto_measure",
        )
        fetch_map = st.button(
            "Fetch Satellite Image Only",
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
    if fetch_map or measure_address:
        provider = MapboxReferenceProvider(mapbox_token)
        fetched = provider.static_satellite_image(address, zoom=float(map_zoom))
        if not fetched.ok or not fetched.image_bytes:
            st.warning(fetched.warning or "Could not fetch Mapbox imagery.")
        else:
            st.session_state["roof_measure_mapbox_image_bytes"] = fetched.image_bytes
            st.session_state["roof_measure_mapbox_file_name"] = fetched.file_name
            st.session_state["roof_measure_mapbox_pixels_per_foot"] = fetched.pixels_per_foot
            st.session_state["roof_measure_mapbox_warning"] = fetched.warning
            st.session_state["roof_measure_mapbox_context"] = {
                "latitude": fetched.latitude,
                "longitude": fetched.longitude,
                "zoom": fetched.zoom,
            }
            local_footprint_lookup = postgres_building_footprints(
                latitude=float(fetched.latitude),
                longitude=float(fetched.longitude),
            )
            mapbox_footprint_lookup = provider.building_footprints(
                latitude=float(fetched.latitude),
                longitude=float(fetched.longitude),
            )
            st.session_state["roof_measure_local_footprints"] = (
                local_footprint_lookup.footprints if local_footprint_lookup.ok else []
            )
            st.session_state["roof_measure_mapbox_footprints"] = (
                mapbox_footprint_lookup.footprints if mapbox_footprint_lookup.ok else []
            )
            warnings = [
                lookup.warning
                for lookup in (local_footprint_lookup, mapbox_footprint_lookup)
                if lookup.warning
            ]
            st.session_state["roof_measure_mapbox_footprint_warning"] = " ".join(warnings)
            st.session_state.pop("roof_measure_osm_footprints", None)
            st.session_state.pop("roof_measure_osm_footprint_attempted", None)
            st.session_state.pop("roof_measure_selected_footprint_ids", None)
            if measure_address:
                st.session_state["roof_measure_auto_measure_pending"] = True
            else:
                st.success("Fetched calibrated satellite imagery from Mapbox.")
    overhead = st.file_uploader("Top-down or near-top-down aerial image", type=["jpg", "jpeg", "png"], key="roof_measure_overhead")
    oblique_files = st.file_uploader(
        "Optional oblique drone/reference images",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="roof_measure_oblique",
    )
    reference_images, reference_image_warnings = _load_reference_images(oblique_files)
    if reference_image_warnings:
        for warning in reference_image_warnings:
            st.warning(warning)
    if reference_images:
        st.caption(
            f"{len(reference_images):,} oblique/reference image(s) attached. "
            "AI can use them as context; measurement and coordinates still come from the overhead image."
        )

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

    stored_result = st.session_state.get("roof_measure_result")
    stored_image_bytes = st.session_state.get("roof_measure_image_bytes")
    stored_file_name = st.session_state.get("roof_measure_file_name")
    if stored_result is not None and stored_image_bytes and stored_file_name:
        _render_measurement_result(
            stored_result,
            image_bytes=stored_image_bytes,
            file_name=stored_file_name,
            reference_images=reference_images,
        )
        return
    st.caption(f"Image: {image_name} ({loaded.metadata.inference_width} x {loaded.metadata.inference_height} inference pixels)")
    if image_source == "mapbox" and metadata_pixels_per_foot:
        st.caption(f"Mapbox metadata calibration: {metadata_pixels_per_foot:.4f} pixels per foot.")
    if loaded.metadata.quality_flags:
        st.warning("Image quality flags: " + ", ".join(loaded.metadata.quality_flags))

    footprint_polygons: list[list[tuple[float, float]]] = []
    if image_source == "mapbox":
        footprint_candidates = [
            *(st.session_state.get("roof_measure_local_footprints") or []),
            *(st.session_state.get("roof_measure_mapbox_footprints") or []),
        ]
        footprint_warning = str(st.session_state.get("roof_measure_mapbox_footprint_warning") or "")
        context = st.session_state.get("roof_measure_mapbox_context") or {}
        uploaded_footprints = st.file_uploader(
            "Building footprint GeoJSON (optional)",
            type=["geojson", "json"],
            key="roof_measure_footprint_geojson",
            help="Use a county, state, survey, or other trusted building-footprint export when map providers do not cover the site.",
        )
        if uploaded_footprints is not None:
            uploaded_lookup = geojson_building_footprints(uploaded_file_bytes(uploaded_footprints))
            if uploaded_lookup.ok:
                footprint_candidates = uploaded_lookup.footprints
                footprint_warning = uploaded_lookup.warning
            else:
                footprint_warning = uploaded_lookup.warning
        if not footprint_candidates and context.get("latitude") is not None and context.get("longitude") is not None:
            if st.button(
                "Try OpenStreetMap Building Footprints",
                key="roof_measure_try_osm_footprints",
                help="Optional public-data lookup. Upload GeoJSON if this site is not mapped or the public service is unavailable.",
            ):
                with st.spinner("Looking up public building footprints..."):
                    osm_lookup = openstreetmap_building_footprints(
                        latitude=float(context["latitude"]),
                        longitude=float(context["longitude"]),
                    )
                st.session_state["roof_measure_osm_footprint_attempted"] = True
                st.session_state["roof_measure_osm_footprints"] = osm_lookup.footprints if osm_lookup.ok else []
            footprint_candidates = st.session_state.get("roof_measure_osm_footprints") or []
            if not footprint_candidates:
                st.info(
                    "No automatic building footprint is available for this site. Upload trusted GeoJSON from county/state GIS, a survey source, or another approved source."
                )
        if footprint_warning and footprint_candidates:
            st.caption(footprint_warning)
        if footprint_candidates and context.get("latitude") is not None and context.get("longitude") is not None and context.get("zoom") is not None:
            st.subheader("Building Footprint Prior")
            candidate_by_id = {
                candidate.footprint_id: candidate
                for candidate in footprint_candidates
                if isinstance(candidate, BuildingFootprint)
            }
            if "roof_measure_selected_footprint_ids" not in st.session_state:
                st.session_state["roof_measure_selected_footprint_ids"] = list(candidate_by_id)[:1]
            selected_ids = st.multiselect(
                "Target building footprint(s)",
                options=list(candidate_by_id),
                format_func=lambda footprint_id: candidate_by_id[footprint_id].label,
                key="roof_measure_selected_footprint_ids",
                help="Select only the roof masses intended for this estimate. The selection constrains the segmentation mask.",
            )
            for footprint_id in selected_ids:
                candidate = candidate_by_id[footprint_id]
                footprint_polygons.extend(
                    footprint_rings_to_image_pixels(
                        candidate.rings,
                        center_latitude=float(context["latitude"]),
                        center_longitude=float(context["longitude"]),
                        zoom=float(context["zoom"]),
                        width=loaded.metadata.inference_width,
                        height=loaded.metadata.inference_height,
                    )
                )
            if footprint_polygons:
                st.image(
                    footprint_overlay(loaded.inference_image, polygons=footprint_polygons),
                    caption="Selected building footprint prior. Blue boundary constrains the segmentation mask.",
                    width=min(loaded.metadata.inference_width, 1000),
                )
                st.caption("Footprints are a constraint and review aid, not an authoritative roof takeoff.")

    automatic_measure_clicked = st.button(
        "Measure Roof",
        type="primary",
        width="stretch",
        help="Runs AI roof prompting, segmentation, deterministic architectural smoothing, and optional AI cleanup before opening the editable outline.",
        key="roof_measure_auto_measure",
    )
    if automatic_measure_clicked:
        st.session_state["roof_measure_auto_measure_pending"] = True

    st.subheader("Advanced Measurement Controls")
    ai_points_col1, ai_points_col2 = st.columns([1, 2])
    openai_available = bool(os.getenv("OPENAI_API_KEY"))
    with ai_points_col1:
        suggest_points = st.button(
            "Suggest Roof Points with AI",
            width="stretch",
            disabled=not openai_available,
            help="Optional: sends the displayed overhead image to OpenAI to suggest positive/exclude points for the segmenter path.",
            key="roof_measure_suggest_ai_points",
        )
    with ai_points_col2:
        if openai_available:
            st.caption("Prompt points are only needed when using Measure with Segmenter. The AI outline path does not use them.")
        else:
            st.caption("Set OPENAI_API_KEY to enable AI point suggestions.")
    if suggest_points:
        suggestion = suggest_roof_prompt_points(
            loaded.inference_image,
            address=address,
            reference_images=reference_images,
        )
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
            st.session_state[f"roof_measure_point_action_{loaded.metadata.image_id}"] = "Move/delete points"
            st.session_state.pop("roof_measure_result", None)
            st.session_state.pop("roof_measure_original_result", None)
            _bump_prompt_points_revision()
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
    if stored_result is None:
        _render_prompt_point_picker(loaded.inference_image, image_key=loaded.metadata.image_id)
    else:
        st.caption("A measurement is active below. Edit the current roof outline in the result workspace, or reset the result to choose new segmenter prompt points.")
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
    use_ai_outline_cleanup = st.checkbox(
        "Attempt AI outline cleanup after automatic segmentation",
        value=False,
        disabled=not openai_available,
        help="Optional final pass. The deterministic smoothed outline is retained if AI cleanup times out or is invalid.",
        key="roof_measure_auto_ai_cleanup",
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
        footprint_polygons=footprint_polygons,
    )
    if st.session_state.pop("roof_measure_auto_measure_pending", False):
        try:
            with st.spinner("Finding roof surfaces and measuring the editable outline..."):
                result, workflow_notes = _measure_roof_automatically(
                    image_bytes=image_bytes,
                    image=loaded.inference_image,
                    request=request,
                    address=address,
                    reference_images=reference_images,
                    use_ai_outline_cleanup=use_ai_outline_cleanup,
                )
        except Exception as exc:
            st.error(f"Automatic roof measurement failed: {type(exc).__name__}: {exc}")
            return
        st.session_state["roof_measure_result"] = result
        st.session_state["roof_measure_original_result"] = result
        st.session_state["roof_measure_image_bytes"] = image_bytes
        st.session_state["roof_measure_file_name"] = image_name
        st.session_state["roof_measure_auto_workflow_notes"] = workflow_notes
        st.rerun()
    measure_col1, measure_col2 = st.columns(2)
    with measure_col1:
        if st.button(
            "Suggest Editable Outline with AI",
            type="primary",
            width="stretch",
            disabled=not openai_available,
            help="Sends the displayed image to OpenAI for straight-line roof polygons, then opens them in the visual editor.",
            key="roof_measure_suggest_ai_outline",
        ):
            suggestion = suggest_roof_polygons(
                loaded.inference_image,
                address=address,
                reference_images=reference_images,
            )
            if suggestion.warnings:
                for warning in suggestion.warnings:
                    st.warning(warning)
            if not suggestion.polygons:
                st.warning("AI did not return usable roof outlines for this image.")
            else:
                try:
                    result = measure_roof_from_outline_polygons(
                        image_bytes=image_bytes,
                        request=request,
                        polygons=suggestion.polygons,
                        model_name=suggestion.model_name,
                        model_version=suggestion.model_version,
                        outline_confidence=suggestion.confidence,
                        outline_notes=suggestion.notes,
                    )
                except Exception as exc:
                    st.error(f"AI outline measurement failed: {type(exc).__name__}: {exc}")
                    return
                st.session_state["roof_measure_result"] = result
                st.session_state["roof_measure_original_result"] = result
                st.session_state["roof_measure_image_bytes"] = image_bytes
                st.session_state["roof_measure_file_name"] = image_name
                st.rerun()
    with measure_col2:
        if st.button("Measure with Segmenter", width="stretch"):
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

def _measure_roof_automatically(
    *,
    image_bytes: bytes,
    image: Image.Image,
    request: RoofMeasureRequest,
    address: str,
    reference_images: list[Image.Image],
    use_ai_outline_cleanup: bool,
) -> tuple[RoofMeasureResult, list[str]]:
    """Run the default measure pipeline while retaining a valid deterministic result."""
    notes: list[str] = []
    positive_points: list[tuple[float, float]] = []
    negative_points: list[tuple[float, float]] = []
    if os.getenv("OPENAI_API_KEY"):
        point_suggestion = suggest_roof_prompt_points(
            image,
            address=address,
            reference_images=reference_images,
        )
        positive_points = point_suggestion.positive_points
        negative_points = point_suggestion.negative_points
        if positive_points:
            notes.append(f"AI selected {len(positive_points)} roof prompt point(s).")
        else:
            notes.append("AI roof prompting was unavailable; used a centered fallback prompt.")
        notes.extend(point_suggestion.warnings)
    else:
        notes.append("AI roof prompting is not configured; used a centered fallback prompt.")
    if not positive_points:
        positive_points = [(image.width / 2, image.height / 2)]

    automatic_request = request.model_copy(
        update={"positive_points": positive_points, "negative_points": negative_points}
    )
    result = measure_roof_from_overhead_image(
        image_bytes=image_bytes,
        request=automatic_request,
        selected_candidate_index=0,
    )
    if not result.report.measurement.sections:
        notes.append("Segmentation did not produce an editable roof boundary. Use the advanced outline tools to continue.")
        return result, notes

    straightened_sections = []
    for section in result.report.measurement.sections:
        corrected = section.model_copy(deep=True)
        corrected.polygon = straighten_architectural_ring(section.polygon)
        corrected.holes = [straighten_architectural_ring(hole) for hole in section.holes]
        straightened_sections.append(corrected)
    straightened_report = recalculate_report_from_corrected_sections(
        result.report,
        straightened_sections,
        correction_note="Automatic deterministic architectural edge straightening applied.",
    )
    result = RoofMeasureResult(
        report=straightened_report,
        selected_mask=result.selected_mask,
        candidate_count=result.candidate_count,
    )
    notes.append("Segmented boundary was simplified and straightened to architectural edges.")

    if not use_ai_outline_cleanup:
        return result, notes
    if not os.getenv("OPENAI_API_KEY"):
        notes.append("AI outline cleanup was skipped because OpenAI is not configured.")
        return result, notes
    try:
        cleanup = suggest_refined_roof_polygons(
            image,
            result.report.measurement.sections,
            address=address,
            reference_images=reference_images,
        )
        if not cleanup.polygons:
            notes.append("AI outline cleanup returned no usable boundary; kept the deterministic outline.")
            notes.extend(cleanup.warnings)
            return result, notes
        cleaned_sections = _sections_from_ai_polygons(result.report.measurement.sections, cleanup.polygons)
        cleaned_report = recalculate_report_from_corrected_sections(
            result.report,
            cleaned_sections,
            correction_note="Automatic AI outline cleanup applied after deterministic architectural smoothing.",
        )
        notes.append("AI outline cleanup completed.")
        return RoofMeasureResult(
            report=cleaned_report,
            selected_mask=None,
            candidate_count=result.candidate_count,
        ), notes
    except Exception as exc:
        notes.append(f"AI outline cleanup failed ({type(exc).__name__}); kept the deterministic outline.")
        return result, notes


def _render_measurement_result(
    result: RoofMeasureResult,
    *,
    image_bytes: bytes,
    file_name: str,
    reference_images: list[Image.Image] | None = None,
) -> None:
    report = result.report
    measurement = report.measurement
    st.subheader("Measurement Result")
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Total Area", "-" if measurement.total_area_sqft is None else f"{measurement.total_area_sqft:,.0f} sf")
    metric_col2.metric("Low / High", "-" if measurement.low_area_sqft is None else f"{measurement.low_area_sqft:,.0f} - {measurement.high_area_sqft:,.0f} sf")
    metric_col3.metric("Perimeter", "-" if measurement.total_perimeter_ft is None else f"{measurement.total_perimeter_ft:,.0f} ft")
    metric_col4.metric("Confidence", f"{measurement.confidence.get('overall_estimating', 0):.2f}")

    workflow_notes = st.session_state.get("roof_measure_auto_workflow_notes")
    if isinstance(workflow_notes, list) and workflow_notes:
        st.caption(" ".join(str(note) for note in workflow_notes if str(note).strip()))

    loaded = load_image_bytes(image_bytes, file_name=file_name)
    overlay = annotated_overlay(loaded.inference_image, mask=result.selected_mask, sections=measurement.sections)

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

    _render_corner_handle_editor(result, image=loaded.inference_image, reference_images=reference_images)
    with st.expander("Annotated overlay preview", expanded=False):
        st.image(overlay, caption="Current measurement overlay")
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


def _bump_prompt_points_revision() -> None:
    st.session_state["roof_measure_prompt_points_revision"] = _prompt_points_revision() + 1


def _prompt_points_revision() -> int:
    try:
        return int(st.session_state.get("roof_measure_prompt_points_revision", 0))
    except (TypeError, ValueError):
        return 0


def _load_reference_images(files: list | None) -> tuple[list[Image.Image], list[str]]:
    reference_images: list[Image.Image] = []
    warnings: list[str] = []
    for index, uploaded in enumerate((files or [])[:4], start=1):
        try:
            image_bytes = uploaded_file_bytes(uploaded)
            loaded = load_image_bytes(image_bytes, file_name=getattr(uploaded, "name", f"reference-{index}.jpg"))
            reference_images.append(loaded.inference_image)
        except Exception as exc:
            name = getattr(uploaded, "name", f"reference image {index}")
            warnings.append(f"Could not read reference image {name}: {type(exc).__name__}: {exc}")
    if files and len(files) > 4:
        warnings.append("Only the first 4 oblique/reference images are sent to AI to control cost and noise.")
    return reference_images, warnings


def _render_prompt_point_picker(image: Image.Image, *, image_key: str) -> None:
    if st_canvas is None:
        st.info("Install streamlit-drawable-canvas to click roof and exclude points. Coordinate fields remain available below.")
        return
    with st.container(border=True):
        st.markdown("#### Roof Image Workspace")
        st.caption(
            "Before measuring with the segmenter, add roof/exclude points on this image. "
            "Switch to move mode to drag AI-suggested points. "
            "Roof points belong inside roof surfaces; exclude points belong inside parking, grass, roads, courtyards, or wrong areas."
        )
        canvas_width, canvas_height, scale_x, scale_y = _canvas_dimensions(image, max_width=1000)
        current_positive = _current_prompt_positive_points(image)
        current_negative = _parse_points_text(str(st.session_state.get("roof_measure_negative_points") or ""))
        prompt_revision = _prompt_points_revision()
        prompt_background = (
            prompt_points_overlay(
                image,
                positive_points=current_positive,
                negative_points=current_negative,
            )
            if current_positive or current_negative
            else image
        )
        if current_positive or current_negative:
            st.caption(f"Showing {len(current_positive)} roof point(s) and {len(current_negative)} exclude point(s).")
            st.caption(
                "Roof points: "
                + (_format_points_text(current_positive).replace("\n", "; ") or "none")
                + " | Exclude points: "
                + (_format_points_text(current_negative).replace("\n", "; ") or "none")
            )
        mode_col, kind_col = st.columns([1, 1])
        with mode_col:
            point_action = st.radio(
                "Point action",
                ["Add points", "Move/delete points"],
                horizontal=True,
                key=f"roof_measure_point_action_{image_key}",
            )
        with kind_col:
            point_kind = st.radio(
                "Point type",
                ["Roof point", "Exclude point"],
                horizontal=True,
                key=f"roof_measure_point_kind_{image_key}",
            )
        point_color = "#009760" if point_kind == "Roof point" else "#e52828"
        point_fill = "rgba(0, 151, 96, 0.85)" if point_kind == "Roof point" else "rgba(229, 40, 40, 0.85)"
        drawing_mode = "transform" if point_action == "Move/delete points" else "point"
        prompt_canvas = st_canvas(
            fill_color=point_fill,
            stroke_width=2,
            stroke_color=point_color,
            background_image=_canvas_background_image(prompt_background, canvas_width, canvas_height),
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode=drawing_mode,
            initial_drawing=_prompt_points_to_canvas_initial_drawing(
                positive_points=current_positive,
                negative_points=current_negative,
                scale_x=scale_x,
                scale_y=scale_y,
            ),
            display_toolbar=True,
            point_display_radius=8,
            key=f"roof_measure_prompt_point_canvas_{image_key}_{drawing_mode}_{prompt_revision}",
        )
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button("Apply Clicked Points", type="primary", width="stretch", key=f"roof_measure_apply_clicked_points_{image_key}"):
                positive_points, negative_points = _canvas_json_to_prompt_points(
                    getattr(prompt_canvas, "json_data", None),
                    scale_x=scale_x,
                    scale_y=scale_y,
                    default_kind="positive" if point_kind == "Roof point" else "negative",
                )
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
                _bump_prompt_points_revision()
                st.rerun()
        with action_col2:
            if st.button("Clear Clicked Points", width="stretch", key=f"roof_measure_clear_clicked_points_{image_key}"):
                st.session_state["roof_measure_extra_positive_points"] = ""
                st.session_state["roof_measure_negative_points"] = ""
                st.session_state.pop("roof_measure_ai_point_notes", None)
                _bump_prompt_points_revision()
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


def _prompt_points_to_canvas_initial_drawing(
    *,
    positive_points: list[tuple[float, float]],
    negative_points: list[tuple[float, float]],
    scale_x: float,
    scale_y: float,
) -> dict:
    return {
        "version": "4.4.0",
        "objects": [
            *_points_to_canvas_initial_drawing(
                positive_points,
                scale_x=scale_x,
                scale_y=scale_y,
                color="#009760",
            ).get("objects", []),
            *_points_to_canvas_initial_drawing(
                negative_points,
                scale_x=scale_x,
                scale_y=scale_y,
                color="#e52828",
            ).get("objects", []),
        ],
    }


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


def _canvas_json_to_prompt_points(
    canvas_json: dict | None,
    *,
    scale_x: float,
    scale_y: float,
    default_kind: str = "positive",
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    positive_points: list[tuple[float, float]] = []
    negative_points: list[tuple[float, float]] = []
    if not isinstance(canvas_json, dict):
        return positive_points, negative_points
    for obj in canvas_json.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        point = _canvas_object_center(obj)
        if point is None:
            continue
        x, y = point
        scaled_point = (float(x) / scale_x if scale_x else float(x), float(y) / scale_y if scale_y else float(y))
        kind = _canvas_object_point_kind(obj, default_kind=default_kind)
        if kind == "negative":
            negative_points.append(scaled_point)
        else:
            positive_points.append(scaled_point)
    return positive_points, negative_points


def _canvas_object_point_kind(obj: dict, *, default_kind: str = "positive") -> str:
    color_text = " ".join(
        str(obj.get(key) or "").lower()
        for key in ("fill", "stroke")
    )
    if "#e52828" in color_text or "229, 40, 40" in color_text or "rgb(229" in color_text:
        return "negative"
    if "#009760" in color_text or "0, 151, 96" in color_text or "rgb(0" in color_text:
        return "positive"
    return "negative" if default_kind == "negative" else "positive"


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
    with st.expander("Advanced coordinate fallback", expanded=False):
        st.caption(
            "Debug/support fallback for direct pixel-coordinate edits. Normal edits should use the visual corner tools above."
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
    with st.expander("Advanced outline editor", expanded=False):
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
            background_image=_canvas_background_image(image, canvas_width, canvas_height),
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


def _render_corner_handle_editor(
    result: RoofMeasureResult,
    *,
    image: Image.Image,
    reference_images: list[Image.Image] | None = None,
) -> None:
    measurement = result.report.measurement
    if not measurement.sections:
        return
    with st.container(border=True):
        st.markdown("#### Roof Image Workspace")
        if st_canvas is None:
            st.info("Install streamlit-drawable-canvas to drag roof corners. The coordinate table remains available below.")
            return
        st.caption(
            "Clean the current measurement, then move, delete, or add corner dots directly on the same image."
        )
        straighten_col, cleanup_col, reset_col = st.columns([1, 1, 1])
        openai_available = bool(os.getenv("OPENAI_API_KEY"))
        with straighten_col:
            if st.button(
                "Straighten Architectural Edges",
                width="stretch",
                help="Fits near-orthogonal edges to the roof's dominant building angle while keeping each section within 3% of its measured area.",
                key=f"roof_measure_architectural_cleanup_{result.report.id}",
            ):
                corrected_sections = []
                for section in measurement.sections:
                    corrected = section.model_copy(deep=True)
                    corrected.polygon = straighten_architectural_ring(section.polygon)
                    corrected.holes = [straighten_architectural_ring(hole) for hole in section.holes]
                    corrected_sections.append(corrected)
                corrected_report = recalculate_report_from_corrected_sections(
                    result.report,
                    corrected_sections,
                    correction_note="Applied deterministic architectural edge straightening with a 3% per-section area guard.",
                )
                st.session_state["roof_measure_result"] = RoofMeasureResult(
                    report=corrected_report,
                    selected_mask=result.selected_mask,
                    candidate_count=result.candidate_count,
                )
                st.rerun()
        with cleanup_col:
            if st.button(
                "Clean Current Outline with AI",
                width="stretch",
                disabled=not openai_available,
                help="Starts from the current measured outline and asks AI to simplify or move vertices to visible roof edges.",
                key=f"roof_measure_ai_cleanup_workspace_{result.report.id}",
            ):
                suggestion = suggest_refined_roof_polygons(
                    image,
                    measurement.sections,
                    address=result.report.address,
                    reference_images=reference_images,
                )
                if suggestion.warnings:
                    for warning in suggestion.warnings:
                        st.warning(warning)
                if not suggestion.polygons:
                    st.warning("AI did not return usable cleaned roof outlines.")
                    return
                try:
                    corrected_sections = _sections_from_ai_polygons(measurement.sections, suggestion.polygons)
                    corrected_report = recalculate_report_from_corrected_sections(
                        result.report,
                        corrected_sections,
                        correction_note=(
                            "AI refined the current roof outline from existing section polygons. "
                            + (suggestion.notes or "")
                        ).strip(),
                    )
                except Exception as exc:
                    st.error(f"Could not apply AI outline cleanup: {type(exc).__name__}: {exc}")
                    return
                st.session_state["roof_measure_result"] = RoofMeasureResult(
                    report=corrected_report,
                    selected_mask=None,
                    candidate_count=result.candidate_count,
                )
                st.rerun()
            if not openai_available:
                st.caption("Set OPENAI_API_KEY to enable AI cleanup.")
        with reset_col:
            if st.button("Reset to Original Measurement", width="stretch", key=f"roof_measure_workspace_reset_{result.report.id}"):
                original = st.session_state.get("roof_measure_original_result")
                if original is not None:
                    st.session_state["roof_measure_result"] = original
                    st.rerun()
        section_options = [section.section_id for section in measurement.sections]
        selected_section_id = st.selectbox(
            "Roof section",
            section_options,
            index=0,
            key=f"roof_measure_corner_section_{result.report.id}",
        )
        selected_section = next((section for section in measurement.sections if section.section_id == selected_section_id), measurement.sections[0])
        canvas_width, canvas_height, scale_x, scale_y = _canvas_dimensions(image)
        corner_action = st.radio(
            "Corner action",
            ["Move/delete corners", "Add corner"],
            horizontal=True,
            key=f"roof_measure_corner_action_{result.report.id}_{selected_section_id}",
        )
        drawing_mode = "point" if corner_action == "Add corner" else "transform"
        corner_canvas = st_canvas(
            fill_color="rgba(229, 40, 40, 0.85)",
            stroke_width=2,
            stroke_color="#e52828",
            background_image=_canvas_background_image(image, canvas_width, canvas_height),
            update_streamlit=True,
            height=canvas_height,
            width=canvas_width,
            drawing_mode=drawing_mode,
            initial_drawing=_section_to_corner_canvas_initial_drawing(selected_section, scale_x=scale_x, scale_y=scale_y),
            display_toolbar=True,
            point_display_radius=8,
            key=f"roof_measure_corner_canvas_{result.report.id}_{selected_section_id}",
        )
        if st.button("Apply Corner Edits", type="primary", width="stretch", key=f"roof_measure_apply_corners_{result.report.id}_{selected_section_id}"):
            try:
                if corner_action == "Add corner":
                    existing_points, new_points = _canvas_json_to_corner_edit_points(
                        getattr(corner_canvas, "json_data", None),
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    if not new_points:
                        raise ValueError("Click the roof edge where the new corner belongs before applying.")
                    corner_points = _insert_new_corner_points(existing_points, new_points)
                else:
                    corner_points = _canvas_json_to_corner_points(
                        getattr(corner_canvas, "json_data", None),
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                corrected_sections = _replace_section_polygon(
                    measurement.sections,
                    selected_section_id,
                    corner_points,
                )
                corrected_report = recalculate_report_from_corrected_sections(
                    result.report,
                    corrected_sections,
                    correction_note=f"Estimator adjusted corner handles for {selected_section_id}.",
                )
            except Exception as exc:
                st.error(f"Could not apply corner edits: {type(exc).__name__}: {exc}")
                return
            st.session_state["roof_measure_result"] = RoofMeasureResult(
                report=corrected_report,
                selected_mask=None,
                candidate_count=result.candidate_count,
            )
            st.rerun()


def _render_ai_outline_cleanup(
    result: RoofMeasureResult,
    *,
    image: Image.Image,
    reference_images: list[Image.Image] | None = None,
) -> None:
    measurement = result.report.measurement
    if not measurement.sections:
        return
    openai_available = bool(os.getenv("OPENAI_API_KEY"))
    with st.expander("AI Outline Cleanup", expanded=False):
        st.caption(
            "Uses the current outline as a starting point and asks AI to simplify or move vertices to visible roof edges. "
            "This is useful when SAM is close but jagged."
        )
        if st.button(
            "Clean Current Outline with AI",
            width="stretch",
            disabled=not openai_available,
            key=f"roof_measure_ai_cleanup_{result.report.id}",
        ):
            suggestion = suggest_refined_roof_polygons(
                image,
                measurement.sections,
                address=result.report.address,
                reference_images=reference_images,
            )
            if suggestion.warnings:
                for warning in suggestion.warnings:
                    st.warning(warning)
            if not suggestion.polygons:
                st.warning("AI did not return usable cleaned roof outlines.")
                return
            try:
                corrected_sections = _sections_from_ai_polygons(measurement.sections, suggestion.polygons)
                corrected_report = recalculate_report_from_corrected_sections(
                    result.report,
                    corrected_sections,
                    correction_note=(
                        "AI refined the current roof outline from existing section polygons. "
                        + (suggestion.notes or "")
                    ).strip(),
                )
            except Exception as exc:
                st.error(f"Could not apply AI outline cleanup: {type(exc).__name__}: {exc}")
                return
            st.session_state["roof_measure_result"] = RoofMeasureResult(
                report=corrected_report,
                selected_mask=None,
                candidate_count=result.candidate_count,
            )
            st.rerun()
        if not openai_available:
            st.caption("Set OPENAI_API_KEY to enable outline cleanup.")


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


def _canvas_background_image(image: Image.Image, canvas_width: int, canvas_height: int) -> Image.Image:
    if image.size == (canvas_width, canvas_height):
        return image
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    return image.resize((canvas_width, canvas_height), resampling)


def _section_to_corner_canvas_initial_drawing(section: RoofSection, *, scale_x: float, scale_y: float) -> dict:
    points = section.polygon[:-1] if section.polygon and section.polygon[0] == section.polygon[-1] else section.polygon
    canvas_points = [
        {"x": float(x) * scale_x, "y": float(y) * scale_y}
        for x, y in points
    ]
    objects: list[dict] = []
    for index, start in enumerate(canvas_points):
        end = canvas_points[(index + 1) % len(canvas_points)] if canvas_points else None
        if end is None:
            continue
        objects.append(
            {
                "type": "line",
                "version": "4.4.0",
                "originX": "left",
                "originY": "top",
                "left": start["x"],
                "top": start["y"],
                "x1": 0,
                "y1": 0,
                "x2": end["x"] - start["x"],
                "y2": end["y"] - start["y"],
                "fill": "#009760",
                "stroke": "#009760",
                "strokeWidth": 3,
                "strokeLineCap": "round",
                "strokeLineJoin": "round",
                "objectCaching": False,
                "selectable": False,
                "evented": False,
            }
        )
    for index, (x, y) in enumerate(points):
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
                "width": 18,
                "height": 18,
                "radius": 9,
                "fill": "#f4f1ea",
                "stroke": "#e52828",
                "strokeWidth": 3,
                "scaleX": 1,
                "scaleY": 1,
                "sectionId": section.section_id,
                "vertexIndex": index,
            }
        )
    return {"version": "4.4.0", "objects": objects}


def _sections_from_ai_polygons(
    original_sections: list[RoofSection],
    polygons: list[list[tuple[float, float]]],
) -> list[RoofSection]:
    corrected_sections: list[RoofSection] = []
    for index, polygon in enumerate(polygons, start=1):
        if len(polygon) < 3:
            continue
        if index <= len(original_sections):
            template = original_sections[index - 1]
            corrected_sections.append(
                template.model_copy(
                    deep=True,
                    update={
                        "polygon": polygon,
                        "holes": [],
                        "confidence": max(float(template.confidence or 0), 0.65),
                    },
                )
            )
        else:
            section = section_from_polygon(f"section-{index}", polygon)
            section.confidence = 0.65
            corrected_sections.append(section)
    if not corrected_sections:
        raise ValueError("AI cleanup did not produce any usable roof polygons.")
    return corrected_sections


def _sections_to_canvas_initial_drawing(sections: list[RoofSection], *, scale_x: float, scale_y: float) -> dict:
    objects = []
    for section in sections:
        points = section.polygon[:-1] if section.polygon and section.polygon[0] == section.polygon[-1] else section.polygon
        if len(points) < 3:
            continue
        canvas_points = [
            {"x": float(x) * scale_x, "y": float(y) * scale_y}
            for x, y in points
        ]
        objects.append(
            {
                "type": "polygon",
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
                "points": canvas_points,
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


def _canvas_json_to_corner_points(canvas_json: dict | None, *, scale_x: float, scale_y: float) -> list[tuple[float, float]]:
    existing_points, new_points = _canvas_json_to_corner_edit_points(
        canvas_json,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    if new_points:
        return _insert_new_corner_points(existing_points, new_points)
    return existing_points


def _canvas_json_to_corner_edit_points(
    canvas_json: dict | None,
    *,
    scale_x: float,
    scale_y: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if not isinstance(canvas_json, dict):
        return [], []
    indexed_existing: list[tuple[int, int, tuple[float, float]]] = []
    new_points: list[tuple[float, float]] = []
    for fallback_index, obj in enumerate(canvas_json.get("objects") or []):
        if not isinstance(obj, dict) or str(obj.get("type") or "").lower() != "circle":
            continue
        point = _canvas_object_center(obj)
        if point is None:
            continue
        x, y = point
        scaled_point = (float(x) / scale_x if scale_x else float(x), float(y) / scale_y if scale_y else float(y))
        if "vertexIndex" in obj:
            try:
                vertex_index = int(obj.get("vertexIndex"))
            except (TypeError, ValueError):
                vertex_index = fallback_index
            indexed_existing.append((vertex_index, fallback_index, scaled_point))
        else:
            new_points.append(scaled_point)
    indexed_existing.sort(key=lambda item: (item[0], item[1]))
    return [point for _, _, point in indexed_existing], new_points


def _insert_new_corner_points(
    existing_points: list[tuple[float, float]],
    new_points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if len(existing_points) < 3:
        raise ValueError("At least three existing corner points are required.")
    updated = list(existing_points)
    for point in new_points:
        insert_at = _nearest_edge_insert_index(updated, point)
        updated.insert(insert_at, point)
    return updated


def _nearest_edge_insert_index(points: list[tuple[float, float]], point: tuple[float, float]) -> int:
    best_index = 1
    best_distance = float("inf")
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        distance = _distance_to_segment(point, start, end)
        if distance < best_distance:
            best_distance = distance
            best_index = index + 1
    return best_index


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projected_x = x1 + t * dx
    projected_y = y1 + t * dy
    return math.hypot(px - projected_x, py - projected_y)


def _replace_section_polygon(
    sections: list[RoofSection],
    section_id: str,
    points: list[tuple[float, float]],
) -> list[RoofSection]:
    if len(points) < 3:
        raise ValueError("At least three corner points are required.")
    corrected_sections: list[RoofSection] = []
    matched = False
    for section in sections:
        if section.section_id != section_id:
            corrected_sections.append(section)
            continue
        matched = True
        corrected_sections.append(
            section.model_copy(
                deep=True,
                update={
                    "polygon": points,
                    "holes": [],
                },
            )
        )
    if not matched:
        raise ValueError(f"Roof section was not found: {section_id}")
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
    left = float(obj.get("left") or 0)
    top = float(obj.get("top") or 0)
    scale_x = float(obj.get("scaleX") or 1)
    scale_y = float(obj.get("scaleY") or 1)
    points: list[tuple[float, float]] = []
    for command in path:
        if not isinstance(command, list) or not command:
            continue
        op = str(command[0]).upper()
        if op in {"M", "L"} and len(command) >= 3:
            try:
                points.append((left + float(command[1]) * scale_x, top + float(command[2]) * scale_y))
            except (TypeError, ValueError):
                continue
        elif op in {"Q", "C"} and len(command) >= 3:
            try:
                points.append((left + float(command[-2]) * scale_x, top + float(command[-1]) * scale_y))
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
