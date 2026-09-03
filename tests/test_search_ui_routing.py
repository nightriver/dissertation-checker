from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _main_navigation(app):
    return next(item.value for item in app.markdown if 'class="app-main-nav"' in item.value)


def test_default_page_offers_all_four_sections():
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    navigation = _main_navigation(app)
    assert navigation.count('class="app-main-nav__item') == 4
    assert 'href="?"' in navigation
    assert 'href="?mode=search"' in navigation
    assert 'href="?mode=compare"' in navigation
    assert 'href="?mode=table-highlight"' in navigation


def test_search_query_opens_only_search_screen():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == [
        "Дисертація (PDF із текстовим шаром)"
    ]
    labels = [button.label for button in app.button]
    assert "Порівняти" not in labels
    navigation = _main_navigation(app)
    assert (
        'app-main-nav__item app-main-nav__item--active" href="?mode=search" '
        'target="_self" aria-current="page">Пошук джерел вручну'
    ) in navigation


def test_search_mode_does_not_leak_compare_widgets():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    assert not app.exception
    assert "compare_checked_upload" not in {w.key for w in app.get("file_uploader")}


def test_table_highlight_query_opens_only_table_screen():
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "table-highlight"
    app.run(timeout=30)
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == ["Таблиця порівняння (DOCX)"]
    navigation = _main_navigation(app)
    assert (
        'app-main-nav__item app-main-nav__item--active" href="?mode=table-highlight" '
        'target="_self" aria-current="page">Підсвічування таблиці'
    ) in navigation
