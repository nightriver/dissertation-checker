"""Екран режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §8, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.6, §9.2 (етапи 1, 3, 5–6, 8–9) та
`PLAN_PLAG_FILTER_V3.md`, §7 етап 2 — адреса отриманого документа.

Порядок екрана — §9.2 етап 9: заголовок і завантажувач → картка автора →
перевірка або підсумок двома блоками з кнопкою завантаження → перегляд
аркуша компонентом `plag_filter.viewer` з панеллю джерел → згорнута таблиця
всіх джерел → згорнутий блок «Проєкт» унизу (рідко потрібні дії). Кожна
причина названа своїм ім'ям, без узагальненого слова для непевних рішень.
Завантаження очищеного PDF додає протокол у кінець файлу — §10.2, етап 8
плану 1.
"""

from __future__ import annotations

import html
import os
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
)
from plag_filter.project import from_json, new_project, protocol_paragraphs, to_json
from plag_filter.rules import (
    derive_initials,
    extract_author,
    extract_title_year,
    order_numbers,
    recompute,
    top20_share,
)
from plag_filter.types import PlagProject, PlagReport, REASON_LABELS
from plag_filter.view import apply_viewer_event, viewer_payload
from plag_filter.viewer import render_viewer
from ui_helpers import file_sha256

# Ліміт завантажуваного звіту — PLAN_PLAG_FILTER.md, §8, пункт 1.
MAX_PLAG_PDF_BYTES = 30 * 1024 * 1024

_SHA_KEY = "plag_report_sha256"
_REPORT_KEY = "plag_report"
_DATA_KEY = "plag_data"
_PROJECT_KEY = "plag_project"
_PAGE_KEY = "plag_page"
_CLEANED_PDF_KEY = "plag_cleaned_pdf"

# Режим показу для браузера — PLAN_PLAG_FILTER_V2.md, §8.6.
_DEMO_PDF_ENV = "PLAG_FILTER_DEMO_PDF"
_DEMO_PROJECT_ENV = "PLAG_FILTER_DEMO_PROJECT"

