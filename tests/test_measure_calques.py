"""Модульні тести CLI ``tools/measure_calques.py`` (PLAN_SEARCH.md, §20.1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import fitz

from tools.measure_calques import main, measure_file, render_json


def _write_pdf(path) -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_htmlbox(
        fitz.Rect(72, 72, 523, 770),
        "<p>ВСТУП</p><p>Автор аналізує важливе наукове питання.</p>",
    )
    page = document.new_page(width=595, height=842)
    page.insert_htmlbox(
        fitz.Rect(72, 72, 523, 770),
        "<p>СПИСОК ЛІТЕРАТУРИ</p>"
        "<p>1. Автор А. А. Теорія сучасного права. Київ, 2020.</p>",
    )
    document.save(path)
    document.close()


def test_measure_file_preserves_input_filename(tmp_path) -> None:
    path = tmp_path / "sample.PDF"
    _write_pdf(path)

    assert measure_file(path).file == "sample.PDF"


def test_json_cli_prints_parseable_report(tmp_path, capsys) -> None:
    path = tmp_path / "sample.pdf"
    _write_pdf(path)

    assert main(["--json", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["files"][0]["file"] == "sample.pdf"


def test_json_keeps_input_file_order(tmp_path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    _write_pdf(first)
    _write_pdf(second)

    report = render_json((measure_file(first), measure_file(second)))

    assert [item["file"] for item in json.loads(report)["files"]] == ["a.pdf", "b.pdf"]


def test_missing_pdf_returns_nonzero_without_partial_report(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.pdf"

    assert main([str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Помилка вимірювання" in captured.err


def test_script_can_be_launched_directly_from_the_repository_root(tmp_path) -> None:
    path = tmp_path / "sample.pdf"
    _write_pdf(path)
    script = Path(__file__).parents[1] / "tools" / "measure_calques.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json", str(path)],
        cwd=script.parents[1],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["files"][0]["file"] == "sample.pdf"
