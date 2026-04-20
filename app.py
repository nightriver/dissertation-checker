"""
app.py — Перевірка джерел дисертації
Streamlit Community Cloud entry point.
"""

import streamlit as st
import pandas as pd

from parser.extractor import (
    extract_lines,
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


# ---------------------------------------------------------------------------
# Конфігурація сторінки
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Перевірка джерел дисертації",
    page_icon="📚",
    layout="centered",
)

st.title("📚 Перевірка джерел дисертації")
st.caption(
    "Автоматичне виявлення невикористаних бібліографічних джерел у тексті дисертації."
)

st.divider()


# ---------------------------------------------------------------------------
# Допоміжна функція: форматування списку сторінок у діапазони
# ---------------------------------------------------------------------------

def format_page_ranges(pages: list[int]) -> str:
    """
    Перетворює список номерів сторінок у компактний рядок з діапазонами.
    Приклад: [3, 4, 5, 9, 12, 13] → "3–5, 9, 12–13"
    """
    if not pages:
        return ""
    pages = sorted(pages)
    ranges: list[str] = []
    start = end = pages[0]
    for p in pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append(f"{start}–{end}" if end > start else str(start))
            start = end = p
    ranges.append(f"{start}–{end}" if end > start else str(start))
    return ", ".join(ranges)


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


# ---------------------------------------------------------------------------
# Витяг тексту
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Читання файлу…")
def cached_extract(data: bytes, fname: str):
    return extract_lines(data, fname)


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

st.success(
    f"✅ Файл завантажено: **{filename}** "
    f"({len(file_bytes) / 1024 / 1024:.1f} МБ, {len(lines)} рядків)"
)


# ---------------------------------------------------------------------------
# Блок 2 — Автоматичний пошук бібліографії
# ---------------------------------------------------------------------------

st.divider()

zone_result = None
auto_error: str | None = None

try:
    zone_result = split_zones(lines)
except BibliographyNotFoundError as e:
    auto_error = str(e)
except Exception as e:
    st.error(f"❌ Помилка при аналізі структури: {e}")
    st.stop()

if zone_result is not None:
    page_info = (
        f" (стор. {zone_result.biblio_start_page})"
        if zone_result.biblio_start_page
        else ""
    )
    st.info(
        f"✅ Список літератури знайдено автоматично: "
        f"**«{zone_result.biblio_header_line}»**{page_info}"
    )

else:
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

    page_info = (
        f" (стор. {zone_result.biblio_start_page})"
        if zone_result.biblio_start_page
        else ""
    )
    st.info(
        f"✅ Список літератури знайдено вручну: "
        f"**«{zone_result.biblio_header_line}»**{page_info}"
    )


# ---------------------------------------------------------------------------
# Блок: Асистент антиплагіату
# ---------------------------------------------------------------------------

st.divider()
st.subheader("🖍 Асистент антиплагіату")
st.caption(
    "Створює копію PDF з підсвіченими посиланнями [номер, с. XX] "
    "для швидкого ручного маркування у сервісі перевірки плагіату."
)

if filename.lower().endswith(".pdf"):

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
            except Exception as e:
                st.error(f"❌ Помилка при генерації PDF: {e}")

    if st.session_state.highlighted_pdf:
        st.download_button(
            label="📥 Завантажити PDF з підсвіченими посиланнями",
            data=st.session_state.highlighted_pdf,
            file_name=f"{filename.rsplit('.', 1)[0]}_highlighted.pdf",
            mime="application/pdf",
            type="primary",
        )

        # --- Сторінки без посилань ---
        empty_pages: list[int] = st.session_state.empty_pages
        tracked: int = st.session_state.tracked_pages_count

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

else:
    st.info("Підсвітка посилань доступна тільки для PDF файлів.")


# ---------------------------------------------------------------------------
# Блок 3 — Кнопка запуску
# ---------------------------------------------------------------------------

st.divider()
run = st.button("🔍 Перевірити джерела", type="primary", use_container_width=True)

if not run:
    st.stop()


# ---------------------------------------------------------------------------
# Парсинг бібліографії
# ---------------------------------------------------------------------------

with st.spinner("Парсинг джерел…"):
    bibliography = parse_bibliography(zone_result.bibliography)

if not bibliography:
    st.error(
        "❌ У знайденому розділі не виявлено пронумерованих джерел. "
        "Переконайтеся, що записи мають формат «1. Автор…» або «[1] Автор…»."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Пошук посилань у тексті
# ---------------------------------------------------------------------------

with st.spinner("Пошук посилань у тексті…"):
    citations = find_citations(zone_result.body)

if not citations:
    st.warning(
        "⚠️ Посилань у тексті не знайдено. "
        "Переконайтеся, що посилання мають формат [1], [1; 3], [15–18] тощо."
    )


# ---------------------------------------------------------------------------
# Порівняння
# ---------------------------------------------------------------------------

result = compare(bibliography, citations)
citations_dict: dict = citations
orphans_sorted = sorted(result["orphans"])
used_sorted = sorted(result["used"])

total = len(result["all_sources"])
used_count = len(result["used"])
orphan_count = len(result["orphans"])
used_pct = used_count / total * 100 if total else 0
orphan_pct = orphan_count / total * 100 if total else 0


# ---------------------------------------------------------------------------
# Блок 4 — Результати
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Результати перевірки")

col1, col2, col3 = st.columns(3)
col1.metric("Всього джерел у списку", total)
col2.metric("Використано в тексті", used_count, f"{used_pct:.1f}%")
col3.metric(
    "Не використано (сироти)",
    orphan_count,
    f"{orphan_pct:.1f}%",
    delta_color="inverse",
)

st.divider()

if orphan_count == 0:
    st.success("🎉 Усі джерела зі списку літератури використані в тексті!")
    st.stop()

orphans_str = ", ".join(str(n) for n in orphans_sorted)
st.markdown("**Номери невикористаних джерел:**")
st.code(orphans_str, language=None)

st.markdown("**Перелік невикористаних джерел:**")

orphan_rows = [
    {"№": num, "Джерело": bibliography.get(num, "—")}
    for num in orphans_sorted
]
st.dataframe(
    pd.DataFrame(orphan_rows),
    use_container_width=True,
    hide_index=True,
    column_config={
        "№": st.column_config.NumberColumn(width="small"),
        "Джерело": st.column_config.TextColumn(width="large"),
    },
)

with st.expander(f"Використані джерела ({used_count})"):
    used_rows = [
        {
            "№": num,
            "Джерело": bibliography.get(num, "—"),
            "Скобка в тексті": citations_dict.get(num, "—"),
        }
        for num in used_sorted
    ]
    st.dataframe(
        pd.DataFrame(used_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "№": st.column_config.NumberColumn(width="small"),
            "Джерело": st.column_config.TextColumn(width="large"),
            "Скобка в тексті": st.column_config.TextColumn(width="medium"),
        },
    )

phantom_count = len(result["phantom"])
if phantom_count:
    with st.expander(f"⚠️ Посилання без відповідного запису в списку ({phantom_count})"):
        st.caption(
            "Ці номери зустрічаються в тексті, але відсутні в списку літератури. "
            "Можлива помилка нумерації."
        )
        st.write(", ".join(str(n) for n in sorted(result["phantom"])))
