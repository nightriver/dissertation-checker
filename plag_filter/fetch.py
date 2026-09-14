"""Завантаження документів джерел для режиму очищення звіту Plag.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §6, §9. Лише стандартна бібліотека
та PyMuPDF; нових залежностей не додавати. Завантаження послідовні — паралелі
й повторні спроби тут не робляться.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import fitz

# Числа й коди помилок — PLAN_PLAG_FILTER.md, §6.
TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024
MAX_PDF_BYTES = 150 * 1024 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
MIN_TEXT_CHARS = 200
USER_AGENT = "dissertation-checker/1.0 (plag-filter)"


@dataclass
class FetchResult:
    """Результат спроби завантажити документ за адресою — §9."""

    ok: bool
    error: str | None
    url: str
    final_url: str | None
    kind: Literal["pdf", "html"] | None
    pages: list[str]
    meta: dict[str, str]
    jsonld: list[str]
    repository_meta: dict[str, str]
    hints: dict[str, str]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Вимикає автоматичне проходження за `Location`.

    Кожне перенаправлення перевіряється окремо на приватну адресу й
    рахується для ліміту `MAX_REDIRECTS` — PLAN_PLAG_FILTER.md, §6.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


class _HTMLExtractor(HTMLParser):
    """Розбір HTML: метаполя, JSON-LD, посилання `/bitstream/…pdf`, текст."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.bitstream_links: list[str] = []
        self._text_parts: list[str] = []
        self._skip_depth = 0
        self._in_jsonld = False
        self._jsonld_buffer: list[str] = []

    def text(self) -> str:
        return "".join(self._text_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "meta":
            name = attrs_dict.get("name")
            content = attrs_dict.get("content")
            if name and content is not None:
                self.meta[name.casefold()] = content
        elif tag == "a":
            href = attrs_dict.get("href")
            if href and "/bitstream/" in href.casefold():
                path_only = href.split("?", 1)[0].split("#", 1)[0]
                if path_only.casefold().endswith(".pdf"):
                    self.bitstream_links.append(href)
        elif tag == "script":
            script_type = (attrs_dict.get("type") or "").casefold()
            if script_type == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buffer = []
            self._skip_depth += 1
        elif tag in ("style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._in_jsonld:
                self.jsonld.append("".join(self._jsonld_buffer))
                self._in_jsonld = False
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in ("style", "noscript"):
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buffer.append(data)
            return
        if self._skip_depth == 0:
            self._text_parts.append(data)


def _error_result(url: str, error: str) -> FetchResult:
    return FetchResult(
        ok=False,
        error=error,
        url=url,
        final_url=None,
        kind=None,
        pages=[],
        meta={},
        jsonld=[],
        repository_meta={},
        hints={},
    )


def _check_address(host: str, allow_private: bool) -> str | None:
    """Перевірити, що всі адреси хоста глобальні — PLAN_PLAG_FILTER.md, §6."""
    if allow_private:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "network_error"
    if not infos:
        return "network_error"
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return "network_error"
        if not ip.is_global:
            return "blocked_address"
    return None


def _fetch_once(
    url: str, *, tmp_dir: Path, allow_private: bool
) -> tuple[FetchResult, str | None]:
    """Одна спроба завантаження з проходженням перенаправлень — §6."""
    current_url = url
    redirects = 0
    while True:
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return _error_result(url, "blocked_address"), None

        block_reason = _check_address(parsed.hostname, allow_private)
        if block_reason is not None:
            return _error_result(url, block_reason), None

        request = urllib.request.Request(current_url, headers={"User-Agent": USER_AGENT})
        try:
            response = _OPENER.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if 300 <= code < 400:
                location = exc.headers.get("Location") if exc.headers else None
                exc.close()
                if not location:
                    return _error_result(url, f"http_{code}"), None
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    return _error_result(url, "too_many_redirects"), None
                current_url = urljoin(current_url, location)
                continue
            exc.close()
            if code == 429:
                return _error_result(url, "rate_limited"), None
            return _error_result(url, f"http_{code}"), None
        except (socket.timeout, TimeoutError):
            return _error_result(url, "timeout"), None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return _error_result(url, "timeout"), None
            return _error_result(url, "network_error"), None
        break

    return _download_and_parse(url, current_url, response, tmp_dir)


def _download_and_parse(
    url: str, final_url: str, response, tmp_dir: Path
) -> tuple[FetchResult, str | None]:
    """Зчитати тіло відповіді у тимчасовий файл і розібрати — §6."""
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, prefix="plag_fetch_")
        tmp_path = Path(tmp_name)
        os.close(fd)

        too_large = False
        kind: Literal["pdf", "html"] = "html"
        try:
            with open(tmp_path, "wb") as tmp_file:
                peek = response.read(5)
                kind = "pdf" if peek.startswith(b"%PDF-") else "html"
                limit = MAX_PDF_BYTES if kind == "pdf" else MAX_HTML_BYTES
                total = len(peek)
                tmp_file.write(peek)
                too_large = total > limit
                while not too_large:
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    tmp_file.write(chunk)
                    total += len(chunk)
                    too_large = total > limit
        except (socket.timeout, TimeoutError):
            return _error_result(url, "timeout"), None
        except OSError:
            return _error_result(url, "network_error"), None

        if too_large:
            return _error_result(url, "too_large"), None

        headers = response.headers
        if kind == "pdf":
            return _parse_pdf(url, final_url, tmp_path, headers)
        return _parse_html(url, final_url, tmp_path, headers)
    finally:
        response.close()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _parse_pdf(
    url: str, final_url: str, tmp_path: Path, headers
) -> tuple[FetchResult, str | None]:
    """Текст PDF постранично — PLAN_PLAG_FILTER.md, §6."""
    doc = fitz.open(str(tmp_path))
    try:
        pages = [page.get_text() for page in doc]
        hints: dict[str, str] = {}
        last_modified = headers.get("Last-Modified") if headers is not None else None
        if last_modified:
            hints["last_modified"] = last_modified
        creation_date = (doc.metadata or {}).get("creationDate")
        if creation_date:
            hints["pdf_creation"] = creation_date
    finally:
        doc.close()

    total_chars = sum(1 for page_text in pages for ch in page_text if not ch.isspace())
    if total_chars < MIN_TEXT_CHARS:
        return _error_result(url, "no_text_layer"), None

    return (
        FetchResult(
            ok=True,
            error=None,
            url=url,
            final_url=final_url,
            kind="pdf",
            pages=pages,
            meta={},
            jsonld=[],
            repository_meta={},
            hints=hints,
        ),
        None,
    )


def _parse_html(
    url: str, final_url: str, tmp_path: Path, headers
) -> tuple[FetchResult, str | None]:
    """Текст HTML без `script`/`style`/`noscript` — PLAN_PLAG_FILTER.md, §6."""
    charset = headers.get_content_charset() if headers is not None else None
    raw = tmp_path.read_bytes()
    encoding = charset or "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        text = raw.decode("utf-8", errors="replace")

    extractor = _HTMLExtractor()
    extractor.feed(text)
    extractor.close()

    hints: dict[str, str] = {}
    last_modified = headers.get("Last-Modified") if headers is not None else None
    if last_modified:
        hints["last_modified"] = last_modified
    accessioned = extractor.meta.get("dc.date.accessioned")
    if accessioned:
        hints["dc.date.accessioned"] = accessioned

    target = extractor.meta.get("citation_pdf_url")
    if not target and extractor.bitstream_links:
        target = extractor.bitstream_links[0]

    result = FetchResult(
        ok=True,
        error=None,
        url=url,
        final_url=final_url,
        kind="html",
        pages=[extractor.text()],
        meta=extractor.meta,
        jsonld=extractor.jsonld,
        repository_meta={},
        hints=hints,
    )
    return result, target


def fetch_document(url: str, *, tmp_dir: Path, allow_private: bool = False) -> FetchResult:
    """Завантажити документ за адресою — PLAN_PLAG_FILTER.md, §6, §9.

    Сторінка репозиторію з `citation_pdf_url` або посиланням `/bitstream/…pdf`
    веде до файлу один раз; метаполя сторінки зберігаються в
    `repository_meta` результату.
    """
    result, target = _fetch_once(url, tmp_dir=tmp_dir, allow_private=allow_private)
    if not result.ok or result.kind != "html" or not target:
        return result

    resolved_target = urljoin(result.final_url or url, target)
    pdf_result, _unused = _fetch_once(resolved_target, tmp_dir=tmp_dir, allow_private=allow_private)
    return replace(pdf_result, url=url, repository_meta=dict(result.meta))
