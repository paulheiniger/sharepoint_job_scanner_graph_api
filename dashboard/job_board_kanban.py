from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import streamlit as st


KANBAN_HTML = """
<div class="job-kanban-root" aria-label="Interactive job board"></div>
"""


KANBAN_CSS = """
:host {
  color: var(--st-text-color);
  font-family: var(--st-font);
}

.job-kanban-root {
  width: 100%;
  overflow: hidden;
}

.job-kanban-board {
  display: flex;
  gap: 0.85rem;
  overflow-x: auto;
  padding: 0.2rem 0.15rem 0.85rem;
  scroll-snap-type: x proximity;
}

.job-kanban-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.38rem;
  padding: 0.1rem 0.1rem 0.55rem;
}

.job-kanban-nav-button {
  background: var(--st-secondary-background-color);
  border: 1px solid color-mix(in srgb, var(--st-text-color) 14%, transparent);
  border-radius: 999px;
  color: var(--st-text-color);
  cursor: pointer;
  font: inherit;
  font-size: 0.72rem;
  padding: 0.3rem 0.55rem;
}

.job-kanban-nav-button:hover,
.job-kanban-nav-button:focus-visible {
  border-color: var(--st-primary-color);
  outline: none;
}

.job-kanban-lane {
  background: color-mix(in srgb, var(--st-secondary-background-color) 92%, transparent);
  border: 1px solid color-mix(in srgb, var(--st-text-color) 14%, transparent);
  border-radius: 0.8rem;
  display: flex;
  flex: 1 0 225px;
  flex-direction: column;
  max-width: 275px;
  max-height: 650px;
  min-height: 260px;
  scroll-snap-align: start;
}

.job-kanban-lane.is-over {
  border-color: var(--st-primary-color);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--st-primary-color) 24%, transparent);
}

.job-kanban-lane-header {
  border-bottom: 1px solid color-mix(in srgb, var(--st-text-color) 12%, transparent);
  padding: 0.75rem 0.8rem 0.65rem;
}

.job-kanban-lane-title-row {
  align-items: center;
  display: flex;
  gap: 0.5rem;
  justify-content: space-between;
}

.job-kanban-lane-title {
  font-size: 0.92rem;
  font-weight: 700;
  line-height: 1.15;
}

.job-kanban-count {
  align-items: center;
  background: color-mix(in srgb, var(--st-text-color) 10%, transparent);
  border-radius: 999px;
  display: inline-flex;
  font-size: 0.75rem;
  font-weight: 700;
  justify-content: center;
  min-width: 1.7rem;
  padding: 0.18rem 0.45rem;
}

.job-kanban-lane-total {
  color: color-mix(in srgb, var(--st-text-color) 68%, transparent);
  font-size: 0.76rem;
  margin-top: 0.3rem;
}

.job-kanban-lane-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.58rem;
  min-height: 170px;
  overflow-y: auto;
  padding: 0.65rem;
}

.job-kanban-empty {
  border: 1px dashed color-mix(in srgb, var(--st-text-color) 20%, transparent);
  border-radius: 0.55rem;
  color: color-mix(in srgb, var(--st-text-color) 50%, transparent);
  font-size: 0.76rem;
  padding: 0.8rem 0.5rem;
  text-align: center;
}

.job-kanban-card {
  background: var(--st-background-color);
  border: 1px solid color-mix(in srgb, var(--st-text-color) 14%, transparent);
  border-left: 4px solid color-mix(in srgb, var(--st-primary-color) 65%, transparent);
  border-radius: 0.62rem;
  box-shadow: 0 1px 3px color-mix(in srgb, black 10%, transparent);
  cursor: grab;
  padding: 0.68rem 0.72rem;
  user-select: none;
}

.job-kanban-card:hover,
.job-kanban-card:focus-visible {
  border-color: color-mix(in srgb, var(--st-primary-color) 65%, transparent);
  box-shadow: 0 3px 10px color-mix(in srgb, black 16%, transparent);
  outline: none;
  transform: translateY(-1px);
}

.job-kanban-card.is-selected {
  border-color: var(--st-primary-color);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--st-primary-color) 24%, transparent);
}

.job-kanban-card.is-dragging {
  cursor: grabbing;
  opacity: 0.55;
}

.job-kanban-card-title {
  font-size: 0.88rem;
  font-weight: 700;
  line-height: 1.25;
}

.job-kanban-card-customer,
.job-kanban-card-meta {
  color: color-mix(in srgb, var(--st-text-color) 68%, transparent);
  font-size: 0.75rem;
  line-height: 1.25;
  margin-top: 0.28rem;
}

.job-kanban-card-value {
  font-size: 0.88rem;
  font-weight: 700;
  margin-top: 0.5rem;
}

.job-kanban-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.5rem;
}

.job-kanban-badge {
  background: color-mix(in srgb, var(--st-text-color) 9%, transparent);
  border-radius: 999px;
  font-size: 0.66rem;
  line-height: 1;
  padding: 0.27rem 0.42rem;
}

.job-kanban-badge.is-warning {
  background: color-mix(in srgb, #d97706 18%, transparent);
  color: color-mix(in srgb, #d97706 82%, var(--st-text-color));
}

.job-kanban-badge.is-urgent {
  background: color-mix(in srgb, #dc2626 16%, transparent);
  color: color-mix(in srgb, #dc2626 82%, var(--st-text-color));
}
"""


