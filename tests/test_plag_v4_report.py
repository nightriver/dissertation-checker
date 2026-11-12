"""Тести розбору звіту Plag нового формату — PLAN_PLAG_FILTER_V4.md, §6.1, §7 етап 1."""

from __future__ import annotations

import re
from pathlib import Path

import fitz
import pytest

from plag_filter.pdf import UnsupportedReportError
from plag_filter.v4.report import (
    Line,
    Span,
    detect_report_kind,
    extract_lines,
    parse_lines,
    parse_v4_report,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
NEW = EXAMPLES / "new plag"
R1801 = NEW / "plag-report-dis-Чванкін-R001801.pdf"
R1904 = NEW / "plag-report-докт-R001904.pdf"
R1928 = NEW / "plag-report-канд-R001928.pdf"

# Числа — PLAN_PLAG_FILTER_V4.md, §3.
EXPECTED = {
    R1801: {"main": 473, "total_words": 68781},
    R1904: {"main": 158, "total_words": 122841},
    R1928: {"main": 596, "total_words": 67260},
}

TRANSLATED_HEADER = re.compile(r"^\d+\. \S+ · \d+ речень · max \d+%$")


# --- допоміжне: ручні рядки ------------------------------------------------


def S(text: str, bold: bool = False, x0: float = 60.0, size: float = 9.5) -> Span:
    return Span(text=text, bold=bold, x0=x0, size=size)


def L(*spans: Span | str, page: int = 0, x0: float | None = None) -> Line:
    parts = tuple(S(s) if isinstance(s, str) else s for s in spans)
    text = re.sub(r"\s+", " ", "".join(p.text for p in parts)).strip()
    return Line(page=page, text=text, x0=parts[0].x0 if x0 is None else x0, spans=parts)


def heading(text: str, page: int = 0) -> Line:
    return L(S(text, bold=True, x0=51, size=12.5), page=page)


def entry_header(text: str, page: int = 0) -> Line:
    return L(S(text, bold=True, x0=51, size=10.0), page=page)


def meta_lines() -> list[Line]:
    return [
        L(S("Звіт про перевірку роботи", bold=True, x0=53, size=19)),
        L(S("Plag · "), S("Номер звіту "), S("R-000123", bold=True), S(" (v1) · Видано 2026-11-12 22:43")),
        heading("1. Робота"),
        L(S("Файл", x0=53)),
        L(S("Вигаданко канд", bold=True, x0=160), S(".pdf", bold=True, x0=200)),
        L(S("Автор", x0=53)),
        L(S("–", x0=160)),
        L(S("Слів", x0=53)),
        L(S("1000", x0=160)),
        L(S("Завантажено", x0=53)),
        L(S("2026-11-12 20:23", x0=160)),
        heading("2. Показники"),
        L(S("Збіги з іншими", bold=True, x0=57, size=8)),
        L(S("джерелами", bold=True, x0=57, size=8)),
        L(S("Мовні", bold=True, x0=479, size=8)),
        L(S("помилки", bold=True, x0=479, size=8)),
        L(S("97%", bold=True, x0=57, size=15)),
        L(S("Унікальність: 3%", x0=57, size=8)),
        L(S("0", bold=True, x0=479, size=15)),
        L(S("Показники є автоматичними допоміжними засобами для викладача.", x0=51, size=8)),
    ]


def main_entry(number: int, domain: str = "example.org", page: int = 0) -> list[Line]:
    return [
        entry_header(f"{number}. {domain}", page=page),
        L(S("http://example.org/a/very/long/", x0=51, size=8), page=page),
        L(S("path.pdf?x=1", x0=51, size=8), page=page),
        L(
            S("Слів, що збігаються:  ", x0=51, size=8),
            S("12", bold=True, size=8),
            S(" (1.6% від обсягу роботи)", size=8),
            page=page,
        ),
        L(S("Фрагменти в роботі", bold=True, x0=51, size=8), page=page),
        L(S("… перший шматок "), S("жирний один", bold=True), S(" далі"), page=page),
        L(S("звичайний "), S("жирний два", bold=True), page=page),
        L(S("продовження", bold=True), S(" хвіст …"), page=page),
        L(S("… другий фрагмент "), S("жирний три", bold=True), S(" …"), page=page),
        L(S("Текст джерела", bold=True, x0=51, size=8), page=page),
        L(S("джерело перше "), S("виділене", bold=True), page=page),
        L(S("джерело друге …"), page=page),
    ]


def short_block() -> list[Line]:
    return [
        L(S("Інші джерела (короткі збіги)", bold=True, x0=51, size=10)),
        L(S("Короткі збіги, які зазвичай є випадковими. Наведено для повноти.", x0=51, size=8)),
        L(S("Джерело", bold=True, x0=56, size=7)),
        L(S("Фрагменти в роботі", bold=True, x0=235, size=7)),
        L(S("Слів", bold=True, x0=506, size=7)),
        L(S("short.example", x0=56, size=7)),
        L(S("https://short.example/abc", x0=56, size=5.7)),
        L(S("… фрагмент 12 …", x0=235, size=8.4)),
        L(S("14", x0=506, size=8.4)),
        L(S("other.example", x0=56, size=7)),
        L(S("https://other.example/x", x0=56, size=5.7)),
        L(S("3", x0=235, size=8.4)),
        L(S("2", x0=506, size=8.4)),
    ]


def translated_block() -> list[Line]:
    return [
        heading("4. Перекладений текст"),
        L(S("Речення роботи, що збігаються з текстом іншою мовою.", x0=51, size=8)),
        L(
            S("1. tr.example ", bold=True, x0=51, size=10),
            S("· 9 речень", bold=True, size=8),
            S(" · max 99%", bold=True, size=8),
        ),
        L(S("Назва документа", x0=51, size=8)),
        L(S("https://tr.example/doc/", x0=51, size=8)),
        L(S("part.pdf", x0=51, size=8)),
        L(S("Фрагменти в роботі", bold=True, x0=57, size=8)),
        L(S("Текст джерела", bold=True, x0=284, size=8)),
        L(S("%", bold=True, x0=506, size=8)),
        L(S("… ліва ", x0=57), S("перекладене речення", bold=True, x0=90)),
        L(S("кінець", bold=True, x0=57), S(" …", x0=90)),
        L(S("right source text", bold=True, x0=284)),
        L(S("97", x0=506)),
        L(S("… другий рядок ", x0=57), S("ще речення", bold=True, x0=100), S(" …", x0=150)),
        L(S("second source", x0=284)),
        L(S("90", x0=506)),
        L(S("2. tr2.example · 2 речень · max 95%", bold=True, x0=51, size=10)),
        L(S("tr2.example https://tr2.example/y", x0=51, size=8)),
        L(S("Фрагменти в роботі", bold=True, x0=57, size=8)),
        L(S("Текст джерела", bold=True, x0=284, size=8)),
        L(S("%", bold=True, x0=506, size=8)),
        L(S("… x ", x0=57), S("y", bold=True, x0=70)),
        L(S("z", x0=284)),
        L(S("95", x0=506)),
    ]


def full_lines() -> list[Line]:
    return (
        meta_lines()
        + [heading("3. Збіги з іншими джерелами")]
        + main_entry(1)
        + main_entry(2, domain="212.1.86.13", page=1)
        + short_block()
        + translated_block()
        + [heading("5. Ознаки тексту, написаного ШІ"), L(S("Текст"))]
    )


# --- parse_lines на ручних рядках -----------------------------------------


def test_parse_lines_meta_and_scores():
    report = parse_lines(full_lines())
    assert report.report_id == "R-000123"
    assert report.file_name == "Вигаданко канд.pdf"
    assert report.total_words == 1000
    assert report.uploaded == "2026-11-12 20:23"
    assert report.scores == (
        ("Збіги з іншими джерелами", "97% Унікальність: 3%"),
        ("Мовні помилки", "0"),
    )


def test_parse_lines_main_entries():
    report = parse_lines(full_lines())
    main = [e for e in report.entries if e.section == "main"]
    assert [e.number for e in main] == [1, 2]
    first, second = main
    assert first.domain == "example.org"
    assert second.domain == "212.1.86.13"
    assert first.url == "http://example.org/a/very/long/path.pdf?x=1"
    assert first.words == 12
    assert first.percent == 1.6
    assert first.sentences is None and first.max_percent is None
    assert first.report_page == 0 and second.report_page == 1


def test_parse_lines_fragments_and_runs():
    entry = parse_lines(full_lines()).entries[0]
    assert len(entry.fragments) == 2
    one, two = entry.fragments
    assert one.runs == ("жирний один", "жирний два продовження")
    assert one.text.startswith("перший шматок жирний один")
    assert one.text.endswith("продовження хвіст")
    assert "…" not in one.text
    assert two.runs == ("жирний три",)
    assert two.text == "другий фрагмент жирний три"
    assert entry.source_texts == ("джерело перше виділене джерело друге …",)


def test_parse_lines_short_matches_counted_not_entries():
    report = parse_lines(full_lines())
    assert report.short_count == 2
    domains = {e.domain for e in report.entries}
    assert "short.example" not in domains and "other.example" not in domains


def test_parse_lines_translated_entries():
    report = parse_lines(full_lines())
    translated = [e for e in report.entries if e.section == "translated"]
    assert [e.number for e in translated] == [1, 2]
    first, second = translated
    assert first.domain == "tr.example"
    assert first.url == "https://tr.example/doc/part.pdf"
    assert first.sentences == 9 and first.max_percent == 99
    assert first.words is None and first.percent is None
    assert [f.runs for f in first.fragments] == [("перекладене речення кінець",), ("ще речення",)]
    assert first.fragments[0].text == "ліва перекладене речення кінець"
    assert first.source_texts == ("right source text", "second source")
    assert second.url == "https://tr2.example/y"
    assert second.sentences == 2


def test_parse_lines_entries_order_main_then_translated():
    sections = [e.section for e in parse_lines(full_lines()).entries]
    assert sections == ["main", "main", "translated", "translated"]


def test_parse_lines_no_short_section():
    lines = meta_lines() + [heading("3. Збіги з іншими джерелами")] + main_entry(1)
    report = parse_lines(lines)
    assert report.short_count == 0
    assert len(report.entries) == 1


def test_parse_lines_bad_sequence():
    lines = meta_lines() + [heading("3. Збіги з іншими джерелами")] + main_entry(1) + main_entry(3)
    with pytest.raises(UnsupportedReportError) as exc:
        parse_lines(lines)
    assert exc.value.code == "v4_bad_sequence"


def test_parse_lines_entry_without_words():
    broken = [line for line in main_entry(1) if not line.text.startswith("Слів, що")]
    lines = meta_lines() + [heading("3. Збіги з іншими джерелами")] + broken + main_entry(2)
    with pytest.raises(UnsupportedReportError) as exc:
        parse_lines(lines)
    assert exc.value.code == "v4_bad_entry"


def test_parse_lines_no_meta():
    lines = [heading("3. Збіги з іншими джерелами")] + main_entry(1)
    with pytest.raises(UnsupportedReportError) as exc:
        parse_lines(lines)
    assert exc.value.code == "v4_no_meta"


def test_parse_lines_no_sources():
    with pytest.raises(UnsupportedReportError) as exc:
        parse_lines(meta_lines())
    assert exc.value.code == "v4_no_sources"


def test_extract_lines_drops_footer_and_normalizes():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "file   text", fontname="hebo")
    page.insert_text((50, 815), "Plag · R-000123 · v1 · 2026-11-12 22:43", fontname="helv")
    data = doc.tobytes()
    lines = extract_lines(data)
    texts = [line.text for line in lines]
    assert texts == ["file text"]
    assert lines[0].spans[0].bold is True
    assert lines[0].x0 == pytest.approx(50, abs=1)
    assert all(line.page == 0 for line in lines)


