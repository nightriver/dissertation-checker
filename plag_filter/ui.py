"""Екран режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §8, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.6, §9.2 (етап 1).

Постраничний перегляд «варіант б»: заголовок і завантажувач, картка автора,
лічильники, дії з проєктом, перегляд аркуша з панеллю джерел, згорнута
таблиця всіх джерел. Завантаження очищеного PDF додає протокол у кінець
файлу — §10.2, етап 8.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st

from parser.extractor import extract_dissertation_year
from plag_filter import fetch as fetch_module
from plag_filter.checker import CHUNK_SIZE, MAX_WORKERS, check_batch, pending_count, recheck_source
from plag_filter.pdf import (
    UnsupportedReportError,
    append_protocol,
    filter_pdf,
    parse_report,
    render_page_png,
)
from plag_filter.project import from_json, new_project, protocol_paragraphs, to_json
from plag_filter.rules import derive_initials, extract_author, extract_title_year, order_numbers, recompute, top20_share
from plag_filter.types import PlagProject, PlagReport, REASON_LABELS
from ui_helpers import file_sha256

# Ліміт завантажуваного звіту — PLAN_PLAG_FILTER.md, §8, пункт 1.
MAX_PLAG_PDF_BYTES = 30 * 1024 * 1024

_SHA_KEY = "plag_report_sha256"
_REPORT_KEY = "plag_report"
_DATA_KEY = "plag_data"
_PROJECT_KEY = "plag_project"
_PAGE_KEY = "plag_page"

_RESETTABLE_KEYS = (
    _PROJECT_KEY,
    _REPORT_KEY,
    _DATA_KEY,
    _PAGE_KEY,
    "plag_surname",
    "plag_given_name",
    "plag_patronymic",
    "plag_year",
)

_SORT_OPTIONS = {
    "за W": "width",
    "за найдовшим фрагментом": "longest",
    "за номером": "number",
}


def _filtered_pdf_name(filename: str) -> str:
    """Ім'я файлу очищеного PDF з протоколом — PLAN_PLAG_FILTER.md, §8."""
    return f"{Path(filename).stem}_filtered.pdf"


def _reset_if_new_file(data: bytes) -> None:
    """Новий файл (інший SHA-256) скидає проєкт — PLAN_PLAG_FILTER.md, §8."""
    sha = file_sha256(data)
    if st.session_state.get(_SHA_KEY) == sha:
        return
    st.session_state[_SHA_KEY] = sha
    for key in _RESETTABLE_KEYS:
        st.session_state.pop(key, None)


