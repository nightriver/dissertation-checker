"""Компонент вкладки «Звіт» у вигляді Plag — PLAN_PLAG_VIEW.md, §7 етап 3.

Двобічний компонент `st.components.v2`, як `plag_filter/viewer/`: у нього
йдуть дані `report_payload`, назад приходить подія для `apply_viewer_event`.
Розмітка, стилі та код лежать поруч у файлах `report_viewer.html`,
`report_viewer.css`, `report_viewer.js` — жодних завантажень із мережі.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_ASSETS = Path(__file__).resolve().parent

# Подія компонента приходить під цим іменем: `setTriggerValue("event", …)`.
_EVENT_NAME = "event"

_HTML = (_ASSETS / "report_viewer.html").read_text(encoding="utf-8")
_CSS = (_ASSETS / "report_viewer.css").read_text(encoding="utf-8")
_JS = (_ASSETS / "report_viewer.js").read_text(encoding="utf-8")


def _report_component():
    """Зареєструвати компонент у менеджері поточного середовища виконання.

    Як і в `viewer/__init__.py`: реєстр живе в `Runtime`, тому реєстрація
    повторюється на кожному проході екрана.
    """
    return st.components.v2.component("plag_report_viewer", html=_HTML, css=_CSS, js=_JS)


def render_report_viewer(payload: dict, *, key: str) -> dict | None:
    """Показати аркуш у вигляді Plag і повернути подію компонента, якщо вона була.

    Події: `{"type": "decision", …}`, `{"type": "page", …}`,
    `{"type": "show_excluded", …}`; коли експерт нічого не робив — `None`.
    """
    result = _report_component()(
        data=payload,
        key=key,
        height="content",
        on_event_change=lambda: None,
    )
    event = result.get(_EVENT_NAME)
    return event if isinstance(event, dict) else None
