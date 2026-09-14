"""Компонент перегляду аркуша звіту Plag — PLAN_PLAG_FILTER_V2.md, §8.5, §9.2 етап 8.

Двобічний компонент `st.components.v2`: у нього йдуть дані `viewer_payload`,
назад приходить подія для `apply_viewer_event`. Розмітка, стилі та код лежать
поруч у файлах `viewer.html`, `viewer.css`, `viewer.js` — жодних завантажень
із мережі.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_ASSETS = Path(__file__).resolve().parent

# Подія компонента приходить під цим іменем: `setTriggerValue("event", …)`.
_EVENT_NAME = "event"

_HTML = (_ASSETS / "viewer.html").read_text(encoding="utf-8")
_CSS = (_ASSETS / "viewer.css").read_text(encoding="utf-8")
_JS = (_ASSETS / "viewer.js").read_text(encoding="utf-8")


def _viewer_component():
    """Зареєструвати компонент у менеджері поточного середовища виконання.

    Реєстр живе в `Runtime`, а не в модулі, тому реєстрація повторюється на
    кожному проході екрана; однакове визначення переписується мовчки.
    """
    return st.components.v2.component("plag_viewer", html=_HTML, css=_CSS, js=_JS)


def render_viewer(payload: dict, *, key: str) -> dict | None:
    """Показати аркуш і повернути подію компонента, якщо вона була — §8.5.

    Подія — словник виду `{"type": "decision", …}` або `{"type": "page", …}`;
    коли експерт нічого не робив, повертається `None`.
    """
    result = _viewer_component()(
        data=payload,
        key=key,
        height="content",
        on_event_change=lambda: None,
    )
    event = result.get(_EVENT_NAME)
    return event if isinstance(event, dict) else None
