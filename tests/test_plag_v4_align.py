"""Тести прив'язки шматків звіту до тексту дисертації — PLAN_PLAG_FILTER_V4.md, §6.2, §7 етап 2."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from plag_filter.rules import extract_author
from plag_filter.v4.align import DissText, Hit, load_dissertation, locate_entry, normalize
from plag_filter.v4.report import PlagEntry, PlagFragment, parse_v4_report

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "examples" / "plag" / "new plag"
SOURCE = NEW / "source"
PAIRS = {
    "R1801": (NEW / "plag-report-dis-Чванкін-R001801.pdf", SOURCE / "dis Чванкін.pdf"),
    "R1904": (NEW / "plag-report-докт-R001904.pdf", SOURCE / "Чванкін докт.pdf"),
    "R1928": (NEW / "plag-report-канд-R001928.pdf", SOURCE / "Білоус канд.pdf"),
}

# Ціль прив'язки — PLAN_PLAG_FILTER_V4.md, §5.
MIN_LOCATED_SHARE = 0.995
MIN_TRANSLATED_SENTENCES = 7


def diss_from_text(text: str) -> DissText:
    """Синтетичний `DissText` з рядка: один аркуш, нульові прямокутники."""
    stream_chars: list[str] = []
    word_start: list[bool] = []
    previous_alnum = False
    for char in text:
        normalized = normalize(char)
        if normalized:
            stream_chars.append(normalized)
            word_start.append(not previous_alnum)
            previous_alnum = True
        else:
            previous_alnum = False
    stream = "".join(stream_chars)
    return DissText(
        stream=stream,
        char_page=(0,) * len(stream),
        char_bbox=((0.0, 0.0, 0.0, 0.0),) * len(stream),
        word_start=tuple(word_start),
        page_count=1,
        title_text="",
    )


def entry(*fragments: PlagFragment) -> PlagEntry:
    return PlagEntry(
        section="main",
        number=1,
        domain="example.org",
        url="http://example.org/x",
        words=10,
        percent=1.0,
        sentences=None,
        max_percent=None,
        fragments=tuple(fragments),
        source_texts=(),
        report_page=0,
    )


def span_text(diss: DissText, hit: Hit) -> str:
    return diss.stream[hit.start : hit.end]


# --- normalize ---------------------------------------------------------------


def test_normalize_drops_punctuation_and_lowers():
    assert normalize("Ст. 189 КПК України).") == "ст189кпкукраїни"


def test_normalize_ligature_apostrophe_dash():
    assert normalize("ﬁle") == "file"
    assert normalize("п'ять – м’ята") == "пятьмята"
    assert normalize("розвит-\nку") == "розвитку"


def test_normalize_composes_combining_marks():
    decomposed = "й"  # «й» з комбінованого знака
    assert normalize(decomposed) == "й"


# --- locate_entry на синтетиці ------------------------------------------------


def test_unique_long_run():
    diss = diss_from_text("Початок. Унікальне довге речення про процесуальне представництво. Кінець.")
    fragment = PlagFragment(text="зовсім інший контекст", runs=("Унікальне довге речення про процесуальне",))
    hits, missing = locate_entry(entry(fragment), diss)
    assert missing == 0
    assert len(hits) == 1
    assert hits[0].how == "unique"
    assert span_text(diss, hits[0]) == normalize("Унікальне довге речення про процесуальне")


def test_repeated_run_resolved_by_fragment_window():
    text = (
        "Перший абзац: суд розглядає справу у відкритому засіданні. "
        "Другий абзац інший: суд розглядає справу у відкритому засіданні згідно з кодексом."
    )
    diss = diss_from_text(text)
    run = "суд розглядає справу у відкритому засіданні"
    fragment = PlagFragment(text="Другий абзац інший: " + run + " згідно з кодексом", runs=(run,))
    hits, missing = locate_entry(entry(fragment), diss)
    assert missing == 0
    assert hits[0].how == "fragment"
    second = diss.stream.find(normalize(run), diss.stream.find(normalize(run)) + 1)
    assert hits[0].start == second


def test_short_run_without_window_takes_nearest_to_previous():
    text = "аааа bbbb ст. 189 КПК перше. " + "x" * 50 + " Довгий унікальний шматок тексту тут, ст. 189 КПК друге."
    diss = diss_from_text(text)
    fragment = PlagFragment(
        text="не знайдеться",
        runs=("Довгий унікальний шматок тексту тут", "ст. 189 КПК"),
    )
    hits, missing = locate_entry(entry(fragment), diss)
    assert missing == 0
    assert [hit.how for hit in hits] == ["unique", "nearest"]
    occurrences = [i for i in range(len(diss.stream)) if diss.stream.startswith("ст189кпк", i)]
    assert hits[1].start == occurrences[-1]


def test_first_occurrence_when_nothing_found_yet():
    diss = diss_from_text("ст. 189 КПК раз. ст. 189 КПК два.")
    fragment = PlagFragment(text="немає", runs=("ст. 189 КПК",))
    hits, missing = locate_entry(entry(fragment), diss)
    assert missing == 0
    assert hits[0].how == "first"
    assert hits[0].start == diss.stream.find("ст189кпк")


def test_missing_run_counted():
    diss = diss_from_text("Зовсім інший текст дисертації.")
    fragment = PlagFragment(text="немає", runs=("Цього речення в дисертації немає зовсім", "...!"))
    hits, missing = locate_entry(entry(fragment), diss)
    assert hits == []
    assert missing == 1  # порожній після нормалізації шматок не рахується


def test_hit_end_matches_run_length():
    diss = diss_from_text("Текст з унікальним реченням для перевірки довжини.")
    run = "унікальним реченням для перевірки"
    hits, _ = locate_entry(entry(PlagFragment(text="x", runs=(run,))), diss)
    assert hits[0].end - hits[0].start == len(normalize(run))


# --- load_dissertation --------------------------------------------------------


def test_load_dissertation_stream_pages_and_word_starts():
    doc = fitz.open()
    first = doc.new_page()
    first.insert_text((72, 100), "Hello, World")
    second = doc.new_page()
    second.insert_text((72, 100), "ab-cd ef")
    diss = load_dissertation(doc.tobytes())
    assert diss.stream == "helloworldabcdef"
    assert diss.page_count == 2
    assert diss.char_page == (0,) * 10 + (1,) * 6
    assert len(diss.char_bbox) == len(diss.stream)
    starts = [diss.stream[i] for i, start in enumerate(diss.word_start) if start]
    assert starts == ["h", "w", "a", "c", "e"]
    x0, y0, x1, y1 = diss.char_bbox[0]
    assert x0 == pytest.approx(72, abs=1) and x1 > x0 and y1 > y0
    assert "Hello, World" in diss.title_text


# --- корпус -------------------------------------------------------------------


@pytest.fixture(scope="module", params=list(PAIRS), ids=list(PAIRS))
def pair(request):
    report_path, diss_path = PAIRS[request.param]
    return parse_v4_report(report_path.read_bytes()), load_dissertation(diss_path.read_bytes())


@pytest.mark.corpus
def test_corpus_located_share(pair):
    report, diss = pair
    total = located = 0
    for item in report.entries:
        if item.section == "translated" and (item.sentences or 0) < MIN_TRANSLATED_SENTENCES:
            continue
        hits, missing = locate_entry(item, diss)
        located += len(hits)
        total += len(hits) + missing
        for hit in hits:
            assert 0 <= hit.start < hit.end <= len(diss.stream)
    assert total > 0
    assert located / total >= MIN_LOCATED_SHARE


@pytest.mark.corpus
def test_corpus_title_gives_author(pair):
    _report, diss = pair
    assert extract_author(diss.title_text) is not None
