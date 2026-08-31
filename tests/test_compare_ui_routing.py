from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _main_navigation(app):
    return next(item.value for item in app.markdown if 'class="app-main-nav"' in item.value)


def test_default_page_keeps_original_workflow_and_marks_bibliography_active():
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Оберіть файл дисертації (.pdf або .docx)"
    ]
    navigation = _main_navigation(app)
    assert 'href="?mode=compare"' in navigation
    assert (
        'app-main-nav__item app-main-nav__item--active" href="?" '
        'target="_self" aria-current="page">Перевірка джерел'
    ) in navigation


def test_compare_query_opens_only_two_file_screen():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "compare"
    app.run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Перевірювана дисертація", "Ймовірне джерело"
    ]
    labels = [button.label for button in app.button]
    assert "Порівняти" in labels
    navigation = _main_navigation(app)
    assert (
        'app-main-nav__item app-main-nav__item--active" href="?mode=compare" '
        'target="_self" aria-current="page">Порівняння двох робіт'
    ) in navigation