_RESETTABLE_KEYS = (
    _PROJECT_KEY,
    _REPORT_KEY,
    _DATA_KEY,
    _PAGE_KEY,
    _CLEANED_PDF_KEY,
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


def _render_unavailable_form(
    number: int, row, state, report: PlagReport, project: PlagProject
) -> None:
    """Інша адреса й перепроверка недоступного джерела — §9.2 етап 8.

    Ці два віджети лишаються на боці Streamlit під компонентом перегляду.
    """
    with st.container(border=True):
        st.caption(f"№ {number} · {row.label} · {REASON_LABELS[state.reason]}")
        alt = st.text_input("Інша адреса", value=state.alt_url or "", key=f"plag_alt_{number}")
        if st.button("Перевірити за цією адресою", key=f"plag_recheck_{number}"):
            state.alt_url = alt or None
            with tempfile.TemporaryDirectory() as tmp:
                recheck_source(
                    report, project, number, alt or row.urls[0], tmp_dir=Path(tmp)
                )
            st.rerun()


def _render_document_link(number: int, label: str, document_url: str, archive_used: bool) -> None:
    """Адреса документа, який приложение реально отримало — PLAN_PLAG_FILTER_V3.md,
    §7 етап 2. Файл не завантажується застосунком: експерт зберігає його сам
    із браузера за цим посиланням."""
    caption = html.escape(f"№ {number} · {label}")
    href = html.escape(document_url, quote=True)
    mark = ' <span>· Копія з Web Archive</span>' if archive_used else ""
    st.markdown(
        f'<div>{caption}<br><a href="{href}" target="_blank" rel="noopener noreferrer">'
        f"Відкрити знайдений документ</a>{mark}</div>",
        unsafe_allow_html=True,
    )


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

    # Кнопка — поза «Виправити автора»: коли автора з титулу розпізнано вірно,
    # експерт натискає її одразу, не розкриваючи поля.
    can_confirm = bool(surname.strip()) and bool(given_name.strip()) and 1900 <= int(year) <= 2099
    if st.button(
        "Все вірно — перевірити джерела",
        key="plag_confirm",
        disabled=not can_confirm,
        type="primary",
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


# Поділ причин на два блоки підсумку — PLAN_PLAG_FILTER_V2.md, §9.2 етап 9.
# Сума обох блоків = кількості рядків переліку джерел.
_EXCLUDED_REASONS = ("own_work", "cites_author", "later", "below_threshold", "manual_exclude")
_KEPT_REASONS = (
    "earlier",
    "date_unknown",
    "unavailable",
    "date_conflict",
    "same_year",
    "manual_keep",
)


def _reason_counts(project: PlagProject) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state in project.states.values():
        counts[state.reason] = counts.get(state.reason, 0) + 1
    return counts


def _render_summary(data: bytes, report: PlagReport, project: PlagProject, filename: str) -> None:
    """Підсумок двома блоками з кнопкою завантаження — PLAN_PLAG_FILTER_V2.md,
    §9.2 етап 9."""
    counts = _reason_counts(project)

    st.markdown("**Виключено з PDF**")
    excluded_cols = st.columns(len(_EXCLUDED_REASONS))
    excluded_labels = {
        "own_work": "Власні роботи",
        "cites_author": "Цитують автора",
        "later": "Пізніші за дисертацію",
        "below_threshold": "Нижче 0,1 %",
        "manual_exclude": "Виключено вручну",
    }
    for col, reason in zip(excluded_cols, _EXCLUDED_REASONS):
        col.metric(excluded_labels[reason], counts.get(reason, 0))

    st.markdown("**Залишено в PDF**")
    kept_labels = {
        "earlier": "Раніші за дисертацію",
        "date_unknown": "Дату не встановлено",
        "unavailable": "Документ недоступний",
        "date_conflict": "Суперечливі дати",
        "same_year": "Той самий рік",
        "manual_keep": "Залишено вручну",
    }
    not_checked = counts.get("unchecked", 0) + counts.get("unconfirmed", 0)
    kept_cols = st.columns(len(_KEPT_REASONS) + 1)
    for col, reason in zip(kept_cols, _KEPT_REASONS):
        col.metric(kept_labels[reason], counts.get(reason, 0))
    kept_cols[-1].metric("Ще не перевірено", not_checked)

    unknown_pct = sum(1 for row in report.rows.values() if row.percent is None)
    checked = sum(1 for state in project.states.values() if state.check is not None)
    total = _visible_source_count(report)
    st.caption(f"Відсоток не розпізнано: {unknown_pct}")
    st.caption(
        f"Перевірено {checked} з {total}; не вдалося завантажити "
        f"{counts.get('unavailable', 0)}; дату не встановлено — "
        f"{counts.get('date_unknown', 0)}"
    )

    excluded = {
        number for number, state in project.states.items() if state.decision == "exclude"
    }
    with_protocol = _cleaned_pdf_with_protocol(data, report, project, excluded)
    st.download_button(
        "Завантажити очищений PDF",
        data=with_protocol,
        file_name=_filtered_pdf_name(filename),
        mime="application/pdf",
        key="plag_download_cleaned",
    )


def _cleaned_pdf_with_protocol(
    data: bytes, report: PlagReport, project: PlagProject, excluded: set[int]
) -> bytes:
    """Очищений PDF з протоколом, побудований раз на набір виключень — §9.2, етап 7."""
    key = frozenset(excluded)
    cache: dict[frozenset[int], bytes] = st.session_state.setdefault(_CLEANED_PDF_KEY, {})
    cached = cache.get(key)
    if cached is not None:
        return cached
    cleaned = filter_pdf(data, report, excluded)
    with_protocol = append_protocol(cleaned, protocol_paragraphs(project, report))
    cache[key] = with_protocol
    return with_protocol


def _render_project_expander(report: PlagReport, project: PlagProject, filename: str) -> None:
    """Дії з проєктом — рідко потрібні, тому в кінці екрана —
    PLAN_PLAG_FILTER_V2.md, §9.2 етап 9."""
    with st.expander("Проєкт", expanded=False):
        action_cols = st.columns(2)
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
            if restore_upload is not None and st.button(
                "Застосувати відновлений проєкт", key="plag_restore_apply"
            ):
                try:
                    restored = from_json(restore_upload.getvalue().decode("utf-8"), report)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[_PROJECT_KEY] = restored
                    st.rerun()

        st.caption("Потрібно лише, щоб продовжити роботу в іншій сесії")


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


def _render_check_or_summary(
    data: bytes, report: PlagReport, project: PlagProject, filename: str
) -> None:
    """Перевірка, поки не завершиться, або підсумок двома блоками —
    PLAN_PLAG_FILTER_V2.md, §9.2 етап 9."""
    if not project.confirmed:
        return
    if pending_count(report, project) > 0:
        _render_autocheck(report, project)
    else:
        _render_summary(data, report, project, filename)


@st.fragment
def _render_page_view(data: bytes, report: PlagReport, project: PlagProject) -> None:
    """Перегляд аркуша компонентом — PLAN_PLAG_FILTER_V2.md, §9.2 етап 8.

    Панель джерел і наведення малює компонент; подія від нього йде в
    `apply_viewer_event`, після чого сторінка та лічильники перемальовуються.
    """
    if _PAGE_KEY not in st.session_state:
        st.session_state[_PAGE_KEY] = report.body_first + 1

    show_excluded = st.checkbox("Показувати виключені", key="plag_show_excluded")
    page = int(st.session_state[_PAGE_KEY])
    payload = viewer_payload(data, report, project, page, show_excluded)
    st.markdown(f"#### Джерела на аркуші {payload['page']}")

    event = render_viewer(payload, key="plag_viewer")
    if event is not None and apply_viewer_event(project, report, event):
        st.rerun()

    for source in payload["sources"]:
        if source["document_url"]:
            state = project.states[source["number"]]
            archive_used = state.check is not None and state.check.archive_used
            _render_document_link(source["number"], source["label"], source["document_url"], archive_used)

    page_index = payload["page"] - 1
    for number in sorted(report.numbers_by_page.get(page_index, ())):
        row = report.rows[number]
        state = project.states[number]
        if state.reason == "unavailable" and (row.percent is None or row.percent >= 0.1):
            _render_unavailable_form(number, row, state, report, project)


def _render_all_sources(report: PlagReport, project: PlagProject) -> None:
    with st.expander("Усі джерела", expanded=False):
        share = top20_share(project, report)
        share_text = f"{share:.0%}" if share is not None else "немає виділень"
        st.metric(
            "Частка довжини виділень у топ-20 серед джерел, що залишилися", share_text
        )

        sort_label = st.selectbox(
            "Сортування", list(_SORT_OPTIONS), key="plag_sort"
        )
        ordered = order_numbers(project, report, _SORT_OPTIONS[sort_label])
        rows_data = [
            {
                "№": number,
                "Домен": report.rows[number].urls[0] if report.rows[number].urls else "",
                "%": report.rows[number].percent_text or "?",
                "W": round(report.highlight_width.get(number, 0.0), 1),
                "Найдовший фрагмент": round(report.longest_run.get(number, 0.0), 1),
                "Рішення": project.states[number].decision,
                "Причина": REASON_LABELS[project.states[number].reason],
            }
            for number in ordered
        ]
        st.dataframe(
            pd.DataFrame(rows_data),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Домен": st.column_config.LinkColumn(
                    "Домен", display_text=r"(?:https?://)?(?:www\.)?([^/]+)"
                ),
            },
        )

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


