"""Ізольований екран звіту — PLAN_REPORT_FIRST_PAGE.md, §§8, 11."""

from dataclasses import replace
from datetime import date, datetime
from typing import Callable, MutableMapping, Any
from zoneinfo import ZoneInfo

import streamlit as st

from ui_helpers import file_sha256

from .docx_export import DOCX_MIME, build_report_docx
from .extractor import RULES_VERSION, ReportInputError, extract_report_metadata, validate_report_upload
from .presentation import AUTHOR_WARNING, build_report_page, render_report_html
from .types import AcademicPerson, ReportMetadata

PREFIX = "report_first_page_"
UPLOAD_KEY = PREFIX + "upload"
SOURCE_KEY = PREFIX + "source"
METADATA_KEY = PREFIX + "metadata"
ERROR_KEY = PREFIX + "error"
AUTHOR_TOAST_KEY = PREFIX + "author_toast_shown"
FIELD_PREFIX = PREFIX + "field_"
_FIELDS = (
    ("author", "ПІБ автора"), ("title", "Тема дисертації"), ("degree_phrase", "Фраза про ступінь після «на здобуття»"),
    ("supervisor_label", "Позначення керівника / консультанта"),
    ("defense_day", "День захисту"), ("defense_month", "Місяць захисту (родовий відмінок)"),
    ("defense_year", "Рік захисту"), ("council_code", "Код спеціалізованої ради"),
    ("institution_tail", "Установа захисту (хвіст після коду, разом із прийменником)"),
)
_PERSON_FIELDS = (
    ("before_name", "Кваліфікації перед ПІБ"), ("full_name", "Повний ПІБ"),
    ("after_name", "Установа, посада та інші відомості після ПІБ"),
)


def kyiv_date(*, clock: Callable[[ZoneInfo], datetime] | None = None) -> date:
    """Єдине явне читання годинника; тест може передати власні години."""
    timezone = ZoneInfo("Europe/Kyiv")
    moment = clock(timezone) if clock else datetime.now(timezone)
    return moment.astimezone(timezone).date()


def reset_source(state: MutableMapping[str, Any], source: str | None) -> None:
    if state.get(SOURCE_KEY) == source and source is not None:
        return
    for key in list(state):
        if key.startswith(PREFIX) and key != UPLOAD_KEY:
            del state[key]
    if source is not None:
        state[SOURCE_KEY] = source


def load_metadata(data: bytes, state: MutableMapping[str, Any]) -> ReportMetadata:
    """Кешує лише витягування для поточного SHA-256 і версії правил у сесії."""
    reset_source(state, file_sha256(data) + ":" + RULES_VERSION)
    if ERROR_KEY in state:
        raise ReportInputError(state[ERROR_KEY])
    if METADATA_KEY not in state:
        try:
            state[METADATA_KEY] = extract_report_metadata(data)
        except ReportInputError as exc:
            state[ERROR_KEY] = str(exc)
            raise
    return state[METADATA_KEY]


def editable_fields(metadata: ReportMetadata) -> list[tuple[str, str, str | None]]:
    fields = [(name, label, getattr(metadata, name)) for name, label in _FIELDS]
    people = [("supervisor", "Керівник / консультант", metadata.supervisor)]
    people.extend((f"opponents_{i}", f"Опонент {i + 1}", person)
                  for i, person in enumerate(metadata.opponents or (AcademicPerson(),)))
    for prefix, label, person in people:
        fields.extend((prefix + "_" + name, label + ": " + caption, getattr(person, name))
                      for name, caption in _PERSON_FIELDS)
    return fields


def apply_edits(metadata: ReportMetadata, state: MutableMapping[str, Any]) -> ReportMetadata:
    def value(name: str) -> str | None:
        return str(state[FIELD_PREFIX + name]).strip() or None

    def person(prefix: str) -> AcademicPerson:
        return AcademicPerson(**{name: value(prefix + "_" + name) for name, _ in _PERSON_FIELDS})

    return replace(metadata, **{name: value(name) for name, _ in _FIELDS},
                   supervisor=person("supervisor"),
                   opponents=tuple(person(f"opponents_{i}")
                                   for i in range(len(metadata.opponents) or 1)))


def render_report_first_page() -> None:
    st.title("Звіт")
    uploaded = st.file_uploader(
        "Автореферат (PDF із текстовим шаром)", type=["pdf"],
        accept_multiple_files=False, key=UPLOAD_KEY, help="Один PDF до 30 МБ. OCR не виконується.",
    )
    if uploaded is None:
        reset_source(st.session_state, None)
        return
    data = uploaded.getvalue()
    error = validate_report_upload(uploaded.name, len(data))
    if error:
        reset_source(st.session_state, None)
        st.error(error)
        return
    try:
        metadata = load_metadata(data, st.session_state)
    except ReportInputError as exc:
        st.error(str(exc))
        return
    fields = editable_fields(metadata)
    for name, _, initial in fields:
        key = FIELD_PREFIX + name
        if key not in st.session_state:
            st.session_state[key] = initial or ""
    effective = apply_edits(metadata, st.session_state)
    page = build_report_page(effective, generated_on=kyiv_date())
    if not st.session_state.get(AUTHOR_TOAST_KEY):
        st.toast(AUTHOR_WARNING, icon="ℹ️", duration=5)
        st.session_state[AUTHOR_TOAST_KEY] = True
    preview_column, actions_column = st.columns([3, 1.4], gap="large")
    with preview_column:
        st.markdown(render_report_html(page), unsafe_allow_html=True)
    with actions_column:
        try:
            docx = build_report_docx(page)
        except Exception as exc:
            st.error(f"Не вдалося сформувати Word: {exc}")
        else:
            st.download_button(
                "Завантажити", data=docx, type="primary", use_container_width=True,
                file_name="zvit.docx", mime=DOCX_MIME, key=PREFIX + "download",
                help="Завантажити сторінку звіту у форматі Word (.docx).",
            )
        with st.expander("Уточнити дані", expanded=False):
            for warning in metadata.warnings:
                st.warning(warning)
            for name, label, _ in fields:
                st.text_input(label, key=FIELD_PREFIX + name)
