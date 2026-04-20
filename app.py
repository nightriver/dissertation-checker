"""
app.py — Перевірка джерел дисертації
Streamlit Community Cloud entry point.
UI v3: Toast, Tabs, Auto-run, Author extraction.
"""

import statistics

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
from parser.year_extractor import extract_years
from parser.dstu_validator import validate_bibliography, DstuStatus


# ---------------------------------------------------------------------------
# Конфігурація сторінки
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Перевірка джерел дисертації",
    page_icon="📚",
    layout="centered",
)


# ---------------------------------------------------------------------------
# Допоміжні функції (визначені ДО використання)
# ---------------------------------------------------------------------------

def format_page_ranges(pages: list) -> str:
    """Перетворює список номерів сторінок у компактний рядок з діапазонами."""
    if not pages:
        return ""
    pages = sorted(int(p) for p in pages)  # cast to int — values may arrive as strings
    ranges = []
    start = end = pages[0]
    for p in pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}–{end}" if end > start else str(start))
            start = end = p
    ranges.append(f"{start}–{end}" if end > start else str(start))
    return ", ".join(ranges)


@st.cache_data(show_spinner="Читання файлу…")
def cached_extract(data: bytes, fname: str):
    return extract_lines(data, fname)


@st.cache_data(show_spinner="Аналіз джерел…")
def cached_analyze(file_bytes: bytes, filename: str, biblio_header: str):
    zone = st.session_state["zone_result"]
    bibliography = parse_bibliography(zone.bibliography)
    citations = find_citations(zone.body)
    result = compare(bibliography, citations)
    return bibliography, citations, result


# ---------------------------------------------------------------------------
# render_tab_checker — Перевірка джерел
# ---------------------------------------------------------------------------

