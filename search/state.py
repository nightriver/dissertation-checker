"""
search/state.py
JSON-схема проєкту режиму пошуку, статуси триажу та атомарне
відновлення стану. Специфікація — PLAN_SEARCH.md, §18.

Крок 3 (§22) реалізував лише сам об'єкт стану картки — `QueryState` та
перевірені переходи статусів §18.1. Крок 13 (§22) добудовує §18 повністю:
експорт у JSON-проєкт, транзакційну валідацію допуску (§18.3) і зіставлення
запитів при імпорті (§18.2). Стан триажу свідомо не живе як довільні ключі
`st.session_state` (CLAUDE.md, правило №10): екран тримає
`dict[query_id, QueryState]`, а цей модуль лише вміє серіалізувати той
контейнер у JSON і назад.

Імпорт транзакційний: `apply_project` спершу викликає `validate_project` і
до її успішного проходження не будує жодного стану. Незіставлений запис не
зникає — він переносить у `unmatched` без втрати вмісту.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from enum import Enum
from typing import Literal
from urllib.parse import urlparse

from search.types import (
    SearchDocument,
    SearchQuery,
    SearchResult,
    SectionKind,
    SectionOverride,
    SectionOverrideAction,
)

QueryStatus = Literal["unchecked", "no_result", "found"]


@dataclasses.dataclass(frozen=True)
class QueryState:
    """Стан триажу однієї картки (§18.1). Мутабельний бізнес-стан UI-сесії."""

    query_id: str
    status: QueryStatus = "unchecked"
    needs_review: bool = False
    previous_status: QueryStatus | None = None
    prior_snapshot: str | None = None
    found_engine: str | None = None
    source_url: str | None = None
    failed_engines: tuple[str, ...] = ()
    comment: str = ""


def initial_state(query_id: str) -> QueryState:
    """Початковий стан щойно побудованого запиту: `unchecked`."""
    return QueryState(query_id=query_id)


def is_absolute_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def mark_unchecked(state: QueryState) -> QueryState:
    """§18.1: скидає статус на `unchecked`, не чіпаючи коментар/логи спроб."""
    return dataclasses.replace(
        state, status="unchecked", found_engine=None, source_url=None
    )


def mark_no_result(state: QueryState, *, comment: str | None = None) -> QueryState:
    """
    §18.1: `no_result` очищає `found_engine`/`source_url`, але не
    `failed_engines` і не коментар (якщо новий коментар не переданий).
    """
    return dataclasses.replace(
        state,
        status="no_result",
        found_engine=None,
        source_url=None,
        comment=state.comment if comment is None else comment,
    )


def mark_found(
    state: QueryState,
    *,
    found_engine: str,
    source_url: str | None = None,
    comment: str | None = None,
) -> QueryState:
    """§18.1: `found` вимагає `found_engine`; `source_url`, якщо є, — абсолютний http(s)."""
    if not found_engine:
        raise ValueError("Статус 'found' вимагає непорожній found_engine.")
    if source_url is not None and not is_absolute_http_url(source_url):
        raise ValueError("source_url має бути абсолютним http/https URL.")
    return dataclasses.replace(
        state,
        status="found",
        found_engine=found_engine,
        source_url=source_url,
        comment=state.comment if comment is None else comment,
    )


def add_failed_engine(state: QueryState, engine_code: str) -> QueryState:
    """Технічна недоступність рушія не є статусом усього запиту (§18.1)."""
    if engine_code in state.failed_engines:
        return state
    return dataclasses.replace(state, failed_engines=state.failed_engines + (engine_code,))


def is_counted_as_checked(state: QueryState) -> bool:
    """§18.1: у метриках перевіреними вважаються лише `no_result`/`found` без `needs_review`."""
    return state.status in ("no_result", "found") and not state.needs_review


# ---------------------------------------------------------------------------
# JSON-схема проєкту (§18.3)
# ---------------------------------------------------------------------------

CURRENT_SCHEMA_VERSION = 1


class ImportRejectReason(Enum):
    MALFORMED_JSON = "malformed_json"
    SCHEMA_MISSING = "schema_missing"
    SCHEMA_MISMATCH = "schema_mismatch"
    FILE_MISMATCH = "file_mismatch"
    PARSER_VERSION_MISMATCH = "parser_version_mismatch"
    OVERRIDE_NOT_FOUND = "override_not_found"


class ImportRejected(Exception):
    """§18.3: відмова імпорту з конкретною причиною, без часткової зміни сесії."""

    def __init__(self, reason: ImportRejectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclasses.dataclass(frozen=True)
class UnmatchedRecord:
    """Запис із JSON, донор якого не знайдений у поточних запитах (§18.2, п.3)."""

    query_id: str
    donor_id: str
    payload: dict  # вихідний запис JSON, переноситься без втрати


@dataclasses.dataclass(frozen=True)
class ImportResult:
    states: dict[str, QueryState]  # query_id -> стан
    section_overrides: tuple[SectionOverride, ...]
    restored_count: int
    needs_review_count: int
    unmatched: tuple[UnmatchedRecord, ...]


def _reject(reason: ImportRejectReason) -> None:
    raise ImportRejected(reason)


def export_project(
    *,
    document: SearchDocument,
    result: SearchResult,
    states: dict[str, QueryState],
    app_version: str,
    file_name: str = "",
) -> dict:
    """
    §18.3: знімок поточної сесії триажу у словник, готовий до `json.dumps`.

    `unmatched` в експорті завжди порожній: це знімок живої сесії, а не
    накопичена історія попередніх імпортів (та живе лише в `ImportResult`
    останнього виклику `apply_project`, її перенесення між сесіями — поза
    межами цього модуля).

    `file_name` — суто інформаційне поле (`file.name` у форматі §18.3):
    ідентичність файлу при допуску перевіряється тільки за SHA-256
    (`validate_project` це поле не читає ніколи). За замовчуванням порожній
    рядок — екран кроку 15 передаватиме реальне ім'я завантаженого файлу.
    """
    queries_payload: dict[str, dict] = {}
    for query in result.queries:
        state = states.get(query.query_id) or initial_state(query.query_id)
        queries_payload[query.query_id] = {
            "donor_id": query.donor_id,
            "primary_channel": query.primary_channel.value,
            "subtype": query.subtype,
            "query_text": query.query_text,
            "parts": [
                {
                    "text": part.text,
                    "origin": part.origin.value,
                    "origin_id": part.origin_id,
                }
                for part in query.parts
            ],
            "status": state.status,
            "needs_review": state.needs_review,
            "previous_status": state.previous_status,
            "prior_snapshot": state.prior_snapshot,
            "comment": state.comment,
            "source_url": state.source_url,
            "found_engine": state.found_engine,
            "failed_engines": list(state.failed_engines),
        }

    overrides_payload = [
        {
            "action": override.action.value,
            "heading_block_id": override.heading_block_id,
            "section_kind": (
                override.section_kind.value if override.section_kind is not None else None
            ),
        }
        for override in document.applied_overrides
    ]

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "app_version": app_version,
        "parser_version": document.parser_version,
        "algo_version": result.algo_version,
        "dictionary_version": result.dictionary_version,
        "file": {"sha256": document.document_sha256, "name": file_name},
        "section_overrides": overrides_payload,
        "queries": queries_payload,
        "unmatched": [],
    }


def parse_project(raw: str | bytes) -> dict:
    """Розбір рядка/байтів JSON-проєкту. Синтаксична помилка -> `MALFORMED_JSON`."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ImportRejected(ImportRejectReason.MALFORMED_JSON) from None
    if not isinstance(payload, dict):
        raise ImportRejected(ImportRejectReason.MALFORMED_JSON)
    return payload


