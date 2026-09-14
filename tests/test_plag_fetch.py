"""Тести завантаження документів режиму очищення звіту Plag —
PLAN_PLAG_FILTER.md, §10.2, етап 5.

Мережа — лише локальний `http.server` у потоці на 127.0.0.1 з
`allow_private=True`. Реальні зовнішні запити тут не виконуються.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import fitz
import pytest

from plag_filter import fetch as fetch_module
from plag_filter.fetch import fetch_document


DEFAULT_PDF_TEXT = (
    "Текст документа для перевірки завантаження та розбору PDF-файлу. "
    "Цей абзац навмисно довгий, щоб перевищити поріг двохсот непробільних "
    "символів і пройти перевірку no_text_layer у fetch_document. Додатковий "
    "рядок тексту додається саме для того, щоб гарантовано перевищити поріг "
    "навіть з урахуванням пробілів."
)


def make_pdf_bytes(text: str | None = DEFAULT_PDF_TEXT) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(fitz.Rect(36, 36, 559, 800), text)
    data = doc.tobytes()
    doc.close()
    return data


class _Server:
    """Локальний HTTP-сервер у потоці для тестів `fetch_document`."""

    def __init__(self, handler_factory):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler_factory)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _run_server(handler_factory):
    server = _Server(handler_factory)
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "plag_fetch"
    directory.mkdir()
    return directory


def _assert_tmp_dir_empty(tmp_dir: Path) -> None:
    assert list(tmp_dir.iterdir()) == []


def test_fetch_pdf_with_text_ok(tmp_dir: Path) -> None:
    pdf_bytes = make_pdf_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — ім'я задане http.server
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        def log_message(self, format, *args):  # noqa: A002 — сигнатура базового класу
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/doc.pdf", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is True
    assert result.error is None
    assert result.kind == "pdf"
    assert result.pages
    assert "fetch_document" in result.pages[0]
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_html_with_citation_publication_date(tmp_dir: Path) -> None:
    html = (
        "<html><head>"
        '<meta name="citation_publication_date" content="2019-05-01">'
        "</head><body><p>Стаття про дослідження.</p></body></html>"
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/page.html", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is True
    assert result.kind == "html"
    assert result.meta.get("citation_publication_date") == "2019-05-01"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_repository_citation_pdf_url_redirects_to_pdf(tmp_dir: Path) -> None:
    pdf_bytes = make_pdf_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/repo/item":
                body = (
                    "<html><head>"
                    f'<meta name="citation_pdf_url" content="http://{self.headers["Host"]}/files/x.pdf">'
                    '<meta name="dc.date.issued" content="2018">'
                    "</head><body>Опис матеріалу.</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/files/x.pdf":
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.end_headers()
                self.wfile.write(pdf_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/repo/item", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is True
    assert result.kind == "pdf"
    assert result.repository_meta.get("dc.date.issued") == "2018"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_repository_bitstream_link_redirects_to_pdf(tmp_dir: Path) -> None:
    pdf_bytes = make_pdf_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/repo/item2":
                body = (
                    "<html><head>"
                    '<meta name="dc.date.issued" content="2017">'
                    "</head><body>"
                    '<a href="/bitstream/handle/1/2/thesis.PDF?download=1">завантажити</a>'
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/bitstream/handle/1/2/thesis.PDF"):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.end_headers()
                self.wfile.write(pdf_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/repo/item2", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is True
    assert result.kind == "pdf"
    assert result.repository_meta.get("dc.date.issued") == "2017"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_too_many_redirects(tmp_dir: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                step = int(self.path.strip("/").split("/")[-1])
            except ValueError:
                step = 0
            if step < 6:
                self.send_response(302)
                self.send_header("Location", f"/hop/{step + 1}")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html></html>")

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/hop/0", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "too_many_redirects"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_http_404(tmp_dir: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/missing", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "http_404"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_rate_limited(tmp_dir: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(429)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/busy", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "rate_limited"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_too_large_html(tmp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module, "MAX_HTML_BYTES", 10)
    body = b"<html>" + b"a" * 100 + b"</html>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/big.html", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "too_large"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_pdf_no_text_layer(tmp_dir: Path) -> None:
    pdf_bytes = make_pdf_bytes(text=None)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/empty.pdf", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "no_text_layer"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_timeout(tmp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module, "TIMEOUT_SECONDS", 1)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(3)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/slow", tmp_dir=tmp_dir, allow_private=True)

    assert result.ok is False
    assert result.error == "timeout"
    _assert_tmp_dir_empty(tmp_dir)


def test_fetch_blocked_address_without_allow_private(tmp_dir: Path) -> None:
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")

        def log_message(self, format, *args):
            pass

    for server in _run_server(Handler):
        result = fetch_document(f"{server.url}/page", tmp_dir=tmp_dir, allow_private=False)

    assert result.ok is False
    assert result.error == "blocked_address"
    assert calls == []
    _assert_tmp_dir_empty(tmp_dir)


def test_no_new_dependencies_in_requirements() -> None:
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "requests" not in requirements.casefold()
    assert "httpx" not in requirements.casefold()
    assert "beautifulsoup" not in requirements.casefold()
