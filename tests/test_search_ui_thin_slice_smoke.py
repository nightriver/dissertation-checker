"""
Шлюз кроку 3 §22 PLAN_SEARCH.md (друга половина): UI smoke-тест показує
одну картку каналу A на реальному Streamlit-екрані ?mode=search і змінює
її статус. Доменна логіка (парсер, канал A, побудова запиту, переходи
статусу) перевіряється окремо в `test_search_thin_slice_integration.py` і
модульних тестах `search/ui_logic.py`; тут перевіряється лише «чистий
екран» app.py відповідно до §22: «app.py залишається тонкою оболонкою без
непокритої логіки».
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

HEADING_TEXT = "РОЗДІЛ 1"
BODY_TEXT = (
    "Початковий контекст. "
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства. Завершальний контекст."
)


def _build_single_section_pdf_bytes(body_text: str = BODY_TEXT) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    html = f"<p>{HEADING_TEXT}</p><p>{body_text}</p>"
    page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), html)
    data = doc.tobytes()
    doc.close()
    return data


def _run_with_uploaded_pdf(body_text: str = BODY_TEXT) -> AppTest:
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    uploader = app.get("file_uploader")[0]
    uploader.upload("dissertation.pdf", _build_single_section_pdf_bytes(body_text), "application/pdf")
    app.run(timeout=30)
    return app


def test_uploaded_pdf_renders_exactly_one_query_card():
    app = _run_with_uploaded_pdf()
    assert not app.exception

    radios = app.get("radio")
    assert len(radios) == 1
    assert radios[0].label == "Статус"
    assert radios[0].value == "unchecked"
    assert radios[0].index == 0

    markdown_text = "\n".join(md.value for md in app.markdown)
    assert "Авторське положення" in markdown_text
    assert "[A]" not in markdown_text
    assert "Знахідка 1 із 1" in markdown_text
    assert "«" in markdown_text and "»" in markdown_text

    code_blocks = [c.value for c in app.get("code")]
    assert any("пропонуємо" in block for block in code_blocks)


def test_changing_the_status_radio_updates_the_triage_state():
    app = _run_with_uploaded_pdf()
    result = app.session_state["search_result"]
    query_id = result.queries[0].query_id
    assert app.session_state["search_query_states"][query_id].status == "unchecked"

    app.get("radio")[0].set_value("found").run(timeout=30)

    assert not app.exception
    updated_state = app.session_state["search_query_states"][query_id]
    assert updated_state.status == "found"
    assert updated_state.found_engine == "google"
    assert app.get("radio")[0].value == "found"


def test_main_navigation_still_links_back_from_the_search_screen():
    app = _run_with_uploaded_pdf()
    markdown_text = "\n".join(md.value for md in app.markdown)
    assert 'href="?"' in markdown_text


def test_title_metadata_header_and_prompts_use_complete_name_and_title():
    with fitz.open(stream=_build_single_section_pdf_bytes(), filetype="pdf") as doc:
        title_page = doc.new_page(pno=0)
        title_page.insert_htmlbox(fitz.Rect(72, 72, 500, 700), (
            "<p>ЗАКЛАД ВИЩОЇ ОСВІТИ</p>"
            "<p>ПЕТРЕНКО</p><p>ІВАН ІВАНОВИЧ</p><p>УДК 004.9</p>"
            "<p>ДИСЕРТАЦІЯ</p><p>ПРАВОВІ ЗАСАДИ</p><p>СУДОУСТРОЮ</p>"
            "<p>Спеціальність 081 – Право</p><p>Київ – 2020</p>"
        ))
        data = doc.tobytes()
    app = AppTest.from_file(APP_PATH)
    app.query_params["mode"] = "search"
    app.run(timeout=30)
    app.get("file_uploader")[0].upload("work.pdf", data, "application/pdf")
    app.run(timeout=30)
    assert not app.exception
    metadata = app.session_state["search_metadata"]
    assert metadata.author == "Петренко Іван Іванович"
    assert metadata.title == "ПРАВОВІ ЗАСАДИ СУДОУСТРОЮ"
    assert metadata.year == 2020
    texts = [(item.type, item.value) for item in app if item.type in {"subheader", "markdown", "caption"}]
    author_index = texts.index(("subheader", metadata.author))
    assert texts[author_index + 1:author_index + 3] == [
        ("markdown", metadata.title), ("caption", "Рік роботи: 2020"),
    ]
    editor = next(item for item in app.expander if item.label == "Редагувати дані роботи")
    assert not editor.proto.expanded
    links = [item for item in app.get("link_button") if item.label in {"ChatGPT", "Perplexity"}]
    assert len(links) == 2
    for link in links:
        prompt = parse_qs(urlsplit(link.proto.url).query)["q"][0]
        assert "Автор: Петренко Іван Іванович\n" in prompt
        assert "Дисертація: ПРАВОВІ ЗАСАДИ СУДОУСТРОЮ\n" in prompt
        assert "Рік роботи: 2020\n" in prompt


@pytest.mark.parametrize("body_text,instruction", [
    (BODY_TEXT,
     "Знайди можливе джерело цього українського тексту. "
     "Шукай дослівні та злегка перефразовані збіги у всіх типах джерел. "
     "Виключи роботу, що перевіряється, її автореферат, копії, а також публікації цього ж автора.\n"
     "Автор: не вказано\nДисертація: не вказано\nРік роботи: не вказано\n"
     "Шукай більш ранні незалежні джерела. Покажи фрагменти, що збігаються, дати та посилання."),
    (BODY_TEXT.replace("вітчизняного", "діючого"),
     "Найди возможный русскоязычный оригинал этого украинского текста. "
     "Ищи дословные, переведенные и слегка перефразированные совпадения "
     "во всех типах источников. Покажи совпадающие фрагменты и дай ссылки."),
], ids=["ordinary", "calque"])
def test_assistant_buttons_send_full_paragraph_with_matching_prompt(body_text, instruction):
    app = _run_with_uploaded_pdf(body_text)
    assert not app.exception
    result = app.session_state["search_result"]
    assert result.queries
    links = [link for link in app.get("link_button") if link.label in {"ChatGPT", "Perplexity"}]
    assert len(links) == 2 * len(result.queries)
    for query in result.queries:
        paragraph = next(block.raw_text for block in result.document.blocks if block.block_id == query.block_id)
        assert len(paragraph) > len(query.donor_text)
        assert "Початковий контекст." in paragraph
        assert "Завершальний контекст." in paragraph
        for label, endpoint in (
            ("ChatGPT", "https://chatgpt.com/"),
            ("Perplexity", "https://www.perplexity.ai/search"),
        ):
            key = f"search_assistant_{query.query_id}_{label.lower()}"
            link = next(item for item in links if item.proto.id.endswith(key))
            assert not link.proto.disabled
            url = urlsplit(link.proto.url)
            assert f"{url.scheme}://{url.netloc}{url.path}" == endpoint
            assert parse_qs(url.query) == {"q": [f"{instruction}\n\n{paragraph}"]}