def validate_project(payload: dict, *, document: SearchDocument) -> None:
    """
    §18.3: усі перевірки допуску. Нічого не змінює, нічого не повертає.

    Порядок: точна `schema_version` -> той самий SHA-256 файлу -> точна
    `parser_version` -> кожен `heading_block_id` override існує в документі.
    `algo_version`/`dictionary_version`/`app_version` на допуск не впливають.
    """
    if "schema_version" not in payload:
        _reject(ImportRejectReason.SCHEMA_MISSING)
    if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
        _reject(ImportRejectReason.SCHEMA_MISMATCH)

    file_info = payload.get("file") or {}
    if file_info.get("sha256") != document.document_sha256:
        _reject(ImportRejectReason.FILE_MISMATCH)

    if payload.get("parser_version") != document.parser_version:
        _reject(ImportRejectReason.PARSER_VERSION_MISMATCH)

    known_block_ids = {block.block_id for block in document.blocks}
    for raw_override in payload.get("section_overrides") or ():
        heading_block_id = raw_override.get("heading_block_id")
        if heading_block_id not in known_block_ids:
            _reject(ImportRejectReason.OVERRIDE_NOT_FOUND)


def _state_from_record(query_id: str, record: dict) -> QueryState:
    """Точне відновлення стану при збігу `query_id` (§18.2, п.1)."""
    failed_engines = record.get("failed_engines") or ()
    return QueryState(
        query_id=query_id,
        status=record.get("status", "unchecked"),
        needs_review=bool(record.get("needs_review", False)),
        previous_status=record.get("previous_status"),
        prior_snapshot=record.get("prior_snapshot"),
        found_engine=record.get("found_engine"),
        source_url=record.get("source_url"),
        failed_engines=tuple(failed_engines),
        comment=record.get("comment", ""),
    )


