"""
Спільні фікстури: будують справжні DOCX/PDF у пам'яті.

Бінарні зразки в репозиторій не кладемо — документи збираються тут, тож
кожен тест бачить рівно ту структуру, яку перевіряє.
"""
import io

import pytest

docx = pytest.importorskip("docx", reason="python-docx not installed")
fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")


@pytest.fixture
def make_docx():
    """
    build([(text, style_name), ...]) -> bytes

    style_name=None → звичайний абзац ("Normal").
    """
    def build(paragraphs) -> bytes:
        document = docx.Document()
        for item in paragraphs:
            text, style = item if isinstance(item, tuple) else (item, None)
            if style:
                document.add_paragraph(text, style=style)
            else:
                document.add_paragraph(text)
        buf = io.BytesIO()
        document.save(buf)
        return buf.getvalue()
    return build


@pytest.fixture
def make_numbered_docx():
    """
    build(intro_paragraphs, numbered_paragraphs) -> bytes

    numbered_paragraphs отримують стиль 'List Number' — Word-івську
    автонумерацію, за якої номер НЕ зберігається в тексті абзацу.
    """
    def build(intro, numbered) -> bytes:
        document = docx.Document()
        for text in intro:
            document.add_paragraph(text)
        for text in numbered:
            document.add_paragraph(text, style="List Number")
        buf = io.BytesIO()
        document.save(buf)
        return buf.getvalue()
    return build


@pytest.fixture
def make_pdf():
    """
    build([[line, line, ...], [line, ...]]) -> bytes   (список сторінок)
    """
    def build(pages) -> bytes:
        document = fitz.open()
        for lines in pages:
            page = document.new_page()
            y = 72
            for line in lines:
                page.insert_text((72, y), line, fontsize=11)
                y += 16
        data = document.tobytes()
        document.close()
        return data
    return build


@pytest.fixture
def empty_pdf():
    """PDF без текстового шару — імітація скан-копії."""
    document = fitz.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    return data