def test_extract_lines_keeps_ellipsis_and_applies_nfkc():
    """«…» — межа фрагментів: NFKC не має перетворити його на три крапки."""
    doc = fitz.open()
    page = doc.new_page()
    font = fitz.Font("cjk")
    page.insert_font(fontname="cjk", fontbuffer=font.buffer)
    page.insert_text((50, 100), "a … b ﬁ", fontname="cjk")
    lines = extract_lines(doc.tobytes())
    assert [line.text for line in lines] == ["a … b fi"]


# --- корпус ----------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize("path", [R1801, R1904, R1928], ids=["R1801", "R1904", "R1928"])
def test_detect_new_reports(path):
    assert detect_report_kind(path.read_bytes()) == "v4"


@pytest.mark.corpus
def test_detect_classic_reports():
    classic = sorted(EXAMPLES.glob("*.pdf"))
    assert classic
    for path in classic:
        assert detect_report_kind(path.read_bytes()) == "classic", path.name


@pytest.fixture(scope="module", params=[R1801, R1904, R1928], ids=["R1801", "R1904", "R1928"])
def parsed(request):
    data = request.param.read_bytes()
    return request.param, data, parse_v4_report(data)


@pytest.mark.corpus
def test_corpus_main_entries(parsed):
    path, _data, report = parsed
    main = [e for e in report.entries if e.section == "main"]
    assert len(main) == EXPECTED[path]["main"]
    assert [e.number for e in main] == list(range(1, len(main) + 1))
    assert report.total_words == EXPECTED[path]["total_words"]
    for entry in main:
        assert entry.words is not None and entry.words >= 1
        assert entry.url.startswith("http"), entry.number
        assert " " not in entry.url and "ﬁ" not in entry.url
        assert entry.fragments, entry.number
        assert any(fragment.runs for fragment in entry.fragments), entry.number
        for fragment in entry.fragments:
            assert "…" not in fragment.text
    # Межі «…» діють: фрагментів більше, ніж записів.
    assert sum(len(entry.fragments) for entry in main) > len(main)
    assert report.scores


@pytest.mark.corpus
def test_corpus_translated_count_matches_headers(parsed):
    _path, data, report = parsed
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        headers = sum(
            1
            for page in doc
            for raw in page.get_text().splitlines()
            if TRANSLATED_HEADER.match(re.sub(r"\s+", " ", raw).strip())
        )
    finally:
        doc.close()
    translated = [e for e in report.entries if e.section == "translated"]
    assert len(translated) == headers
    for entry in translated:
        assert entry.sentences is not None and entry.max_percent is not None
        assert entry.fragments
