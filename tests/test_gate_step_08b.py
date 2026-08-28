"""Шлюз тонкого вимірювача кроку 8b (PLAN_SEARCH.md, §20.1)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import fitz

from parser.searchdoc import PARSER_VERSION, parse_search_document
from search import ALGO_VERSION
from search.calques import DICT_VERSION, compute_metrics
from tools import measure_calques


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_htmlbox(
        fitz.Rect(72, 72, 523, 770),
        "<p>ВСТУП</p><p>Це положення являється важливим для дослідження.</p>",
    )
    page = document.new_page(width=595, height=842)
    page.insert_htmlbox(
        fitz.Rect(72, 72, 523, 770),
        "<p>СПИСОК ЛІТЕРАТУРИ</p>"
        "<p>1. Иванов И. И. Теория права. Москва, 2001.</p>"
        "<p>2. Петренко П. П. Теорія права. Київ, 2002.</p>",
    )
    result = document.tobytes()
    document.close()
    return result


def test_gate_tool_has_no_external_parser_or_dictionary_copy() -> None:
    source_path = Path(measure_calques.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    assert "subprocess" not in imported
    assert "CALQUES" not in assigned
    assert "entry_language" not in functions
    assert "split_body_biblio" not in functions


def test_gate_measurement_uses_the_shared_versions_and_metrics() -> None:
    data = _pdf_bytes()
    parsed = parse_search_document(data)
    shared = compute_metrics(parsed)

    measured = measure_calques.measure_pdf_bytes(data, name="sample.pdf")

    assert measured.parser_version == PARSER_VERSION
    assert measured.dictionary_version == DICT_VERSION
    assert measured.algo_version == ALGO_VERSION
    assert measured.document_sha256 == parsed.document_sha256
    assert measured.author_words == shared.author_words
    assert measured.tier1_hits == shared.tier1_hits
    assert measured.tier1_density == shared.tier1_density


def test_gate_language_statistics_come_from_the_shared_detector() -> None:
    measured = measure_calques.measure_pdf_bytes(_pdf_bytes(), name="sample.pdf")

    assert measured.bibliography_total == 2
    assert (measured.language_ru, measured.language_uk) == (1, 1)
    assert (measured.language_mixed, measured.language_unknown) == (0, 0)
    assert measured.ru_ratio == 0.5


def test_gate_json_report_is_deterministic_and_has_three_top_level_versions() -> None:
    measured = (measure_calques.measure_pdf_bytes(_pdf_bytes(), name="sample.pdf"),)

    first = measure_calques.render_json(measured)
    second = measure_calques.render_json(measured)
    payload = json.loads(first)

    assert first == second
    assert payload["parser_version"] == PARSER_VERSION
    assert payload["dictionary_version"] == DICT_VERSION
    assert payload["algo_version"] == ALGO_VERSION
    assert payload["files"][0]["file"] == "sample.pdf"


def test_gate_human_report_always_prints_versions_and_core_metrics() -> None:
    measured = (measure_calques.measure_pdf_bytes(_pdf_bytes(), name="sample.pdf"),)

    report = measure_calques.render_table(measured, verbose=True)

    assert f"PARSER_VERSION={PARSER_VERSION}" in report
    assert f"DICT_VERSION={DICT_VERSION}" in report
    assert f"ALGO_VERSION={ALGO_VERSION}" in report
    assert "sample" in report
    assert "RU=1 UK=1" in report


def test_gate_repeated_measurement_is_byte_for_byte_equal() -> None:
    data = _pdf_bytes()
    assert measure_calques.measure_pdf_bytes(data) == measure_calques.measure_pdf_bytes(data)


def test_gate_non_pdf_input_fails_explicitly(tmp_path, capsys) -> None:
    text_path = tmp_path / "dump.txt"
    text_path.write_text("не PDF", encoding="utf-8")

    result = measure_calques.main([str(text_path)])

    assert result == 1
    assert "Очікується PDF-файл" in capsys.readouterr().err

