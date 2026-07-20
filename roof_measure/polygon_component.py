from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from PIL import Image

from .geometry import repair_polygon
from .models import RoofSection

try:
    import streamlit.components.v2 as components_v2
except ImportError:  # pragma: no cover - current app pins Streamlit 1.58.
    components_v2 = None


_HTML = """
<div class="roof-polygon-editor">
  <div class="toolbar" role="toolbar" aria-label="Polygon editing tools">
    <button type="button" data-mode="move" class="active" title="Move vertices">Move</button>
    <button type="button" data-mode="add" title="Add a vertex to the nearest edge">Add</button>
    <button type="button" data-mode="delete" title="Delete a vertex">Delete</button>
    <button type="button" data-action="undo" title="Undo last edit">Undo</button>
  </div>
  <div class="canvas-wrap">
    <svg class="editor-svg" aria-label="Editable roof polygon">
      <image class="background" preserveAspectRatio="none"></image>
      <g class="geometry"></g>
    </svg>
  </div>
  <div class="status" aria-live="polite"></div>
</div>
"""


_CSS = """
.roof-polygon-editor { width: 100%; color: var(--st-text-color); font-family: var(--st-font); }
.toolbar { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; }
.toolbar button { border: 1px solid rgba(49, 51, 63, .28); border-radius: 4px; background: var(--st-secondary-background-color); color: var(--st-text-color); padding: 6px 12px; cursor: pointer; }
.toolbar button.active { background: var(--st-primary-color); color: white; border-color: var(--st-primary-color); }
.canvas-wrap { width: 100%; overflow: hidden; border: 1px solid rgba(49, 51, 63, .18); border-radius: 4px; background: #111; }
.editor-svg { display: block; width: 100%; height: auto; touch-action: none; user-select: none; }
.roof-ring { fill: rgba(0, 151, 96, .12); stroke: #00d99b; stroke-width: 3; vector-effect: non-scaling-stroke; pointer-events: none; }
.hole-ring { fill: rgba(255, 193, 7, .08); stroke: #ffc107; stroke-width: 3; vector-effect: non-scaling-stroke; pointer-events: none; }
.vertex { fill: #00e599; stroke: #071b15; stroke-width: 2; vector-effect: non-scaling-stroke; cursor: grab; }
.vertex.locked { fill: #2196f3; }
.vertex:hover { r: 7; }
.mode-add .editor-svg { cursor: crosshair; }
.mode-delete .vertex { cursor: pointer; fill: #e52828; }
.status { min-height: 20px; margin-top: 6px; color: rgba(49, 51, 63, .72); font-size: 13px; }
"""