def _demo_source() -> tuple[bytes, str] | None:
    """Звіт із змінної оточення для показу в браузері — PLAN_PLAG_FILTER_V2.md, §8.6."""
    demo_pdf = os.environ.get(_DEMO_PDF_ENV)
    if not demo_pdf:
        return None
    path = Path(demo_pdf)
    if not path.is_file():
        st.error(f"Файл показу не знайдено: {demo_pdf}")
        return None
    return path.read_bytes(), path.name


def _apply_demo_project(report: PlagReport) -> None:
    """Проєкт із змінної оточення — PLAN_PLAG_FILTER_V2.md, §8.6."""
    demo_project = os.environ.get(_DEMO_PROJECT_ENV)
    if not demo_project:
        return
    path = Path(demo_project)
    if not path.is_file():
        st.error(f"Проєкт показу не знайдено: {demo_project}")
        return
    try:
        st.session_state[_PROJECT_KEY] = from_json(path.read_text(encoding="utf-8"), report)
    except ValueError as exc:
        st.error(str(exc))


def render_plag_filter_page() -> None:
    """Головна точка входу режиму `?mode=plag-filter` — PLAN_PLAG_FILTER.md, §8."""
    st.title("Очищення звіту Plag")

    demo = _demo_source()
    if demo is None:
        uploaded = st.file_uploader(
            "Звіт Plag (PDF)", type=["pdf"], key="plag_upload", help="Один PDF до 30 МБ."
        )
        if uploaded is None:
            return
        data = uploaded.getvalue()
        filename = uploaded.name
    else:
        data, filename = demo
        st.caption(f"Показ: {filename}")

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
        st.session_state[_PROJECT_KEY] = new_project(report, filename)
        if demo is not None:
            _apply_demo_project(report)

    report: PlagReport = st.session_state[_REPORT_KEY]
    data = st.session_state[_DATA_KEY]
    project: PlagProject = st.session_state[_PROJECT_KEY]

    _render_author_card(data, report, project)

    st.divider()
    _render_check_or_summary(data, report, project, filename)

    st.divider()
    _render_page_view(data, report, project)

    st.divider()
    _render_all_sources(report, project)

    st.divider()
    _render_project_expander(report, project, filename)
