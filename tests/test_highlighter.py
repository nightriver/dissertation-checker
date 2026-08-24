"""
Tests for highlighter.py — previously 0% covered (the module could not even be
imported without PyMuPDF installed).

Run: pytest tests/test_highlighter.py
"""
import fitz

from parser.highlighter import (
    _build_page_spans,
    _highlight_page,
    highlight_citations_pdf,
)


def _page_of(pdf_bytes: bytes, idx: int = 0):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return doc, doc[idx]


class TestBuildPageSpans:
    """Char-index → quad mapping must line up with the string it builds."""

    def _word(self, text, block_no, x0=0.0):
        # (x0, y0, x1, y1, text, block_no, line_no, word_no)
        return (x0, 0.0, x0 + 10.0, 10.0, text, block_no, 0, 0)

    def test_offsets_match_the_built_string(self):
        words = [self._word("alpha", 0, 0), self._word("beta", 0, 20)]
        full_text, spans = _build_page_spans(words)

        assert full_text.startswith("alpha beta")
        for span, expected in zip(spans, ["alpha", "beta"]):
            assert full_text[span["start"]:span["end"]] == expected

    def test_block_change_inserts_newline_and_keeps_offsets(self):
        words = [self._word("alpha", 0), self._word("beta", 1)]
        full_text, spans = _build_page_spans(words)

        assert "\n" in full_text
        for span, expected in zip(spans, ["alpha", "beta"]):
            assert full_text[span["start"]:span["end"]] == expected

    def test_empty_input(self):
        full_text, spans = _build_page_spans([])
        assert full_text == ""
        assert spans == []


class TestHighlightPage:
    def test_real_citation_is_found(self, make_pdf):
        data = make_pdf([["see [12] here"]])
        doc, page = _page_of(data)
        try:
            assert _highlight_page(page) is True
            assert len(list(page.annots())) == 1
        finally:
            doc.close()

    def test_array_index_is_not_a_citation(self, make_pdf):
        # Regression: the old, looser pattern counted this page as cited,
        # so it never appeared in the "pages without citations" report.
        data = make_pdf([["array a[0] = 1 and c[0] = 2"]])
        doc, page = _page_of(data)
        try:
            assert _highlight_page(page) is False
            assert list(page.annots()) == []
        finally:
            doc.close()

    def test_small_index_stays_ambiguous(self, make_pdf):
        # Documents a real limit, not a bug: "b[3]" is indistinguishable from
        # a reference to source 3 by text alone, so it is still highlighted.
        # Only out-of-range numbers (0, years, >999) can be ruled out.
        data = make_pdf([["array b[3] = 2"]])
        doc, page = _page_of(data)
        try:
            assert _highlight_page(page) is True
        finally:
            doc.close()

    def test_year_bracket_is_not_a_citation(self, make_pdf):
        data = make_pdf([["in [2020] the author wrote"]])
        doc, page = _page_of(data)
        try:
            assert _highlight_page(page) is False
        finally:
            doc.close()

    def test_page_without_text(self):
        doc = fitz.open()
        page = doc.new_page()
        try:
            assert _highlight_page(page) is False
        finally:
            doc.close()

    def test_citation_split_across_two_lines(self, make_pdf):
        data = make_pdf([["reference [124;", "149] continues"]])
        doc, page = _page_of(data)
        try:
            assert _highlight_page(page) is True
        finally:
            doc.close()


class TestHighlightCitationsPdf:
    def test_returns_a_valid_pdf(self, make_pdf):
        data = make_pdf([["text [1]"], ["text [2]"], ["text [3]"]])
        out, _pages, _tracked = highlight_citations_pdf(data, None, skip_first=0)

        doc = fitz.open(stream=out, filetype="pdf")
        try:
            assert len(doc) == 3
        finally:
            doc.close()

    def test_skip_first_excludes_pages_from_tracking(self, make_pdf):
        # 4 pages, none cited, skip_first=2 → only pages 3 and 4 tracked
        data = make_pdf([["plain"], ["plain"], ["plain"], ["plain"]])
        _out, pages_without, tracked = highlight_citations_pdf(data, None, skip_first=2)

        assert tracked == 2
        assert pages_without == [3, 4]

    def test_bibliography_pages_are_excluded(self, make_pdf):
        # 6 pages, bibliography starts on page 5 → body is pages 1-4,
        # skip_first=2 → tracked pages are 3 and 4
        data = make_pdf([["plain"]] * 6)
        _out, pages_without, tracked = highlight_citations_pdf(data, 5, skip_first=2)

        assert tracked == 2
        assert pages_without == [3, 4]

    def test_cited_pages_are_not_reported_as_empty(self, make_pdf):
        data = make_pdf([["plain"], ["plain"], ["text [7]"], ["plain"]])
        _out, pages_without, tracked = highlight_citations_pdf(data, None, skip_first=2)

        assert tracked == 2
        assert pages_without == [4]

    def test_tracked_count_can_be_zero(self, make_pdf):
        # biblio on page 3, skip_first=2 → nothing left to track.
        # app.py divides by this value, so it must come back as a clean 0.
        data = make_pdf([["plain"]] * 4)
        _out, pages_without, tracked = highlight_citations_pdf(data, 3, skip_first=2)

        assert tracked == 0
        assert pages_without == []

    def test_biblio_on_first_page_is_not_treated_as_a_bound(self, make_pdf):
        # biblio_start_page=1 would make last_body_idx negative; the guard
        # keeps the whole document in range instead.
        data = make_pdf([["plain"], ["plain"], ["plain"]])
        _out, _pages, tracked = highlight_citations_pdf(data, 1, skip_first=0)
        assert tracked == 3

    def test_document_is_closed(self, make_pdf, monkeypatch):
        """Regression: fitz.open() used to run without `with`/close()."""
        opened = []
        real_open = fitz.open

        def spy(*args, **kwargs):
            doc = real_open(*args, **kwargs)
            opened.append(doc)
            return doc

        monkeypatch.setattr("parser.highlighter.fitz.open", spy)

        data = make_pdf([["text [1]"]])
        highlight_citations_pdf(data, None, skip_first=0)

        assert opened, "fitz.open was never called"
        assert all(d.is_closed for d in opened), "document left open"