def _year_hint_lines(data: bytes) -> list[dict]:
    """Рядки аркушів 4–6 для `extract_dissertation_year` — PLAN_PLAG_FILTER.md, §2, §8."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines: list[dict] = []
        for page_index in range(3, min(6, doc.page_count)):
            for line in doc[page_index].get_text().split("\n"):
                lines.append({"line": line, "page": page_index + 1})
        return lines
    finally:
        doc.close()


def _format_percent(row) -> str:
    return row.percent_text if row.percent_text else "?"


def _evidence_text(state) -> str:
    check = state.check
    if check is None:
        return ""
    parts: list[str] = []
    if state.reason == "own_work" and check.author_hit is not None:
        parts.append(
            f"Підпис автора: «{check.author_hit.snippet}» (стор. документа {check.author_hit.page})"
        )
    elif state.reason == "cites_author" and check.author_hit is not None:
        parts.append(
            f"Цитування автора: «{check.author_hit.snippet}» (стор. документа {check.author_hit.page})"
        )
    elif state.reason == "unavailable":
        parts.append(f"Помилка: {check.error}")
    elif check.doc_date is not None:
        basis = check.date_basis or "—"
        parts.append(
            f"Дата документа: {check.doc_date.start.isoformat()}–{check.doc_date.end.isoformat()} · {basis}"
        )
    elif check.date_conflict:
        parts.append("Суперечливі дати в документі")
    if check.url_year_hint is not None:
        parts.append(f"рік в адресі: {check.url_year_hint}")
    return " · ".join(parts)


def _pages_with_disputed(report: PlagReport, project: PlagProject) -> list[int]:
    pages: set[int] = set()
    for number, state in project.states.items():
        if state.decision == "disputed":
            pages.update(report.pages_by_number.get(number, ()))
    return sorted(pages)


def _next_disputed_page(report: PlagReport, project: PlagProject, current_index: int) -> int:
    disputed_pages = _pages_with_disputed(report, project)
    if not disputed_pages:
        return current_index
    for page in disputed_pages:
        if page > current_index:
            return page
    return disputed_pages[0]


def _render_source_row(
    number: int, row, state, report: PlagReport, project: PlagProject
) -> None:
    with st.container(border=True):
        st.markdown(f"**№ {number} · {row.label} · {_format_percent(row)}**")
        st.caption(REASON_LABELS[state.reason])
        evidence = _evidence_text(state)
        if evidence:
            st.caption(evidence)

        button_cols = st.columns(3)
        with button_cols[0]:
            st.link_button("Відкрити", row.urls[0], key=f"plag_open_{number}")
        with button_cols[1]:
            if st.button("Залишити", key=f"plag_keep_{number}"):
                state.manual = "keep"
                recompute(project, report)
                st.rerun()
        with button_cols[2]:
            if st.button("Виключити", key=f"plag_exclude_{number}"):
                state.manual = "exclude"
                recompute(project, report)
                st.rerun()

        if state.manual is not None:
            if st.button("Скасувати ручне рішення", key=f"plag_cancel_manual_{number}"):
                state.manual = None
                recompute(project, report)
                st.rerun()

        if state.reason == "unavailable":
            alt = st.text_input(
                "Інша адреса", value=state.alt_url or "", key=f"plag_alt_{number}"
            )
            if st.button("Перевірити за цією адресою", key=f"plag_recheck_{number}"):
                state.alt_url = alt or None
                with tempfile.TemporaryDirectory() as tmp:
                    recheck_source(
                        report, project, number, alt or row.urls[0], tmp_dir=Path(tmp)
                    )
                st.rerun()


def _seed_author_fields(data: bytes, report: PlagReport, project: PlagProject) -> None:
    """Заповнити поля автора при першій появі екрана — §9.2 етап 1."""
    if "plag_surname" not in st.session_state:
        guess = extract_author(report.title_text)
        st.session_state["plag_surname"] = project.surname or (guess.surname if guess else "")
        st.session_state["plag_given_name"] = project.given_name or (
            guess.given_name if guess else ""
        )
        st.session_state["plag_patronymic"] = project.patronymic or (
            guess.patronymic if guess else ""
        )
    if "plag_year" not in st.session_state:
        st.session_state["plag_year"] = (
            project.year
            or extract_dissertation_year(_year_hint_lines(data))
            or extract_title_year(report.title_text)
            or 2000
        )


def _render_author_card(data: bytes, report: PlagReport, project: PlagProject) -> None:
    """Картка автора з титулу дисертації — PLAN_PLAG_FILTER_V2.md, §9.2 етап 1."""
    guess = extract_author(report.title_text)
    _seed_author_fields(data, report, project)

    surname = st.session_state["plag_surname"]
    given_name = st.session_state["plag_given_name"]
    patronymic = st.session_state["plag_patronymic"]
    year = st.session_state["plag_year"]

    full_name = " ".join(part for part in (surname, given_name, patronymic) if part)
    st.markdown(f"**Автор: {full_name} · {int(year)}**")

    if guess is None:
        st.warning("Автора в титулі не знайдено — заповніть поля")
    elif guess.confidence == "single":
        st.warning("Автора знайдено в одному місці титулу — перевірте")

    with st.expander("Виправити автора", expanded=guess is None):
        col_surname, col_given, col_patronymic, col_year = st.columns(4)
        with col_surname:
            surname = st.text_input("Прізвище", key="plag_surname")
        with col_given:
            given_name = st.text_input("Ім'я", key="plag_given_name")
        with col_patronymic:
            patronymic = st.text_input("По батькові", key="plag_patronymic")
        with col_year:
            year = st.number_input(
                "Рік дисертації", min_value=1900, max_value=2099, step=1, key="plag_year"
            )

        can_confirm = (
            bool(surname.strip()) and bool(given_name.strip()) and 1900 <= int(year) <= 2099
        )
        if st.button(
            "Все вірно — перевірити джерела", key="plag_confirm", disabled=not can_confirm
        ):
            project.surname = surname
            project.given_name = given_name
            project.patronymic = patronymic
            project.year = int(year)
            project.initials = derive_initials(given_name, patronymic)
            project.confirmed = True
            recompute(project, report)
            st.rerun()

    if project.confirmed and (
        surname != project.surname
        or given_name != project.given_name
        or patronymic != project.patronymic
        or int(year) != project.year
    ):
        project.confirmed = False
        recompute(project, report)
        st.warning("Автора змінено — підтвердіть ще раз")


def _render_counters(report: PlagReport, project: PlagProject) -> None:
    ge_01 = sum(
        1 for row in report.rows.values() if row.percent is None or row.percent >= 0.1
    )
    reason_counts: dict[str, int] = {}
    for state in project.states.values():
        reason_counts[state.reason] = reason_counts.get(state.reason, 0) + 1
    checked = sum(1 for state in project.states.values() if state.check is not None)
    disputed = sum(1 for state in project.states.values() if state.decision == "disputed")
    unknown_pct = sum(1 for row in report.rows.values() if row.percent is None)
    share = top20_share(project, report)
    share_text = f"{share:.0%}" if share is not None else "немає виділень"

    row1 = st.columns(5)
    row1[0].metric("Джерел ≥ 0,1 %", ge_01)
    row1[1].metric("Перевірено", checked)
    row1[2].metric("Власні роботи", reason_counts.get("own_work", 0))
    row1[3].metric("Пізніші", reason_counts.get("later", 0))
    row1[4].metric("Раніші", reason_counts.get("earlier", 0))

    row2 = st.columns(4)
    row2[0].metric("Спірні", disputed)
    row2[1].metric("Недоступні", reason_counts.get("unavailable", 0))
    row2[2].metric("Нижче 0,1 %", reason_counts.get("below_threshold", 0))
    row2[3].metric("Відсоток не розпізнано", unknown_pct)

    st.metric(
        "Частка довжини виділень у топ-20 серед джерел, що залишилися", share_text
    )


def _render_actions(data: bytes, report: PlagReport, project: PlagProject, filename: str) -> None:
    action_cols = st.columns(3)
    with action_cols[0]:
        st.download_button(
            "Зберегти проєкт (JSON)",
            data=to_json(project),
            file_name=f"{Path(filename).stem}.plag-project.json",
            mime="application/json",
            key="plag_save_project",
        )

    with action_cols[1]:
        restore_upload = st.file_uploader(
            "Відновити проєкт", type=["json"], key="plag_restore_upload"
        )
        if restore_upload is not None and st.button("Застосувати відновлений проєкт", key="plag_restore_apply"):
            try:
                restored = from_json(restore_upload.getvalue().decode("utf-8"), report)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[_PROJECT_KEY] = restored
                st.rerun()

    with action_cols[2]:
        excluded = {
            number for number, state in project.states.items() if state.decision == "exclude"
        }
        cleaned = filter_pdf(data, report, excluded)
        with_protocol = append_protocol(cleaned, protocol_paragraphs(project, report))
        st.download_button(
            "Завантажити очищений PDF",
            data=with_protocol,
            file_name=_filtered_pdf_name(filename),
            mime="application/pdf",
            key="plag_download_cleaned",
        )

    st.caption("Збережіть проєкт, щоб не втратити рішення")


def _visible_source_count(report: PlagReport) -> int:
    """Джерела ≥ 0,1 % або з нерозпізнаним відсотком — PLAN_PLAG_FILTER_V2.md, §9.2 етап 3."""
    return sum(1 for row in report.rows.values() if row.percent is None or row.percent >= 0.1)


def _render_autocheck(report: PlagReport, project: PlagProject) -> None:
    """Автоматична паралельна перевірка джерел після підтвердження автора —
    PLAN_PLAG_FILTER_V2.md, §9.2, етап 3."""
    if not project.confirmed:
        return
    remaining = pending_count(report, project)
    if remaining <= 0:
        return

    total = _visible_source_count(report)
    checked = max(total - remaining, 0)

    if st.session_state.get("plag_check_stopped", False):
        st.info("Перевірку зупинено")
        if st.button("Продовжити перевірку", key="plag_resume"):
            st.session_state["plag_check_stopped"] = False
            st.rerun()
        return

    st.progress(checked / total if total else 0.0, text=f"Перевірено {checked} з {total}")
    if st.button("Зупинити", key="plag_stop"):
        st.session_state["plag_check_stopped"] = True
        st.rerun()
        return

    with tempfile.TemporaryDirectory() as tmp:
        check_batch(
            report,
            project,
            tmp_dir=Path(tmp),
            limit=CHUNK_SIZE,
            workers=MAX_WORKERS,
            fetch=fetch_module.fetch_document,
        )
    st.rerun()


def _render_page_view(data: bytes, report: PlagReport, project: PlagProject) -> None:
    max_page = report.page_count
    if _PAGE_KEY not in st.session_state:
        st.session_state[_PAGE_KEY] = report.body_first + 1

    left_col, right_col = st.columns([3, 2])
    with left_col:
        nav_cols = st.columns(3)
        with nav_cols[0]:
            if st.button("◀ Попередня"):
                st.session_state[_PAGE_KEY] = max(1, st.session_state[_PAGE_KEY] - 1)
        with nav_cols[1]:
            if st.button("Наступна ▶"):
                st.session_state[_PAGE_KEY] = min(max_page, st.session_state[_PAGE_KEY] + 1)
        with nav_cols[2]:
            if st.button("Наступна зі спірним ▶"):
                target = _next_disputed_page(
                    report, project, st.session_state[_PAGE_KEY] - 1
                )
                st.session_state[_PAGE_KEY] = target + 1

        page_number = st.number_input(
            "Аркуш PDF", min_value=1, max_value=max_page, step=1, key=_PAGE_KEY
        )
        page_index = int(page_number) - 1

        excluded = {
            number for number, state in project.states.items() if state.decision == "exclude"
        }
        png = render_page_png(data, report, page_index, excluded)
        st.image(png, use_container_width=True)

    with right_col:
        st.markdown(f"#### Джерела на аркуші {page_index + 1}")
        numbers_on_page = report.numbers_by_page.get(page_index, ())
        visible_numbers = sorted(
            number
            for number in numbers_on_page
            if report.rows[number].percent is None or report.rows[number].percent >= 0.1
        )
        below_count = len(numbers_on_page) - len(visible_numbers)
        for number in visible_numbers:
            _render_source_row(number, report.rows[number], project.states[number], report, project)
        st.caption(f"Ще {below_count} джерел нижче 0,1 % прибрано.")


def _render_all_sources(report: PlagReport, project: PlagProject) -> None:
    with st.expander("Всі джерела", expanded=False):
        sort_label = st.selectbox(
            "Сортування", list(_SORT_OPTIONS), key="plag_sort"
        )
        ordered = order_numbers(project, report, _SORT_OPTIONS[sort_label])
        rows_data = [
            {
                "№": number,
                "Домен": report.rows[number].label,
                "%": report.rows[number].percent_text or "?",
                "W": round(report.highlight_width.get(number, 0.0), 1),
                "Найдовший фрагмент": round(report.longest_run.get(number, 0.0), 1),
                "Рішення": project.states[number].decision,
                "Причина": REASON_LABELS[project.states[number].reason],
            }
            for number in ordered
        ]
        st.dataframe(pd.DataFrame(rows_data), use_container_width=True, hide_index=True)

        goto_col, button_col = st.columns([3, 1])
        with goto_col:
            goto_number = st.number_input(
                "Перейти до джерела №",
                min_value=1,
                max_value=max(report.rows),
                step=1,
                key="plag_goto_number",
            )
        with button_col:
            if st.button("Перейти", key="plag_goto_button"):
                number = int(goto_number)
                row = report.rows.get(number)
                if row is not None:
                    pages = report.pages_by_number.get(number)
                    target_page = pages[0] if pages else row.list_page
                    st.session_state[_PAGE_KEY] = target_page + 1
                    st.rerun()


def render_plag_filter_page() -> None:
    """Головна точка входу режиму `?mode=plag-filter` — PLAN_PLAG_FILTER.md, §8."""
    st.title("Очищення звіту Plag")
    uploaded = st.file_uploader(
        "Звіт Plag (PDF)", type=["pdf"], key="plag_upload", help="Один PDF до 30 МБ."
    )
    if uploaded is None:
        return

    data = uploaded.getvalue()
    if len(data) > MAX_PLAG_PDF_BYTES:
        st.error("Файл більший за 30 МБ.")
        return

    _reset_if_new_file(data)

    if _REPORT_KEY not in st.session_state:
        try:
            report = parse_report(data)
        except UnsupportedReportError as exc:
            st.error(f"Звіт не відповідає очікуваному шаблону Plag: {exc.code}.")
            return
        st.session_state[_REPORT_KEY] = report
        st.session_state[_DATA_KEY] = data
        st.session_state[_PROJECT_KEY] = new_project(report, uploaded.name)

    report: PlagReport = st.session_state[_REPORT_KEY]
    data = st.session_state[_DATA_KEY]
    project: PlagProject = st.session_state[_PROJECT_KEY]

    _render_author_card(data, report, project)

    st.divider()
    _render_autocheck(report, project)

    st.divider()
    _render_counters(report, project)

    st.divider()
    _render_actions(data, report, project, uploaded.name)

    st.divider()
    _render_page_view(data, report, project)

    st.divider()
    _render_all_sources(report, project)
