"""Тести очищення й відображення сторінки звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 2."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import fitz
import pytest

from plag_filter.pdf import filter_pdf, parse_report, render_page_png

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
REPORT_2020 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-34-45.pdf"
REPORT_2002 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"

WHITE = 0xFFFFFF


@pytest.fixture(scope="module")
def data_2002() -> bytes:
    return REPORT_2002.read_bytes()


@pytest.fixture(scope="module")
def report_2002(data_2002: bytes):
    return parse_report(data_2002)


def _below_threshold(report) -> set[int]:
    """Номери джерел з відсотком нижче 0,1 % (нерозпізнаний відсоток не входить)."""
    return {
        number
        for number, row in report.rows.items()
        if row.percent is not None and row.percent < 0.1
    }


def _char_multiset(page: fitz.Page) -> Counter:
    """Мультимножина `(символ, x, y, колір)` з округленням координат до 0,1 пт."""
    counter: Counter = Counter()
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                color = span.get("color", 0)
                for char in span.get("chars", []):
                    x, y = char["origin"]
                    counter[(char["c"], round(x, 1), round(y, 1), color)] += 1
    return counter


@pytest.mark.corpus
def test_no_exclusions_keeps_page_streams_and_link_counts_byte_identical(
    data_2002: bytes, report_2002
) -> None:
    result = filter_pdf(data_2002, report_2002, set())

    original_doc = fitz.open(stream=data_2002, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        assert original_doc.page_count == result_doc.page_count
        for i in range(original_doc.page_count):
            assert original_doc[i].read_contents() == result_doc[i].read_contents(), i
            assert len(original_doc[i].get_links()) == len(result_doc[i].get_links()), i
    finally:
        original_doc.close()
        result_doc.close()


@pytest.mark.corpus
def test_excluding_below_threshold_keeps_exactly_the_listed_uris_and_internal_links(
    data_2002: bytes, report_2002
) -> None:
    excluded = _below_threshold(report_2002)
    kept_urls = sorted(
        url for number, row in report_2002.rows.items() if number not in excluded for url in row.urls
    )

    result = filter_pdf(data_2002, report_2002, excluded)
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        remaining_uris: list[str] = []
        internal_links = 0
        for page in result_doc:
            for link in page.get_links():
                uri = link.get("uri")
                if uri:
                    remaining_uris.append(uri)
                else:
                    internal_links += 1
    finally:
        result_doc.close()

    assert sorted(remaining_uris) == kept_urls
    assert internal_links == 3


@pytest.mark.corpus
def test_body_page_streams_equal_original_minus_excluded_event_cuts(
    data_2002: bytes, report_2002
) -> None:
    excluded = _below_threshold(report_2002)
    result = filter_pdf(data_2002, report_2002, excluded)

    cuts_by_page: dict[int, list[tuple[int, int]]] = {}
    for event in report_2002.events:
        if event.number in excluded:
            cuts_by_page.setdefault(event.page, []).extend(event.cuts)

    original_doc = fitz.open(stream=data_2002, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        for page_index in range(report_2002.body_first, report_2002.list_first):
            expected = bytearray(original_doc[page_index].read_contents())
            for start, end in sorted(set(cuts_by_page.get(page_index, [])), reverse=True):
                del expected[start:end]
            assert bytes(expected) == result_doc[page_index].read_contents(), page_index
    finally:
        original_doc.close()
        result_doc.close()


@pytest.mark.corpus
def test_page_count_unchanged_and_input_bytes_not_mutated(data_2002: bytes, report_2002) -> None:
    excluded = _below_threshold(report_2002)
    before_sha = hashlib.sha256(data_2002).hexdigest()

    result = filter_pdf(data_2002, report_2002, excluded)

    after_sha = hashlib.sha256(data_2002).hexdigest()
    assert before_sha == after_sha

    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        assert result_doc.page_count == report_2002.page_count
    finally:
        result_doc.close()


@pytest.mark.corpus
@pytest.mark.parametrize(
    "report_path, expected",
    [
        (REPORT_2002, {"changed_pages": 119, "removed_digits": 3090, "removed_spaces": 2060}),
    ],
    ids=["2002"],
)
def test_body_symbols_untouched_except_removed_white_digits_and_spaces_on_2002(
    report_path: Path, expected: dict[str, int]
) -> None:
    data = report_path.read_bytes()
    report = parse_report(data)
    excluded = _below_threshold(report)
    result = filter_pdf(data, report, excluded)

    original_doc = fitz.open(stream=data, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        changed_pages = 0
        removed_digits = 0
        removed_spaces = 0
        added_total = 0
        for page_index in range(report.body_first, report.list_first):
            before = _char_multiset(original_doc[page_index])
            after = _char_multiset(result_doc[page_index])
            if before == after:
                continue
            changed_pages += 1
            missing = before - after
            extra = after - before
            added_total += sum(extra.values())
            for (char, _x, _y, color), count in missing.items():
                assert color == WHITE, (page_index, char, hex(color))
                assert char.isdigit() or char.isspace(), (page_index, repr(char))
                if char.isdigit():
                    removed_digits += count
                else:
                    removed_spaces += count

            unchanged = before & after
            for key in unchanged:
                assert before[key] == after[key]
    finally:
        original_doc.close()
        result_doc.close()

    assert added_total == 0
    assert changed_pages == expected["changed_pages"]
    assert removed_digits == expected["removed_digits"]
    assert removed_spaces == expected["removed_spaces"]


@pytest.mark.corpus
def test_body_symbols_untouched_except_removed_white_digits_and_spaces_on_2020() -> None:
    data = REPORT_2020.read_bytes()
    report = parse_report(data)
    excluded = _below_threshold(report)
    result = filter_pdf(data, report, excluded)

    original_doc = fitz.open(stream=data, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        added_total = 0
        for page_index in range(report.body_first, report.list_first):
            before = _char_multiset(original_doc[page_index])
            after = _char_multiset(result_doc[page_index])
            if before == after:
                continue
            missing = before - after
            extra = after - before
            added_total += sum(extra.values())
            for (char, _x, _y, color), _count in missing.items():
                assert color == WHITE, (page_index, char, hex(color))
                assert char.isdigit() or char.isspace(), (page_index, repr(char))

            unchanged = before & after
            for key in unchanged:
                assert before[key] == after[key]
    finally:
        original_doc.close()
        result_doc.close()

    assert added_total == 0


@pytest.mark.corpus
def test_render_page_png_returns_png_bytes(data_2002: bytes, report_2002) -> None:
    png = render_page_png(data_2002, report_2002, report_2002.body_first, set())
    assert png.startswith(b"\x89PNG")


@pytest.mark.corpus
def test_render_page_png_excludes_pink_highlight_pixels(data_2002: bytes, report_2002) -> None:
    page_with_pink = None
    for page_index in range(report_2002.body_first, report_2002.list_first):
        numbers = report_2002.numbers_by_page.get(page_index, ())
        if numbers:
            page_with_pink = page_index
            page_numbers = set(numbers)
            break
    assert page_with_pink is not None

    png_plain = render_page_png(data_2002, report_2002, page_with_pink, set())
    png_excluded = render_page_png(data_2002, report_2002, page_with_pink, page_numbers)

    def count_pink(png_bytes: bytes) -> int:
        pix = fitz.Pixmap(png_bytes)
        n = 0
        samples = pix.samples
        stride = pix.n
        for i in range(0, len(samples), stride):
            r, g, b = samples[i], samples[i + 1], samples[i + 2]
            if abs(r - 255) <= 6 and abs(g - 214) <= 6 and abs(b - 214) <= 6:
                n += 1
        return n

    assert count_pink(png_excluded) < count_pink(png_plain)
