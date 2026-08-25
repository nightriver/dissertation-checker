from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_default_page_keeps_original_workflow_and_only_compare_link():
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Оберіть файл дисертації (.pdf або .docx)"
    ]
    assert "Порівняти дві роботи →" in [button.label for button in app.button]


def test_compare_query_opens_only_two_file_screen():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "compare"
    app.run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Перевірювана дисертація", "Ймовірне джерело"
    ]
    labels = [button.label for button in app.button]
    assert "← Повернутися до перевірки джерел" in labels
    assert "Порівняти" in labels
