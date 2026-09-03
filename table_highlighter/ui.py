"""Інтерфейс Streamlit для підсвічування готової таблиці DOCX."""

from __future__ import annotations

import streamlit as st

from table_highlighter.processor import DocumentValidationError, inspect_tables, process_document
from table_highlighter.types import HighlightOptions
from ui_helpers import file_sha256


_RESULT_KEY = "table_highlight_result"
_SOURCE_KEY = "table_highlight_source_sha256"
_OPTIONS_KEY = "table_highlight_result_options"


def _reset_if_new_file(data: bytes) -> None:
    fingerprint = file_sha256(data)
    if st.session_state.get(_SOURCE_KEY) == fingerprint:
        return
    st.session_state[_SOURCE_KEY] = fingerprint
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_OPTIONS_KEY, None)


def render_table_highlight_page() -> None:
    """Відображає ізольований сценарій обробки однієї таблиці DOCX."""
    st.title("🖍️ Підсвічування таблиці")
    st.caption(
        "Підсвічує збіги й відмінності у двоколонковій таблиці, зберігає "
        "пояснення та посилання й вирівнює маркери сторінок «С. …»."
    )
    uploaded = st.file_uploader(
        "Таблиця порівняння (DOCX)",
        type=["docx"],
        key="table_highlight_upload",
        help="Максимальний розмір файлу: 30 МБ.",
    )
    if not uploaded:
        return
    data = uploaded.getvalue()
    _reset_if_new_file(data)
    try:
        tables = inspect_tables(data)
    except DocumentValidationError as error:
        st.error(f"❌ {error}")
        return
    if not tables:
        st.error("❌ Документ не містить таблиць.")
        return

    labels = {
        table.index: f"Таблиця {table.index + 1}: {table.row_count} рядків, "
        f"двоколонкових — {table.two_column_rows}"
        for table in tables
    }
    table_index = st.selectbox("Таблиця для обробки", options=list(labels), format_func=labels.get, key="table_highlight_table")
    selected = tables[table_index]
    first_row, last_row = st.columns(2)
    with first_row:
        start = st.number_input(
            "Перший рядок", min_value=1, max_value=max(1, selected.row_count), value=1, step=1,
            key="table_highlight_first_row",
        )
    with last_row:
        end = st.number_input(
            "Останній рядок", min_value=1, max_value=max(1, selected.row_count),
            value=max(1, selected.row_count), step=1, key="table_highlight_last_row",
        )
    if start > end:
        st.error("Перший рядок не може бути більшим за останній.")
        return

    with st.expander("Налаштування", expanded=False):
        threshold = st.slider("Поріг схожості слів (%)", 60, 100, 75, key="table_highlight_threshold")
        relax_short = st.checkbox(
            "Для коротких слів застосовувати поріг 70%", value=True,
            help="За 100% ця опція автоматично не впливає на результат.", key="table_highlight_relax_short",
        )
        font_name = st.selectbox("Шрифт", ["Calibri", "Times New Roman", "Arial"], key="table_highlight_font")
        font_size = st.number_input("Розмір шрифту (pt)", 8, 16, 14, step=1, key="table_highlight_font_size")
        st.info("Вирівнювання маркерів сторінок увімкнено завжди: воно потрібне для зіставлення фрагментів експертом.")

    options = HighlightOptions(
        threshold=threshold,
        font_name=font_name,
        font_size=int(font_size),
        table_index=table_index,
        first_row=int(start),
        last_row=int(end),
        relax_short_words=relax_short,
    )
    if st.session_state.get(_OPTIONS_KEY) != options:
        st.session_state.pop(_RESULT_KEY, None)

    if st.button("Обробити таблицю", type="primary", key="table_highlight_process"):
        progress = st.progress(0, text="Підготовка таблиці…")

        def update(current: int, total: int, row_number: int) -> None:
            percentage = int(current / total * 100) if total else 100
            progress.progress(percentage, text=f"Обробка рядка {row_number} з {total}…")

        try:
            result = process_document(data, options, update)
        except (DocumentValidationError, ValueError) as error:
            progress.empty()
            st.error(f"❌ {error}")
        except Exception as error:
            progress.empty()
            st.error(f"❌ Не вдалося обробити таблицю: {error}")
        else:
            progress.progress(100, text="Готово")
            st.session_state[_RESULT_KEY] = (result, uploaded.name)
            st.session_state[_OPTIONS_KEY] = options

    saved = st.session_state.get(_RESULT_KEY)
    if not saved:
        return
    result, source_name = saved
    stats = result.stats
    columns = st.columns(4)
    columns[0].metric("Оброблено рядків", stats.processed_rows)
    columns[1].metric("Пропущено рядків", stats.skipped_rows)
    columns[2].metric("Точні збіги", stats.exact_words)
    columns[3].metric("Нечіткі збіги", stats.fuzzy_words)
    st.caption(f"Додано порожніх абзаців для вирівнювання маркерів: {stats.padding_paragraphs}.")
    if result.warnings:
        with st.expander(f"Попередження ({len(result.warnings)})", expanded=True):
            for warning in result.warnings:
                st.warning(f"Рядок {warning.row_number}: {warning.message}")
    st.download_button(
        "Завантажити підсвічений DOCX",
        data=result.document_bytes,
        file_name=f"highlighted_{source_name}",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="table_highlight_download",
    )