def render_tab_checker(zone_result, file_bytes: bytes, filename: str) -> None:
    bibliography, citations, result = cached_analyze(
        file_bytes, filename, zone_result.biblio_header_line
    )

    if not bibliography:
        st.error(
            "❌ У знайденому розділі не виявлено пронумерованих джерел. "
            "Переконайтеся, що записи мають формат «1. Автор…» або «[1] Автор…»."
        )
        return

    citations_dict = citations
    orphans_sorted = sorted(result["orphans"])
    used_sorted = sorted(result["used"])

    total = len(result["all_sources"])
    used_count = len(result["used"])
    orphan_count = len(result["orphans"])
    used_pct = used_count / total * 100 if total else 0
    orphan_pct = orphan_count / total * 100 if total else 0

    st.divider()
    m1, m2, m3 = st.columns(3)
    m1.metric("Джерел у списку", total)
    m2.metric(
        "Використовуються у тексті",
        used_count,
        delta=f"{used_pct:.0f}%",
        delta_color="normal",
    )
    m3.metric(
        "Не згадуються у тексті",
        orphan_count,
        delta=f"-{orphan_pct:.0f}%" if orphan_count else None,
        delta_color="inverse",
    )

    if orphans_sorted:
        st.divider()
        st.markdown("#### ⚠️ Джерела, не згадані у тексті")
        orphan_rows = [
            {"№": num, "Запис": bibliography.get(num, "—")}
            for num in orphans_sorted
        ]
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
                    # citations_dict values are bracket strings, e.g. "[89, с. 11; 98]"
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
        phantom_rows = [
            {"№": num, "Посилання у тексті": citations_dict.get(num, "") or "—"}
            for num in phantom
        ]
        st.dataframe(pd.DataFrame(phantom_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### 📊 Розподіл джерел за роками видання")

    all_years = []
    for raw_text in bibliography.values():
        all_years.extend(extract_years(raw_text))

    if all_years:
        year_counts = {}
        for y in all_years:
            year_counts[y] = year_counts.get(y, 0) + 1

        years_sorted = sorted(year_counts.keys())
        counts = [year_counts[y] for y in years_sorted]

        fig = go.Figure(go.Bar(x=years_sorted, y=counts, marker_color="#4f98a3"))
        fig.update_layout(
            xaxis_title="Рік",
            yaxis_title="Кількість джерел",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

        if len(all_years) >= 2:
            c1, c2, c3 = st.columns(3)
            c1.metric("Найстаріше джерело", min(all_years))
            c2.metric("Медіана", int(statistics.median(all_years)))
            c3.metric("Найновіше джерело", max(all_years))
    else:
        st.info("Роки видання у джерелах не виявлено.")

    st.divider()
    st.markdown("#### 📐 Перевірка ДСТУ 8302:2015")

    dstu_results = validate_bibliography(bibliography)
    ok_count = sum(1 for r in dstu_results if r.status == DstuStatus.OK)
    warn_count = sum(1 for r in dstu_results if r.status == DstuStatus.WARNING)
    error_count = sum(1 for r in dstu_results if r.status == DstuStatus.ERROR)

    dc1, dc2, dc3 = st.columns(3)
    dc1.metric("✅ Відповідають", ok_count)
    dc2.metric("⚠️ Зауваження", warn_count)
    dc3.metric("❌ Порушення", error_count)

    errors_and_warnings = [
        r for r in dstu_results if r.status in (DstuStatus.ERROR, DstuStatus.WARNING)
    ]

    if errors_and_warnings:
        with st.expander(
            f"Показати зауваження та порушення ({len(errors_and_warnings)})",
            expanded=False,
        ):
            dstu_rows = [
                {
                    "№": r.source_num,
                    "Статус": "❌" if r.status == DstuStatus.ERROR else "⚠️",
                    "Повідомлення": r.message,
                }
                for r in errors_and_warnings
            ]
            st.dataframe(pd.DataFrame(dstu_rows), use_container_width=True, hide_index=True)
    else:
        st.success("🎉 Усі джерела відповідають вимогам ДСТУ 8302:2015!")


# ---------------------------------------------------------------------------
# render_tab_highlighter — Асистент антиплагіату
# ---------------------------------------------------------------------------

def render_tab_highlighter(file_bytes: bytes, filename: str, zone_result) -> None:
    st.caption(
        "Створює копію PDF з підсвіченими посиланнями [номер, с. XX] "
        "для швидкого ручного маркування у сервісі перевірки плагіату."
    )

    if not filename.lower().endswith(".pdf"):
        st.info("Підсвітка посилань доступна тільки для PDF файлів.")
        return

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
            st.code(format_page_ranges(empty_pages), language=None)


# ===========================================================================
# ГОЛОВНИЙ ПОТІК СТОРІНКИ
# ===========================================================================

st.title("📚 Перевірка джерел дисертації")
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

file_bytes = uploaded.read()
filename = uploaded.name

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

# ---------------------------------------------------------------------------
# Шапка: автор + рік
# ---------------------------------------------------------------------------

auto_author = extract_dissertation_author(lines)
auto_year = extract_dissertation_year(lines)

col_author, col_year = st.columns([4, 1])
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
with col_year:
    if auto_year:
        st.markdown(f"**📅 {auto_year} р.**")

# ---------------------------------------------------------------------------
# Блок 2 — Пошук бібліографії
# ---------------------------------------------------------------------------

st.divider()

zone_result = None
auto_error = None

try:
    zone_result = split_zones(lines)
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

# ---------------------------------------------------------------------------
# Зберегти zone_result в session_state ПЕРЕД cached_analyze
# ---------------------------------------------------------------------------

if zone_result is not None:
    st.session_state["zone_result"] = zone_result

# ---------------------------------------------------------------------------
# Вкладки — тільки якщо бібліографія знайдена
# ---------------------------------------------------------------------------

if zone_result is not None:
    tab1, tab2 = st.tabs(["📋 Перевірка джерел", "🖍 Асистент антиплагіату"])

    with tab1:
        render_tab_checker(zone_result, file_bytes, filename)

    with tab2:
        render_tab_highlighter(file_bytes, filename, zone_result)