KANBAN_JS = """
export default function(component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector('.job-kanban-root');
  if (!root) return;
  root.replaceChildren();

  const nav = document.createElement('nav');
  nav.className = 'job-kanban-nav';
  nav.setAttribute('aria-label', 'Sales stage lanes');
  root.appendChild(nav);

  const board = document.createElement('div');
  board.className = 'job-kanban-board';
  root.appendChild(board);

  const lanes = Array.isArray(data?.lanes) ? data.lanes : [];
  const selectedJobId = String(data?.selected_job_id || '');
  let draggedCard = null;
  let draggedJobId = '';
  let sourceStatus = '';
  let suppressClick = false;

  const emit = (type, payload) => {
    const eventId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    setTriggerValue('event', { event_id: eventId, type, ...payload });
  };

  const addText = (parent, className, value) => {
    if (value === null || value === undefined || String(value).trim() === '') return null;
    const element = document.createElement('div');
    element.className = className;
    element.textContent = String(value);
    parent.appendChild(element);
    return element;
  };

  const money = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 0
    }).format(number);
  };

  const laneElements = new Map();

  for (const lane of lanes) {
    const status = String(lane.status || 'Other');
    const laneElement = document.createElement('section');
    laneElement.className = 'job-kanban-lane';
    laneElement.dataset.status = status;

    const header = document.createElement('div');
    header.className = 'job-kanban-lane-header';
    const titleRow = document.createElement('div');
    titleRow.className = 'job-kanban-lane-title-row';
    addText(titleRow, 'job-kanban-lane-title', lane.label || status);
    addText(titleRow, 'job-kanban-count', Number(lane.count || 0));
    header.appendChild(titleRow);
    addText(header, 'job-kanban-lane-total', `${money(lane.total_value || 0)} total`);
    laneElement.appendChild(header);

    const body = document.createElement('div');
    body.className = 'job-kanban-lane-body';
    body.dataset.status = status;
    laneElement.appendChild(body);
    laneElements.set(status, { laneElement, body });

    const navButton = document.createElement('button');
    navButton.className = 'job-kanban-nav-button';
    navButton.type = 'button';
    navButton.textContent = `${lane.label || status} (${Number(lane.count || 0)})`;
    navButton.addEventListener('click', () => {
      laneElement.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    });
    nav.appendChild(navButton);

    body.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
      laneElement.classList.add('is-over');
    });
    body.addEventListener('dragleave', (event) => {
      if (!laneElement.contains(event.relatedTarget)) laneElement.classList.remove('is-over');
    });
    body.addEventListener('drop', (event) => {
      event.preventDefault();
      laneElement.classList.remove('is-over');
      if (!draggedCard || !draggedJobId || status === sourceStatus) return;
      const empty = body.querySelector('.job-kanban-empty');
      if (empty) empty.remove();
      body.appendChild(draggedCard);
      emit('move', { job_id: draggedJobId, from_status: sourceStatus, to_status: status });
    });

    const cards = Array.isArray(lane.cards) ? lane.cards : [];
    if (!cards.length) addText(body, 'job-kanban-empty', 'Drop a job here');

    for (const card of cards) {
      const jobId = String(card.job_id || '');
      if (!jobId) continue;
      const cardElement = document.createElement('article');
      cardElement.className = 'job-kanban-card';
      if (jobId === selectedJobId) cardElement.classList.add('is-selected');
      cardElement.draggable = true;
      cardElement.tabIndex = 0;
      cardElement.dataset.jobId = jobId;
      cardElement.setAttribute('role', 'button');
      cardElement.setAttribute('aria-label', `Open details for ${card.title || card.customer || jobId}`);

      addText(cardElement, 'job-kanban-card-title', card.title || card.customer || jobId);
      if (card.customer && card.customer !== card.title) {
        addText(cardElement, 'job-kanban-card-customer', card.customer);
      }
      addText(cardElement, 'job-kanban-card-meta', card.owner ? `Owner: ${card.owner}` : 'Owner: Unassigned');
      addText(cardElement, 'job-kanban-card-value', money(card.value));

      const badges = document.createElement('div');
      badges.className = 'job-kanban-badges';
      const badgeValues = [card.division, card.priority, card.freshness];
      for (const value of badgeValues) {
        if (!value) continue;
        const badge = document.createElement('span');
        badge.className = 'job-kanban-badge';
        if (String(value).toLowerCase() === 'urgent') badge.classList.add('is-urgent');
        badge.textContent = String(value);
        badges.appendChild(badge);
      }
      if (Number(card.warning_count || 0) > 0) {
        const warning = document.createElement('span');
        warning.className = 'job-kanban-badge is-warning';
        warning.textContent = `${Number(card.warning_count)} warning${Number(card.warning_count) === 1 ? '' : 's'}`;
        badges.appendChild(warning);
      }
      if (badges.childElementCount) cardElement.appendChild(badges);

      cardElement.addEventListener('dragstart', (event) => {
        suppressClick = true;
        draggedCard = cardElement;
        draggedJobId = jobId;
        sourceStatus = status;
        cardElement.classList.add('is-dragging');
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', jobId);
        }
      });
      cardElement.addEventListener('dragend', () => {
        cardElement.classList.remove('is-dragging');
        for (const entry of laneElements.values()) entry.laneElement.classList.remove('is-over');
        draggedCard = null;
        draggedJobId = '';
        sourceStatus = '';
        globalThis.setTimeout(() => { suppressClick = false; }, 0);
      });
      cardElement.addEventListener('click', () => {
        if (!suppressClick) emit('select', { job_id: jobId, status });
      });
      cardElement.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          emit('select', { job_id: jobId, status });
        }
      });
      body.appendChild(cardElement);
    }

    board.appendChild(laneElement);
  }
}
"""