_JS = """
export default function(component) {
  const data = component.data || {};
  const root = component.parentElement.querySelector('.roof-polygon-editor');
  const svg = root.querySelector('.editor-svg');
  const geometry = root.querySelector('.geometry');
  const background = root.querySelector('.background');
  const status = root.querySelector('.status');
  const width = Number(data.width || 1);
  const height = Number(data.height || 1);
  const revision = String(data.revision || '0');

  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  background.setAttribute('href', data.image_url || '');
  background.setAttribute('width', width);
  background.setAttribute('height', height);

  if (!root._editorState || root._editorRevision !== revision) {
    root._editorState = JSON.parse(JSON.stringify(data.sections || []));
    root._editorRevision = revision;
    root._history = [];
    root._mode = 'move';
  }

  const state = root._editorState;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const openRing = (vertices) => {
    if (!vertices || vertices.length < 2) return vertices || [];
    const first = vertices[0];
    const last = vertices[vertices.length - 1];
    return first.x === last.x && first.y === last.y ? vertices.slice(0, -1) : vertices;
  };
  const pointString = (vertices) => openRing(vertices).map((point) => point.x + ',' + point.y).join(' ');
  const svgPoint = (event) => {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const transformed = point.matrixTransform(svg.getScreenCTM().inverse());
    return {x: Math.max(0, Math.min(width, transformed.x)), y: Math.max(0, Math.min(height, transformed.y))};
  };
  const saveHistory = () => {
    root._history.push(clone(state));
    if (root._history.length > 50) root._history.shift();
  };
  const commit = (message) => {
    status.textContent = message;
    component.setStateValue('sections', clone(state));
  };
  const distanceToSegment = (point, start, end) => {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (!dx && !dy) return Math.hypot(point.x - start.x, point.y - start.y);
    const t = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
  };

  const render = () => {
    geometry.replaceChildren();
    root.classList.toggle('mode-add', root._mode === 'add');
    root.classList.toggle('mode-delete', root._mode === 'delete');
    root.querySelectorAll('[data-mode]').forEach((button) => button.classList.toggle('active', button.dataset.mode === root._mode));

    state.forEach((section, sectionIndex) => {
      const vertices = openRing(section.vertices || []);
      if (vertices.length >= 3) {
        const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        polygon.setAttribute('points', pointString(vertices));
        polygon.setAttribute('class', 'roof-ring');
        geometry.appendChild(polygon);
      }
      (section.holes || []).forEach((hole) => {
        const holeVertices = openRing(hole.vertices || []);
        if (holeVertices.length >= 3) {
          const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
          polygon.setAttribute('points', pointString(holeVertices));
          polygon.setAttribute('class', 'hole-ring');
          geometry.appendChild(polygon);
        }
      });
      vertices.forEach((vertex, vertexIndex) => {
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', vertex.x);
        circle.setAttribute('cy', vertex.y);
        circle.setAttribute('r', 5.5);
        circle.setAttribute('class', 'vertex' + (vertex.locked ? ' locked' : ''));
        circle.dataset.sectionIndex = String(sectionIndex);
        circle.dataset.vertexIndex = String(vertexIndex);
        circle.onpointerdown = (event) => {
          event.preventDefault();
          event.stopPropagation();
          if (root._mode === 'delete') {
            if (vertices.length <= 3) {
              status.textContent = 'A polygon must keep at least three vertices.';
              return;
            }
            saveHistory();
            section.vertices.splice(vertexIndex, 1);
            render();
            commit('Vertex deleted.');
            return;
          }
          if (root._mode !== 'move') return;
          saveHistory();
          root._drag = {sectionIndex: sectionIndex, vertexIndex: vertexIndex, pointerId: event.pointerId};
          svg.setPointerCapture(event.pointerId);
        };
        geometry.appendChild(circle);
      });
    });
  };

  svg.onpointermove = (event) => {
    if (!root._drag) return;
    const point = svgPoint(event);
    const vertex = state[root._drag.sectionIndex].vertices[root._drag.vertexIndex];
    vertex.x = point.x;
    vertex.y = point.y;
    vertex.locked = true;
    render();
  };
  const finishDrag = (event) => {
    if (!root._drag) return;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    root._drag = null;
    commit('Vertex moved and connected edges preserved.');
  };
  svg.onpointerup = finishDrag;
  svg.onpointercancel = finishDrag;

  svg.onpointerdown = (event) => {
    if (root._mode !== 'add' || event.target.classList.contains('vertex')) return;
    const point = svgPoint(event);
    let best = null;
    state.forEach((section, sectionIndex) => {
      const vertices = openRing(section.vertices || []);
      vertices.forEach((start, edgeIndex) => {
        const end = vertices[(edgeIndex + 1) % vertices.length];
        const distance = distanceToSegment(point, start, end);
        if (!best || distance < best.distance) best = {sectionIndex, edgeIndex, distance};
      });
    });
    if (!best) return;
    saveHistory();
    state[best.sectionIndex].vertices.splice(best.edgeIndex + 1, 0, {x: point.x, y: point.y, locked: true});
    render();
    commit('Vertex added to the nearest connected edge.');
  };

  root.querySelectorAll('[data-mode]').forEach((button) => {
    button.onclick = () => {
      root._mode = button.dataset.mode;
      status.textContent = root._mode === 'move' ? 'Drag a vertex; its connected edges move with it.' : root._mode === 'add' ? 'Click near an edge to insert a connected vertex.' : 'Click a vertex to delete it.';
      render();
    };
  });
  root.querySelector('[data-action="undo"]').onclick = () => {
    if (!root._history.length) {
      status.textContent = 'Nothing to undo.';
      return;
    }
    const previous = root._history.pop();
    state.splice(0, state.length, ...previous);
    render();
    commit('Last polygon edit undone.');
  };

  render();
  if (!status.textContent) status.textContent = 'Drag a vertex; its connected edges move with it.';
}
"""


