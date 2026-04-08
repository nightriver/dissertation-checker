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
# Якщо не знайдено — показуємо блок ручного налаштування і зупиняємось.
# ---------------------------------------------------------------------------

st.divider()

# Спроба автоматичного пошуку (без кнопки — одразу після завантаження)
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
    # Автоматично знайдено — показуємо тихе підтвердження
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
    # Автоматично не знайдено — показуємо блок ручного вводу
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

    # Пробуємо ручний пошук
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
citations_dict: dict = citations  # dict[int, str] — first bracket per source
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

# Рядок для копіювання
orphans_str = ", ".join(str(n) for n in orphans_sorted)
st.markdown("**Номери невикористаних джерел:**")
st.code(orphans_str, language=None)

# Таблиця сиріт
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

# Розгорнута секція: використані джерела (для перевірки)
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

# Phantom — посилання без відповідного запису в списку (інформаційно)
phantom_count = len(result["phantom"])
if phantom_count:
    with st.expander(f"⚠️ Посилання без відповідного запису в списку ({phantom_count})"):
        st.caption(
            "Ці номери зустрічаються в тексті, але відсутні в списку літератури. "
            "Можлива помилка нумерації."
        )
        st.write(", ".join(str(n) for n in sorted(result["phantom"])))
