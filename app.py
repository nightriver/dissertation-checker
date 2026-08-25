"""
app.py — Перевірка джерел дисертації
Streamlit Community Cloud entry point.
UI v3: Toast, Tabs, Auto-run, Author extraction + Paragraph Gaps.
"""

import datetime
import statistics
from dataclasses import dataclass

import plotly.graph_objects as go
import pandas as pd
import streamlit as st

from parser.extractor import (
    extract_lines,
    extract_dissertation_year,
    extract_dissertation_author,
    FileTooLargeError,
    ScannedPDFError,
    UnsupportedFormatError,
)
from parser.bibliography import (
    split_zones,
    split_zones_manual,
    parse_bibliography,
    BibliographyNotFoundError,
)
from parser.citations import find_citations, compare
from parser.year_extractor import extract_years_with_confidence
from parser.dstu_validator import validate_bibliography, DstuStatus
from parser.paragraph_analyzer import MIN_SUSPICIOUS_SENTENCES
from parser.anomalies import find_anachronisms
from parser.duplicates import DuplicateGroup, find_duplicates
from parser.text_forensics import scan_text_forensics
from parser.types import Severity
from ui_helpers import (
    format_number_ranges,
    lines_to_tuple,
    tuple_to_lines,
    make_file_key,
    reset_file_scoped_state,
    is_compare_mode,
    is_search_mode,
    file_sha256,
    make_pair_key,
    reset_pair_scoped_state,
    has_usable_text_lines,
    validate_search_upload,
)
from compare.matcher import compare_documents
from compare.prepare import prepare_document_for_comparison
from compare.presentation import format_physical_pages, render_comparison_table


# ---------------------------------------------------------------------------
# Конфігурація сторінки
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Перевірка джерел дисертації",
    page_icon="📚",
    layout="centered",
)

SEVERITY_LABEL = {
    Severity.PROOF: "🔴 Неможливо",
    Severity.SUSPECT: "🟡 Підозріло",
}

DUPLICATE_KIND_LABEL = {
    "exact": "Точний збіг",
    "same_title_diff_year": "Та сама назва, інший рік",
    "near": "Майже ідентичні",
}


# ---------------------------------------------------------------------------
# Кешовані обчислення
# ---------------------------------------------------------------------------

@dataclass
class AnalysisBundle:
    """
    Один зібраний результат аналізу бібліографії: один парсинг, один запис
    у кеші, усі похідні дані поруч.

    Анахронізми сюди НЕ входять: вони залежать від року дисертації, який
    експерт може ввести вручну, тобто змінюються без зміни бібліографії.
    Вони дешеві (O(n)) і рахуються поза кешем.
    """
    bibliography: dict[int, str]
    citations: dict[int, str]
    result: dict
    years: dict[int, int | None]
    year_confidence: dict[int, str]
    dstu: dict[int, DstuStatus]
    duplicates: list[DuplicateGroup]


@st.cache_data(show_spinner="Читання файлу…")
def cached_extract(data: bytes, fname: str):
    return extract_lines(data, fname)


@st.cache_data(show_spinner="Аналіз структури…")
def cached_split_zones(lines_tuple: tuple):
    """
    Кешоване визначення зон документа.

    split_zones() всередині робить повний parse_bibliography() на кожного
    кандидата-заголовка. Коли заголовок повторюється колонтитулом на десятках
    сторінок, без кешу це десятки повних парсингів хвоста документа на КОЖНЕ
    натискання будь-якого віджета.
    """
    return split_zones(tuple_to_lines(lines_tuple))


@st.cache_data(show_spinner="Аналіз джерел…")
def cached_analyze(bibliography_lines_tuple: tuple, body_lines_tuple: tuple) -> AnalysisBundle:
    """
    Кешована функція аналізу.

    Отримує дані явно через параметри — без звернень до session state —
    що гарантує коректну інвалідацію кешу при зміні зони бібліографії
    (наприклад, при переході між авто- і ручним режимами).
    """
    bibliography_lines = tuple_to_lines(bibliography_lines_tuple)
    body_lines = tuple_to_lines(body_lines_tuple)

    bibliography = parse_bibliography(bibliography_lines)
    citations = find_citations(body_lines)
    result = compare(bibliography, citations)
    years, confidence = extract_years_with_confidence(bibliography)

    return AnalysisBundle(
        bibliography=bibliography,
        citations=citations,
        result=result,
        years=years,
        year_confidence=confidence,
        dstu=validate_bibliography(bibliography),
        duplicates=find_duplicates(bibliography, years),
    )


@st.cache_data(show_spinner="Пошук підміни символів…")
def cached_forensics(lines_tuple: tuple):
    return scan_text_forensics(tuple_to_lines(lines_tuple))