job_board_kanban_component = st.components.v2.component(
    "spraytec_job_board_kanban",
    html=KANBAN_HTML,
    css=KANBAN_CSS,
    js=KANBAN_JS,
)


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN without importing pandas.
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_job_board_kanban_payload(
    rows: Iterable[Mapping[str, Any]],
    statuses: Iterable[str],
    *,
    selected_job_id: object = None,
    group_field: str = "board_status",
) -> dict[str, Any]:
    ordered_statuses: list[str] = []
    for value in statuses:
        status = _text(value)
        if status and status not in ordered_statuses:
            ordered_statuses.append(status)

    cards_by_status: dict[str, list[dict[str, Any]]] = {status: [] for status in ordered_statuses}
    seen_job_ids: set[str] = set()
    for row in rows:
        job_id = _text(row.get("job_id"))
        if not job_id or job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)
        status = _text(row.get(group_field)) or "Other"
        if status not in cards_by_status:
            ordered_statuses.append(status)
            cards_by_status[status] = []
        value = _number(row.get("sales_value"))
        if value is None:
            value = _number(row.get("estimated_value"))
        warning_count = _number(row.get("warning_count")) or 0
        title = _text(row.get("project")) or _text(row.get("job_name")) or _text(row.get("customer_display"))
        customer = _text(row.get("customer_display")) or _text(row.get("customer"))
        owner = (
            _text(row.get("deal_owner"))
            or _text(row.get("estimator_display"))
            or _text(row.get("assigned_user"))
        )
        cards_by_status[status].append(
            {
                "job_id": job_id,
                "title": title or customer or job_id,
                "customer": customer,
                "value": value,
                "owner": owner,
                "division": _text(row.get("division")),
                "priority": _text(row.get("priority")),
                "freshness": _text(row.get("opportunity_freshness")),
                "warning_count": int(max(0, warning_count)),
            }
        )

    lanes: list[dict[str, Any]] = []
    for status in ordered_statuses:
        cards = cards_by_status[status]
        cards.sort(key=lambda card: (-(card.get("value") or 0), card.get("title") or ""))
        lanes.append(
            {
                "status": status,
                "label": status,
                "count": len(cards),
                "total_value": sum(card.get("value") or 0 for card in cards),
                "cards": cards,
            }
        )
    return {"lanes": lanes, "selected_job_id": _text(selected_job_id)}


def component_event(result: object) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, Mapping):
        event = result.get("event")
    else:
        event = getattr(result, "event", None)
    if not isinstance(event, Mapping):
        return None
    event_type = _text(event.get("type"))
    event_id = _text(event.get("event_id"))
    job_id = _text(event.get("job_id"))
    if event_type not in {"select", "move"} or not event_id or not job_id:
        return None
    return {str(key): value for key, value in event.items()}


def render_job_board_kanban(
    payload: Mapping[str, Any],
    *,
    key: str = "job_board_kanban",
    height: int = 720,
) -> object:
    return job_board_kanban_component(
        key=key,
        data=dict(payload),
        default={},
        height=height,
        on_event_change=lambda: None,
    )