def _migrated_state(query_id: str, old_record: dict) -> QueryState:
    """
    Міграція при зміненому запиті (§18.2, п.2): коментар і посилання
    зберігаються, попередній статус іде в `previous_status`, а компактний
    знімок попереднього рішення — у `prior_snapshot`. Новий статус завжди
    `unchecked`, `needs_review=True`; такий запис не вважається перевіреним.
    """
    old_status = old_record.get("status", "unchecked")
    old_query_text = old_record.get("query_text", "")
    snapshot = f"status={old_status}; query_text={old_query_text}"
    return QueryState(
        query_id=query_id,
        status="unchecked",
        needs_review=True,
        previous_status=old_status,
        prior_snapshot=snapshot,
        found_engine=None,
        source_url=old_record.get("source_url"),
        failed_engines=(),
        comment=old_record.get("comment", ""),
    )


def apply_project(
    payload: dict,
    *,
    document: SearchDocument,
    queries: Iterable[SearchQuery],
) -> ImportResult:
    """
    §18.2/§18.3: транзакційне застосування JSON-проєкту до поточних запитів.

    Спершу повна валідація допуску (`validate_project`); до її успішного
    проходження жоден стан не будується. Далі — зіставлення записів `queries`
    із переданими поточними запитами за правилами §18.2, у порядку: збіг
    `query_id` -> збіг `donor_id + primary_channel + subtype` при зміненому
    запиті (мітка `needs_review`) -> донор не знайдений (`unmatched`).

    `ImportResult.unmatched` — спершу записи, що не зіставилися саме в цьому
    імпорті (у порядку обходу `queries`), потім записи з верхньорівневого
    поля `unmatched` вхідного файлу (у їхньому вихідному порядку) — старі
    "не вдалося зіставити" не зникають при повторному імпорті. Елемент без
    `query_id`/`donor_id` не відкидається: замість відсутнього поля йде
    порожній рядок, а `payload` зберігається повністю (нічого не
    придушується мовчки, CLAUDE.md правило №3).
    """
    validate_project(payload, document=document)

    queries = list(queries)
    current_by_id: dict[str, SearchQuery] = {query.query_id: query for query in queries}
    current_by_key: dict[tuple, list[SearchQuery]] = {}
    for query in queries:
        key = (query.donor_id, query.primary_channel.value, query.subtype)
        current_by_key.setdefault(key, []).append(query)
    for candidates in current_by_key.values():
        candidates.sort(key=lambda query: query.query_id)

    records: dict[str, dict] = payload.get("queries") or {}

    states: dict[str, QueryState] = {}
    unmatched: list[UnmatchedRecord] = []
    claimed: set[str] = set()
    restored_count = 0
    pending: list[tuple[str, dict]] = []

    for json_query_id, record in records.items():
        current = current_by_id.get(json_query_id)
        if current is not None:
            states[json_query_id] = _state_from_record(json_query_id, record)
            claimed.add(json_query_id)
            restored_count += 1
        else:
            pending.append((json_query_id, record))

    for json_query_id, record in pending:
        key = (record.get("donor_id"), record.get("primary_channel"), record.get("subtype"))
        candidates = current_by_key.get(key, ())
        matched = next((c for c in candidates if c.query_id not in claimed), None)
        if matched is not None:
            states[matched.query_id] = _migrated_state(matched.query_id, record)
            claimed.add(matched.query_id)
        else:
            unmatched.append(
                UnmatchedRecord(
                    query_id=json_query_id,
                    donor_id=record.get("donor_id", ""),
                    payload=record,
                )
            )

    for carried in payload.get("unmatched") or ():
        unmatched.append(
            UnmatchedRecord(
                query_id=carried.get("query_id", ""),
                donor_id=carried.get("donor_id", ""),
                payload=carried.get("payload", {}),
            )
        )

    needs_review_count = sum(1 for state in states.values() if state.needs_review)

    overrides = tuple(
        SectionOverride(
            action=SectionOverrideAction(raw_override["action"]),
            heading_block_id=raw_override["heading_block_id"],
            section_kind=(
                SectionKind(raw_override["section_kind"])
                if raw_override.get("section_kind")
                else None
            ),
        )
        for raw_override in payload.get("section_overrides") or ()
    )

    return ImportResult(
        states=states,
        section_overrides=overrides,
        restored_count=restored_count,
        needs_review_count=needs_review_count,
        unmatched=tuple(unmatched),
    )