@st.cache_data(show_spinner="Підготовка тексту…")
def cached_prepare_compare(lines_tuple: tuple):
    return prepare_document_for_comparison(tuple_to_lines(lines_tuple))[0]


@st.cache_data(show_spinner="Пошук і вирівнювання збігів…")
def cached_compare_documents(lines_a_tuple: tuple, lines_b_tuple: tuple):
    return compare_documents(tuple_to_lines(lines_a_tuple), tuple_to_lines(lines_b_tuple))


# ---------------------------------------------------------------------------
# Секції вкладки «Перевірка джерел»
# ---------------------------------------------------------------------------

def _render_anachronisms(anachronisms: dict, bibliography: dict[int, str]) -> None:
    """
    Заголовок навмисно не «Неможливі джерела»: у секції живуть і 🔴, і 🟡,
    і назва «неможливі» обмовляла б 🟡-знахідки.
    """
    st.divider()
    st.markdown("#### 🔴 Джерела новіші за дисертацію")
    st.caption(
        "Джерело не може бути видане пізніше за роботу, яка на нього посилається. "
        "🟡 — різниця в один рік або неточно визначений рік видання: "
        "це може бути рік подання проти року захисту."
    )

    # 🔴 згори, всередині рівня — за спаданням різниці: найгірше видно
    # без прокручування.
    order = sorted(
        anachronisms.items(),
        key=lambda item: (item[1].severity is not Severity.PROOF, -item[1].delta),
    )
    rows = [
        {
            "№": num,
            "Рівень": SEVERITY_LABEL[hit.severity],
            "Рік видання": hit.source_year,
            "Різниця": f"+{hit.delta}" if hit.delta > 0 else "—",
            "Підстава": hit.reason,
            "Запис": bibliography.get(num, "—"),
        }
        for num, hit in order
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_duplicates(duplicates: list[DuplicateGroup], bibliography: dict[int, str]) -> None:
    st.divider()
    st.markdown("#### 🟡 Дублікати у списку")
    st.caption(
        "Записи з однаковою або майже однаковою назвою. Це може бути й помилка "
        "оформлення (два видання однієї монографії), тож останнє слово за вами."
    )

    rows = []
    for index, group in enumerate(duplicates, start=1):
        for num in group.numbers:
            rows.append({
                "Група": index,
                "№": num,
                "Схожість": f"{group.similarity:.2f}",
                "Тип": DUPLICATE_KIND_LABEL.get(group.kind, group.kind),
                "Запис": bibliography.get(num, "—"),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Скільки позицій списку є повторами — для нормативного мінімуму в 200
    # джерел це прямо стосується того, чи набрано норму насправді.
    extra = sum(len(group.numbers) - 1 for group in duplicates)
    total = len(bibliography)
    unique = total - extra
    st.caption(
        f"У списку {total} позицій, з них {extra} — повтори. "
        f"Унікальних джерел: {unique}."
    )


def _render_year_chart(
    years: dict[int, int | None],
    anachronisms: dict,
    dissertation_year: int | None,
) -> None:
    normal_counts: dict[int, int] = {}
    flagged_counts: dict[int, int] = {}

    for num, year in years.items():
        if year is None:
            continue
        bucket = flagged_counts if num in anachronisms else normal_counts
        bucket[year] = bucket.get(year, 0) + 1

    all_years = sorted(set(normal_counts) | set(flagged_counts))
    if not all_years:
        st.info("Роки видання у джерелах не виявлено.")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=all_years,
        y=[normal_counts.get(y, 0) for y in all_years],
        name="Звичайні джерела",
        marker_color="#4f98a3",
    ))
    if flagged_counts:
        fig.add_trace(go.Bar(
            x=all_years,
            y=[flagged_counts.get(y, 0) for y in all_years],
            name="Анахронізми",
            marker_color="#d13b3b",
        ))

    shapes = []
    annotations = []
    if dissertation_year and min(all_years) <= dissertation_year <= max(all_years):
        shapes.append(dict(
            type="line", x0=dissertation_year, x1=dissertation_year, y0=0, y1=1,
            yref="paper", line=dict(color="#FF5000", width=2, dash="dash"),
        ))
        annotations.append(dict(
            x=dissertation_year, y=1, yref="paper", text=f"Рік дисертації ({dissertation_year})",
            showarrow=False, xanchor="left", yanchor="bottom",
            font=dict(color="#FF5000", size=11),
        ))

    fig.update_layout(
        barmode="stack",
        xaxis_title="Рік", yaxis_title="Кількість джерел",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=30, b=0), height=300,
        shapes=shapes, annotations=annotations,
        showlegend=bool(flagged_counts),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    values = [y for y in years.values() if y is not None]
    if len(values) >= 2:
        c1, c2, c3 = st.columns(3)
        c1.metric("Найстаріше джерело", min(values))
        c2.metric("Медіана", int(statistics.median(values)))
        c3.metric("Найновіше джерело", max(values))


# ---------------------------------------------------------------------------
# render_tab_checker — Перевірка джерел
# ---------------------------------------------------------------------------

def render_tab_checker(zone_result, dissertation_year: int | None = None) -> None:
    bundle = cached_analyze(
        lines_to_tuple(zone_result.bibliography),
        lines_to_tuple(zone_result.body),
    )

    bibliography = bundle.bibliography
    if not bibliography:
        st.error(
            "❌ У знайденому розділі не виявлено пронумерованих джерел. "
            "Переконайтеся, що записи мають формат «1. Автор…» або «[1] Автор…»."
        )
        return

    result = bundle.result
    citations_dict = bundle.citations
    orphans_sorted = sorted(result["orphans"])
    used_sorted = sorted(result["used"])

    total = len(result["all_sources"])
    used_count = len(result["used"])
    orphan_count = len(result["orphans"])
    used_pct = used_count / total * 100 if total else 0
    orphan_pct = orphan_count / total * 100 if total else 0

    anachronisms = find_anachronisms(
        bundle.years,
        bundle.year_confidence,
        dissertation_year,
        current_year=datetime.datetime.now().year,
    )
    duplicates = bundle.duplicates

    # Додаткові колонки з'являються ТІЛЬКИ коли знахідки є — щоб на чистій
    # роботі не висів нуль, який нема з чим співвіднести. Тому жорсткий індекс
    # cols[3] не годиться: метрики збираються в список і розкладаються
    # за фактичною довжиною.
    st.divider()
    metrics = [
        ("Джерел у списку", total, None, "normal"),
        ("Використовуються у тексті", used_count, f"{used_pct:.0f}%", "normal"),
        (
            "Не згадуються у тексті",
            orphan_count,
            f"-{orphan_pct:.0f}%" if orphan_count else None,
            "inverse",
        ),
    ]
    if anachronisms:
        metrics.append(("🔴 Анахронізмів", len(anachronisms), None, "off"))
    if duplicates:
        metrics.append(("🟡 Дублікатів", len(duplicates), None, "off"))

    cols = st.columns(len(metrics))
    for col, (label, value, delta, delta_color) in zip(cols, metrics):
        col.metric(label, value, delta=delta, delta_color=delta_color)

    # Анахронізми й дублікати йдуть ДО сиріт: сирота — це привід подивитися,
    # а джерело з майбутнього — знахідка.
    if anachronisms:
        _render_anachronisms(anachronisms, bibliography)

    if duplicates:
        _render_duplicates(duplicates, bibliography)

    if orphans_sorted:
        st.divider()
        st.markdown("#### ⚠️ Джерела, не згадані у тексті")
        st.markdown("**Номери джерел:**")
        st.code(format_number_ranges(orphans_sorted), language=None)
        orphan_rows = [{"№": num, "Запис": bibliography.get(num, "—")} for num in orphans_sorted]
        st.dataframe(pd.DataFrame(orphan_rows), use_container_width=True, hide_index=True)
    else:
        st.divider()
        st.success("🎉 Усі джерела зі списку згадуються у тексті дисертації!")

    if used_sorted:
        st.divider()
        with st.expander(f"✅ Використані джерела ({used_count})", expanded=False):
            used_rows = [
                {
                    "№": num,
                    "Запис": bibliography.get(num, "—"),
                    "Посилання у тексті": citations_dict.get(num, "") or "—",
                }
                for num in used_sorted
            ]
            st.dataframe(pd.DataFrame(used_rows), use_container_width=True, hide_index=True)

    phantom = sorted(result.get("phantom", []))
    if phantom:
        st.divider()
        st.markdown("#### 👻 Фантомні посилання")
        st.caption("Ці номери є у тексті, але відсутні у списку літератури.")
        st.markdown("**Номери посилань:**")
        st.code(format_number_ranges(phantom), language=None)
        phantom_rows = [{"№": num, "Посилання у тексті": citations_dict.get(num, "") or "—"} for num in phantom]
        st.dataframe(pd.DataFrame(phantom_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### 📊 Розподіл джерел за роками видання")
    _render_year_chart(bundle.years, anachronisms, dissertation_year)

    st.divider()
    st.markdown("#### 📐 Перевірка ДСТУ 8302:2015")

    dstu_results = bundle.dstu
    ok_count = sum(1 for s in dstu_results.values() if s == DstuStatus.DSTU)
    partial_count = sum(1 for s in dstu_results.values() if s == DstuStatus.PARTIAL)
    other_count = sum(1 for s in dstu_results.values() if s == DstuStatus.OTHER)

    dc1, dc2, dc3 = st.columns(3)
    dc1.metric("✅ Відповідають ДСТУ", ok_count)
    dc2.metric("⚠️ Частково", partial_count)
    dc3.metric("❌ Інший формат", other_count)

    non_dstu = [(num, s) for num, s in dstu_results.items() if s != DstuStatus.DSTU]

    if non_dstu:
        with st.expander(f"Показати джерела не за ДСТУ ({len(non_dstu)})", expanded=False):
            dstu_rows = [
                {
                    "№": num,
                    "Статус": "⚠️ Частково" if s == DstuStatus.PARTIAL else "❌ Інший формат",
                    "Запис": bibliography.get(num, "—"),
                }
                for num, s in sorted(non_dstu)
            ]
            st.dataframe(pd.DataFrame(dstu_rows), use_container_width=True, hide_index=True)
    else:
        st.success("🎉 Усі джерела відповідають вимогам ДСТУ 8302:2015!")


# ---------------------------------------------------------------------------
# Допоміжна функція для рендерингу результатів абзаців
# ---------------------------------------------------------------------------

def _render_paragraph_gap_results(pgr) -> None:
    if pgr.docx_mode:
        st.info("ℹ️ Для DOCX файлів номери сторінок недоступні.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Всього абзаців", pgr.total_paragraphs)
    c2.metric("З посиланнями", pgr.cited_paragraphs)
    c3.metric(
        "Без посилань",
        pgr.clean_paragraphs,
        delta=f"{pgr.clean_pct:.1f}%",
        delta_color="inverse" if pgr.clean_pct > 30 else "off",
    )

    if pgr.suspicious:
        st.divider()
        st.markdown(f"**⚠️ Великі абзаци без посилань ({len(pgr.suspicious)})**")
        st.caption(
            f"Абзаци ≥ {MIN_SUSPICIOUS_SENTENCES} речень без жодного посилання [N]. "
            "Перевірте їх на наявність запозичень."
        )
        rows = [
            {
                "Стор. / Розділ": (
                    str(p["page"]) if p["page"]
                    else (p.get("context_heading") or "—")
                ),
                "Речень": p["sentence_count"],
                "Початок абзацу": p["text"][:120] + "…" if len(p["text"]) > 120 else p["text"],
            }
            for p in pgr.suspicious
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.success("🎉 Великих абзаців без посилань не виявлено!")


# ---------------------------------------------------------------------------
# Підміна символів
# ---------------------------------------------------------------------------

def _render_homoglyphs(lines: list[dict], is_pdf: bool) -> None:
    st.divider()
    st.markdown("#### 🔤 Підміна символів")
    st.caption(
        "Кириличні літери, замінені візуально однаковими латинськими або "
        "грецькими — типовий спосіб обійти пошук збігів. Скануються всі "
        "розділи, включно зі списком літератури."
    )

    forensics = cached_forensics(lines_to_tuple(lines))

    if forensics.total_words == 0:
        st.info("У документі не виявлено тексту для аналізу.")
        return

    if not forensics.hits:
        st.success("🎉 Підміни символів не виявлено.")
        return

    metric_cols = st.columns(3 if is_pdf else 2)
    metric_cols[0].metric("Уражених слів", len(forensics.hits))
    metric_cols[1].metric("Від обсягу тексту", f"{forensics.affected_pct:.2f}%")
    if is_pdf:
        metric_cols[2].metric("Сторінок", len(forensics.pages_affected))

    if forensics.likely_encoding_issue:
        st.error(
            "⚠️ Підміни рівномірно розподілені по всьому документу. "
            "Це радше дефект шрифту або конвертера PDF, ніж навмисна підміна: "
            "справжня підміна точкова за визначенням. "
            "Не робіть висновку про порушення на цій підставі."
        )

    rows = [
        {
            "Сторінка": hit.page if hit.page is not None else "—",
            "У документі": hit.word,
            "Мало бути": hit.restored,
            "Правило": "Змішане слово" if hit.rule == "mixed" else "Самотня літера",
        }
        for hit in forensics.hits
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if is_pdf and forensics.pages_affected:
        st.markdown("**Номери сторінок:**")
        st.code(format_number_ranges(forensics.pages_affected), language=None)


# ---------------------------------------------------------------------------
# render_tab_highlighter — Асистент антиплагіату
# ---------------------------------------------------------------------------

def render_tab_highlighter(
    file_bytes: bytes,
    filename: str,
    zone_result,
    lines: list[dict],
) -> None:
    st.caption(
        "Створює копію PDF з підсвіченими посиланнями [номер, с. XX] "
        "для швидкого ручного маркування у сервісі перевірки плагіату."
    )

    is_pdf = filename.lower().endswith(".pdf")

    if not is_pdf:
        st.info("Підсвітка посилань доступна тільки для PDF файлів.")
    else:
        if "highlighted_pdf" not in st.session_state:
            st.session_state.highlighted_pdf = None
        if "empty_pages" not in st.session_state:
            st.session_state.empty_pages = []
        if "tracked_pages_count" not in st.session_state:
            st.session_state.tracked_pages_count = 0

        if st.button("Згенерувати PDF з підсвіткою", use_container_width=True):
            from parser.highlighter import highlight_citations_pdf

            biblio_page = zone_result.biblio_start_page if zone_result else None

            with st.spinner("Обробка сторінок…"):
                try:
                    pdf_out, empty_pages, tracked = highlight_citations_pdf(
                        file_bytes, biblio_page
                    )
                    st.session_state.highlighted_pdf = pdf_out
                    st.session_state.empty_pages = empty_pages
                    st.session_state.tracked_pages_count = tracked
                    st.toast("PDF з підсвіткою згенеровано!", icon="✅")
                except Exception as e:
                    st.error(f"❌ Помилка при генерації PDF: {e}")
                    return

        if st.session_state.highlighted_pdf:
            st.download_button(
                label="📥 Завантажити PDF з підсвіченими посиланнями",
                data=st.session_state.highlighted_pdf,
                file_name=f"{filename.rsplit('.', 1)[0]}_highlighted.pdf",
                mime="application/pdf",
                type="primary",
            )

            empty_pages = st.session_state.empty_pages
            tracked = st.session_state.tracked_pages_count

            st.divider()
            st.markdown("#### 🔍 Сторінки без посилань")
            st.caption(
                "Ці сторінки не містять жодного посилання у форматі [N]. "
                "Перевірте їх у першу чергу — саме тут найімовірніше "
                "запозичення без зазначення джерела. "
                "Перші 2 сторінки (титул, зміст) та бібліографія виключені."
            )

            if not empty_pages:
                st.success("🎉 На кожній сторінці тексту є хоча б одне посилання.")
            else:
                empty_count = len(empty_pages)
                pct = empty_count / tracked * 100 if tracked else 0

                col1, col2 = st.columns(2)
                col1.metric("Сторінок без посилань", empty_count)
                col2.metric(
                    "Від загального обсягу тексту",
                    f"{pct:.1f}%",
                    help=f"Враховано {tracked} сторінок (без перших 2 і бібліографії)",
                )

                st.markdown("**Номери сторінок:**")
                st.code(format_number_ranges(empty_pages), language=None)

    _render_homoglyphs(lines, is_pdf)

    st.divider()
    st.markdown("#### 🔬 Абзаци без посилань")
    st.caption(
        "Аналізуються лише змістовні розділи (Розділ 1, 2, 3…). "
        "Вступ, зміст, анотація, висновки та бібліографія виключені."
    )

    if "para_gap_result" not in st.session_state:
        st.session_state.para_gap_result = None

    if st.button("Проаналізувати абзаци", use_container_width=True, key="btn_para"):
        from parser.paragraph_analyzer import analyze_paragraph_gaps, ContentBoundsNotFoundError
        with st.spinner("Аналіз абзаців…"):
            try:
                st.session_state.para_gap_result = analyze_paragraph_gaps(
                    file_bytes,
                    filename,
                    lines,
                    zone_result.biblio_start_page if zone_result else None,
                )
                st.toast("Аналіз завершено!", icon="✅")
            except ContentBoundsNotFoundError as e:
                st.error(f"❌ {e}")
            except Exception as e:
                st.error(f"❌ Помилка при аналізі абзаців: {e}")

    pgr = st.session_state.para_gap_result
    if pgr:
        _render_paragraph_gap_results(pgr)


# ===========================================================================
# ГОЛОВНИЙ ПОТІК СТОРІНКИ
# ===========================================================================

def render_two_file_compare_page() -> None:
    """Окремий екран; основний сценарій нижче лишається недоторканим."""
    if st.button("← Повернутися до перевірки джерел", key="compare_back"):
        if "mode" in st.query_params:
            del st.query_params["mode"]
        st.rerun()

    st.title("Порівняння двох робіт")
    st.caption(
        "Завантажте перевірювану дисертацію та ймовірне джерело. "
        "Система показує текстові збіги для експертної перевірки й не визначає напрям запозичення."
    )
    st.info(
        "Знаходить дослівні збіги та змінені фрагменти, у яких збереглися точні "
        "послідовності слів. Не виявляє повністю перефразований текст."
    )

    left_column, right_column = st.columns(2)
    with left_column:
        uploaded_a = st.file_uploader(
            "Перевірювана дисертація", type=["pdf", "docx"],
            key="compare_checked_upload", help="PDF із текстовим шаром або DOCX, до 30 МБ",
        )
    with right_column:
        uploaded_b = st.file_uploader(
            "Ймовірне джерело", type=["pdf", "docx"],
            key="compare_source_upload", help="PDF із текстовим шаром або DOCX, до 30 МБ",
        )

    data_a = uploaded_a.getvalue() if uploaded_a else None
    data_b = uploaded_b.getvalue() if uploaded_b else None
    hash_a = file_sha256(data_a) if data_a is not None else "—"
    hash_b = file_sha256(data_b) if data_b is not None else "—"
    pair_key = (
        make_pair_key(data_a, data_b)
        if data_a is not None and data_b is not None
        else f"checked:{hash_a}|source:{hash_b}"
    )
    reset_pair_scoped_state(st.session_state, pair_key)

    lines_a = lines_b = None
    error_a = error_b = None

    def read_uploaded(uploaded, data):
        if uploaded is None or data is None:
            return None, None
        try:
            extracted = cached_extract(data, uploaded.name)
            if not has_usable_text_lines(extracted):
                return None, (
                    "У файлі не знайдено текстових абзаців. "
                    "Перевірте, чи текст не розміщено лише в таблицях або зображеннях."
                )
            return extracted, None
        except (FileTooLargeError, ScannedPDFError, UnsupportedFormatError) as exc:
            return None, str(exc)
        except Exception as exc:
            return None, f"Не вдалося прочитати файл: {exc}"

    with st.spinner("Читання файлів…"):
        lines_a, error_a = read_uploaded(uploaded_a, data_a)
        lines_b, error_b = read_uploaded(uploaded_b, data_b)
    prepared_preview_a = cached_prepare_compare(lines_to_tuple(lines_a)) if lines_a else None
    prepared_preview_b = cached_prepare_compare(lines_to_tuple(lines_b)) if lines_b else None

    def excluded_description(prepared):
        details = []
        reason_labels = {"title_page": "титул", "toc": "зміст", "bibliography": "бібліографію"}
        for excluded in prepared.excluded:
            place = format_physical_pages(prepared.all_tokens, excluded.start, excluded.end)
            details.append(f"{reason_labels[excluded.reason]} ({place})")
        return ", ".join(details)

    def render_file_info(uploaded, lines, error, prepared):
        if uploaded is None:
            return
        st.caption(f"{uploaded.name} · {uploaded.name.rsplit('.', 1)[-1].upper()}")
        if error:
            st.error(f"❌ {error}")
            return
        pages = sorted({item.get("page") for item in lines if item.get("page") is not None})
        text_lines = sum(bool((item.get("line") or "").strip()) for item in lines)
        st.caption(f"Текстових рядків: {text_lines} · аркушів PDF: {len(pages) if pages else '—'}")
        if text_lines < 10:
            st.warning("Тексту дуже мало; результат може бути неповним.")
        if prepared.bibliography_warning:
            st.warning(prepared.bibliography_warning)
        if prepared.excluded:
            st.caption("Виключено з основного текстового порівняння: " + excluded_description(prepared))

    with left_column:
        render_file_info(uploaded_a, lines_a, error_a, prepared_preview_a)
    with right_column:
        render_file_info(uploaded_b, lines_b, error_b, prepared_preview_b)

    identical = data_a is not None and data_b is not None and hash_a == hash_b
    if identical:
        st.error("Завантажено той самий файл з обох боків (збігається SHA-256).")
    ready = bool(lines_a is not None and lines_b is not None and not identical)
    if st.button("Порівняти", type="primary", use_container_width=True, disabled=not ready):
        with st.spinner("Підготовка текстів…"):
            cached_prepare_compare(lines_to_tuple(lines_a))
            cached_prepare_compare(lines_to_tuple(lines_b))
        with st.spinner("Пошук кандидатів і вирівнювання фрагментів…"):
            st.session_state.compare_result = cached_compare_documents(
                lines_to_tuple(lines_a), lines_to_tuple(lines_b)
            )
            st.session_state.compare_visible_limit = 100

    result = st.session_state.get("compare_result")
    if result is None or lines_a is None or lines_b is None:
        return

    prepared_a = cached_prepare_compare(lines_to_tuple(lines_a))
    prepared_b = cached_prepare_compare(lines_to_tuple(lines_b))
    if not result.analysis_complete:
        st.warning(
            f"⚠ Аналіз обмежено: оброблено {result.candidates_processed} із "
            f"{result.candidates_total} областей-кандидатів. Наведені відсотки — "
            "нижня оцінка, реальне покриття може бути більшим."
        )

    accepted = [segment for segment in result.segments if segment.status != "normative_only"]
    normative_only_count = sum(segment.status == "normative_only" for segment in result.segments)
    coverage_a = result.covered_tokens_a / result.analyzed_tokens_a if result.analyzed_tokens_a else 0.0
    coverage_b = result.covered_tokens_b / result.analyzed_tokens_b if result.analyzed_tokens_b else 0.0
    strict_a = result.covered_tokens_a_strict / result.analyzed_tokens_a if result.analyzed_tokens_a else 0.0
    strict_b = result.covered_tokens_b_strict / result.analyzed_tokens_b if result.analyzed_tokens_b else 0.0
    metric_hits, metric_a, metric_b = st.columns(3)
    metric_hits.metric("Знайдені фрагменти", len(accepted))
    metric_a.metric(
        "Покриття дисертації", f"{coverage_a:.1%}",
        f"{strict_a:.1%} без нормативних", delta_color="off",
    )
    metric_b.metric(
        "Покриття джерела", f"{coverage_b:.1%}",
        f"{strict_b:.1%} без нормативних", delta_color="off",
    )

    st.markdown("#### Окреме порівняння списків літератури")
    biblio = result.biblio
    if biblio is None or not (biblio.parsed_a and biblio.parsed_b):
        st.warning("Список літератури не розпізнано — порівняння списків недоступне.")
    else:
        common = biblio.common_exact + biblio.common_near
        order_text = (
            f" · серії спільного порядку: {sum(biblio.order_runs)}"
            if biblio.order_signal_applicable else " · порядок не оцінюється: обидва списки алфавітні"
        )
        st.info(
            f"{biblio.entries_a} і {biblio.entries_b} записів · спільних джерел: {common} "
            f"(точних {biblio.common_exact}, близьких {biblio.common_near}){order_text}"
        )

    st.markdown("#### Текстові збіги")
    st.markdown("**🟡 збігається · 🩵 відрізняється** · покриття рахується за точними збігами")
    filter_column, sort_column = st.columns(2)
    with filter_column:
        type_filter = st.selectbox("Тип", ["усі", "дослівний", "змінений"], key="compare_type_filter")
    with sort_column:
        sort_mode = st.selectbox("Сортування", ["за місцем", "за схожістю"], key="compare_sort")
    show_normative = st.checkbox(
        f"Показати ймовірно нормативні збіги ({normative_only_count})",
        value=False,
        key="compare_show_normative",
    )
    visible = [
        segment for segment in result.segments
        if (segment.status != "normative_only" or show_normative)
        and (type_filter == "усі" or (type_filter == "дослівний") == (segment.kind == "verbatim"))
    ]
    if sort_mode == "за схожістю":
        visible.sort(key=lambda segment: segment.similarity, reverse=True)
    else:
        visible.sort(key=lambda segment: (segment.a_start, segment.status == "accepted_normative"))
    limit = st.session_state.get("compare_visible_limit", 100)
    st.markdown(
        render_comparison_table(
            visible[:limit], lines_a, prepared_a.tokens, lines_b, prepared_b.tokens
        ),
        unsafe_allow_html=True,
    )
    if len(visible) > limit and st.button("Показати ще", key="compare_show_more"):
        st.session_state.compare_visible_limit = limit + 100
        st.rerun()


def render_manual_search_page() -> None:
    """
    Окремий екран ручного пошуку джерел (PLAN_SEARCH.md).
    Крок 1: маршрут і завантаження файлу. Розмітка розділів, канали
    сигналів і побудова запитів приєднуються наступними кроками плану.
    """
    if st.button("← Повернутися до перевірки джерел", key="search_back"):
        if "mode" in st.query_params:
            del st.query_params["mode"]
        st.rerun()

    st.title("Пошук джерел вручну")
    st.caption(
        "Готує запити для ручного пошуку в зовнішніх системах. Не визначає "
        "плагіат і не обходить видачу автоматично — шукає й оцінює результат людина."
    )

    uploaded = st.file_uploader(
        "Дисертація (PDF із текстовим шаром)",
        type=["pdf"],
        key="search_upload",
        help="Лише PDF, до 30 МБ. DOCX у цьому режимі не підтримується.",
    )
    if uploaded is None:
        return

    data = uploaded.getvalue()
    error = validate_search_upload(uploaded.name, len(data))
    if error:
        st.error(f"❌ {error}")
        return

    st.caption(f"{uploaded.name} · {len(data) / (1024 * 1024):.1f} МБ · SHA-256 {file_sha256(data)[:12]}…")
    st.info(
        "Розбір розділів, канали сигналів A/N/B/K/T/L і побудова запитів ще не "
        "реалізовані — режим у розробці за PLAN_SEARCH.md."
    )


if is_compare_mode(st.query_params):
    render_two_file_compare_page()
    st.stop()

if is_search_mode(st.query_params):
    render_manual_search_page()
    st.stop()

header_main, header_compare, header_search = st.columns([3, 1, 1])
with header_main:
    st.title("📚 Перевірка джерел дисертації")
with header_compare:
    st.markdown(" ")
    if st.button("Порівняти дві роботи →", key="open_compare", use_container_width=True):
        st.query_params["mode"] = "compare"
        st.rerun()
with header_search:
    st.markdown(" ")
    if st.button("Пошук джерел вручну →", key="open_search", use_container_width=True):
        st.query_params["mode"] = "search"
        st.rerun()
st.caption(
    "Автоматичне виявлення невикористаних бібліографічних джерел у тексті дисертації."
)
st.divider()

# ---------------------------------------------------------------------------
# Блок 1 — Завантаження файлу
# ---------------------------------------------------------------------------

uploaded = st.file_uploader(
    "Оберіть файл дисертації (.pdf або .docx)",
    type=["pdf", "docx"],
    help="Максимальний розмір файлу: 30 МБ",
)

if not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
filename = uploaded.name

# Завантажили інший файл — скидаємо результати попереднього, інакше кнопка
# завантаження віддасть підсвічений PDF від старого файлу під новим іменем,
# а «Абзаци без посилань» покажуть цифри від старого документа.
reset_file_scoped_state(
    st.session_state,
    make_file_key(filename, len(file_bytes), getattr(uploaded, "file_id", None)),
)

try:
    lines = cached_extract(file_bytes, filename)
except FileTooLargeError as e:
    st.error(f"❌ {e}")
    st.stop()
except ScannedPDFError as e:
    st.error(f"❌ {e}")
    st.stop()
except UnsupportedFormatError as e:
    st.error(f"❌ {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ Не вдалося прочитати файл: {e}")
    st.stop()

st.toast(f"Файл завантажено: {filename}", icon="✅")

auto_author = extract_dissertation_author(lines)
auto_year = extract_dissertation_year(lines)

col_author, col_year = st.columns([3, 2])
with col_author:
    if auto_author:
        st.markdown(f"**👤 {auto_author}**")
    else:
        with st.expander("👤 Вказати автора вручну", expanded=False):
            manual_author = st.text_input(
                "ПІБ автора",
                placeholder="Прізвище Ім'я По-батькові",
                label_visibility="collapsed",
            )
            auto_author = manual_author.strip() or None

# Рік дисертації — обов'язковий супутник перевірки на анахронізми: якщо
# автовизначення помилилось або повернуло None, увесь блок або мовчить,
# або бреше. Тому показуємо, що саме визначила програма, і даємо виправити:
# введене значення перекриває автоматичне.
with col_year:
    with st.expander(f"📅 Рік дисертації: {auto_year or '—'}", expanded=not auto_year):
        st.caption(
            "Від цього року залежить перевірка «джерело новіше за дисертацію». "
            "Якщо визначено неправильно — виправте."
        )
        manual_year = st.number_input(
            "Рік дисертації",
            min_value=1980,
            max_value=datetime.datetime.now().year + 1,
            value=auto_year,
            step=1,
            format="%d",
            label_visibility="collapsed",
        )

dissertation_year = int(manual_year) if manual_year else None

st.divider()

zone_result = None
auto_error = None

try:
    zone_result = cached_split_zones(lines_to_tuple(lines))
except BibliographyNotFoundError as e:
    auto_error = str(e)
except Exception as e:
    st.error(f"❌ Помилка при аналізі структури: {e}")
    st.stop()

if zone_result is None:
    st.warning(f"⚠️ {auto_error}")
    st.subheader("Вкажіть розташування списку літератури вручну")

    col1, col2 = st.columns([3, 1])
    with col1:
        manual_header = st.text_input(
            "Назва розділу (рядок пошуку)",
            placeholder="наприклад: СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ",
        )
    with col2:
        is_pdf = filename.lower().endswith(".pdf")
        if is_pdf:
            manual_page = int(st.number_input(
                "Починаючи зі сторінки №",
                min_value=1, value=1, step=1,
            ))
        else:
            st.markdown(" ")
            st.caption("Сторінки недоступні для DOCX")
            manual_page = None

    if not manual_header.strip():
        st.info("💡 Введіть назву розділу бібліографії так, як вона написана у файлі.")
        st.stop()

    try:
        zone_result = split_zones_manual(lines, manual_header, manual_page)
    except BibliographyNotFoundError as e:
        st.error(f"❌ {e}")
        st.stop()
    except Exception as e:
        st.error(f"❌ Помилка при аналізі структури: {e}")
        st.stop()

if zone_result is not None:
    tab1, tab2 = st.tabs(["📋 Перевірка джерел", "🖍 Асистент антиплагіату"])

    with tab1:
        render_tab_checker(zone_result, dissertation_year=dissertation_year)

    with tab2:
        render_tab_highlighter(file_bytes, filename, zone_result, lines)
