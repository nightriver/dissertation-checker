from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_default_page_offers_both_mode_links():
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    labels = [button.label for button in app.button]
    assert "Порівняти дві роботи →" in labels
    assert "Пошук джерел вручну →" in labels


def test_search_query_opens_only_search_screen():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Дисертація (PDF із текстовим шаром)"
    ]
    labels = [button.label for button in app.button]
    assert "← Повернутися до перевірки джерел" in labels
    assert "Порівняти" not in labels


def test_search_back_button_removes_only_mode_query_parameter():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.query_params["keep"] = "value"
    app.run(timeout=30)
    app.button[0].click().run(timeout=30)
    assert "mode" not in app.query_params
    assert app.query_params["keep"] in ("value", ["value"])


def test_search_mode_does_not_leak_compare_widgets():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    assert not app.exception
    assert "compare_checked_upload" not in {w.key for w in app.get("file_uploader")}