_COMPONENT = None


def _component():
    global _COMPONENT
    if components_v2 is None:
        return None
    if _COMPONENT is None:
        _COMPONENT = components_v2.component(
            "roof_polygon_editor",
            html=_HTML,
            css=_CSS,
            js=_JS,
        )
    return _COMPONENT


def polygon_editor_available() -> bool:
    return components_v2 is not None


def image_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def sections_to_component_data(sections: list[RoofSection], *, locked_vertices: set[str] | None = None) -> list[dict[str, Any]]:
    locked_vertices = locked_vertices or set()
    payload: list[dict[str, Any]] = []
    for section in sections:
        exterior = _open_ring(section.polygon)
        payload.append(
            {
                "polygon_id": section.section_id,
                "vertices": [
                    {
                        "x": float(x),
                        "y": float(y),
                        "locked": f"{section.section_id}:{index}" in locked_vertices,
                    }
                    for index, (x, y) in enumerate(exterior)
                ],
                "holes": [
                    {
                        "hole_id": f"{section.section_id}:hole:{hole_index}",
                        "vertices": [
                            {
                                "x": float(x),
                                "y": float(y),
                                "locked": f"{section.section_id}:hole:{hole_index}:{index}" in locked_vertices,
                            }
                            for index, (x, y) in enumerate(_open_ring(hole))
                        ],
                    }
                    for hole_index, hole in enumerate(section.holes)
                ],
            }
        )
    return payload


def component_data_to_sections(payload: Any, templates: list[RoofSection]) -> tuple[list[RoofSection], list[dict[str, object]]]:
    if not isinstance(payload, list):
        return [], []
    templates_by_id = {section.section_id: section for section in templates}
    sections: list[RoofSection] = []
    locked_points: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        polygon_id = str(item.get("polygon_id") or "")
        template = templates_by_id.get(polygon_id)
        if template is None:
            continue
        vertices = _points(item.get("vertices"), polygon_id, locked_points)
        if len(vertices) < 3:
            continue
        holes = []
        for hole in item.get("holes") or []:
            if not isinstance(hole, dict):
                continue
            hole_id = str(hole.get("hole_id") or "")
            hole_points = _points(hole.get("vertices"), hole_id, locked_points)
            if len(hole_points) >= 3:
                holes.append(hole_points)
        sections.append(
            template.model_copy(
                deep=True,
                update={
                    "polygon": repair_polygon(vertices),
                    "holes": [repair_polygon(hole) for hole in holes],
                },
            )
        )
    return sections, locked_points


def render_polygon_editor(
    image: Image.Image,
    sections: list[RoofSection],
    *,
    locked_vertices: set[str],
    revision: int,
    key: str,
):
    component = _component()
    if component is None:
        return None
    payload = sections_to_component_data(sections, locked_vertices=locked_vertices)
    return component(
        data={
            "image_url": image_data_url(image),
            "width": image.width,
            "height": image.height,
            "sections": payload,
            "revision": revision,
        },
        default={"sections": payload},
        key=key,
        on_sections_change=lambda: None,
        width="stretch",
        height="content",
    )


def _open_ring(points):
    if len(points) > 1 and points[0] == points[-1]:
        return list(points[:-1])
    return list(points)


def _points(value: Any, polygon_id: str, locked_points: list[dict[str, object]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            point = (float(item["x"]), float(item["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        points.append(point)
        if bool(item.get("locked")):
            locked_points.append({"polygon_id": polygon_id, "x": point[0], "y": point[1]})
    return points
