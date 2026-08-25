import pytest

from compare.matcher import compare_documents
from parser.extractor import ScannedPDFError, extract_lines


COMMON = " ".join(f"commonword{index}" for index in range(20))


@pytest.mark.parametrize(
    ("left_kind", "right_kind"),
    [("pdf", "pdf"), ("pdf", "docx"), ("docx", "docx")],
)
def test_file_format_combinations_find_known_fragment(
    left_kind, right_kind, make_pdf, make_docx
):
    def build(kind, prefix):
        common_words = COMMON.split()
        paragraphs = [prefix] + [
            " ".join(common_words[index:index + 5])
            for index in range(0, len(common_words), 5)
        ]
        if kind == "pdf":
            return make_pdf([paragraphs]), f"{prefix}.pdf"
        return make_docx(paragraphs), f"{prefix}.docx"

    left_data, left_name = build(left_kind, "leftonly")
    right_data, right_name = build(right_kind, "rightonly")
    result = compare_documents(
        extract_lines(left_data, left_name),
        extract_lines(right_data, right_name),
    )
    assert result.segments
    assert result.segments[0].kind == "verbatim"
    if left_kind == "pdf":
        assert result.segments[0].a_start >= 0


def test_scanned_pdf_error_is_local_and_other_file_still_reads(empty_pdf, make_docx):
    valid = extract_lines(make_docx([COMMON]), "valid.docx")
    with pytest.raises(ScannedPDFError):
        extract_lines(empty_pdf, "scan.pdf")
    assert valid
