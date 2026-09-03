"""
search/presentation.py
Чисте форматування карток, лічильників і зведень екрана ?mode=search без
звернень до Streamlit. Специфікація — PLAN_SEARCH.md, §§17–19.

HTML формується лише з уже нарізаних фрагментів вихідного тексту: кожний
фрагмент екранується окремо, після чого довірені теги `<mark>` додаються між
ними. Посилання рушіїв завжди проходять через єдине рішення `engines.py`.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from search.calques import (
    collapse_components,
    density_band,
    find_calques,
    rule_by_id,
    section_is_locally_dense,
)
from search.engines import resolve_engine_link
from search.language import bibliography_language_stats
from search.metadata import DissertationMetadata
from search.normalization import map_normalized_offsets, normalize_text
from search.state import QueryState, is_counted_as_checked
from search.types import (
    CONTENT_SECTION_KINDS,
    Channel,
    EngineSpec,
    SearchDocument,
    SearchQuery,
    SearchResult,
    ShortfallReason,
    TextZone,
)


STATUS_LABELS: dict[str, str] = {
    "unchecked": "не перевірено",
    "no_result": "нічого не знайдено",
    "found": "знайдено",
}

CHANNEL_LABELS: dict[Channel, str] = {
    Channel.A: "Авторське положення",
    Channel.N: "Наукова новизна",
    Channel.B: "Емпіричні дані",
    Channel.K: "Ознаки перекладу",
    Channel.T: "Рідкісна словоформа",
    Channel.L: "Довге змістовне речення",
    Channel.D: "Нормативні посилання",
}

CALQUE_LEVEL_LABELS: dict[int, str] = {
    1: "надійна ознака",
    2: "допоміжна ознака",
    3: "контекстна ознака",
}

CALQUE_SUBTYPE_LABELS: dict[str, str] = {
    "K1": "точна українська фраза",
    "K2": "зворотний пошук російською",
    "K3": "пошук визначення",
}

TEXT_ZONE_LABELS: dict[TextZone, str] = {
    TextZone.AUTHOR_TEXT: "авторський текст",
    TextZone.QUOTED_TEXT: "цитований текст",
    TextZone.FOOTNOTE_TEXT: "текст приміток і виносок",
    TextZone.BIBLIOGRAPHY: "список використаних джерел",
    TextZone.TOC: "зміст",
    TextZone.HEADER_FOOTER: "колонтитули",
    TextZone.UNCERTAIN: "текст із невизначеним типом",
}

_REJECTION_REASON_LABELS = {
    "diversity_limit": "обмеження різноманітності запитів",
    "k_no_buildable_subtype": "бракує даних для пошукового запиту за ознакою перекладу",
    "l_duplicate_of_base": "фрагмент повторює вже створений запит",
    "l_subsection_already_covered": "підрозділ уже представлено іншим запитом",
    "no_valid_windows": "немає придатних фрагментів для запиту",
    "score_below_threshold_4": "недостатньо змістовних ознак",
    "section_not_content_kind": "службовий або незмістовний розділ",
    "section_unknown": "тип розділу не визначено",
    "section_unresolved": "межі розділу не визначено",
}

_PREFILL_REASON_LABELS = {
    "not_verified": "адресу пошуку ще не перевірено вручну",
    "stale_verification": "строк ручної перевірки адреси минув",
    "no_template": "рушій не має перевіреної адреси запиту",
    "query_too_long": "запит довший за ліміт рушія",
    "prefill_disabled": "попереднє заповнення вимкнено",
    "empty_query": "порожній запит",
}

_SHORTFALL_LABELS = {
    ShortfallReason.SECTION_UNRESOLVED: "межі розділу не визначено",
    ShortfallReason.NO_EXTRACTABLE_BODY: "немає придатного авторського тексту",
    ShortfallReason.NO_VALID_WINDOWS: "немає придатних пошукових вікон",
    ShortfallReason.INSUFFICIENT_QUALITY: "недостатньо якісних кандидатів",
    ShortfallReason.DEDUPLICATION_REDUCED: "кандидати об'єднано як дублікати",
    ShortfallReason.DIVERSITY_LIMITS: "спрацювали ліміти різноманітності",
    ShortfallReason.PARTIAL_COVERAGE: "неповне текстове покриття",
    ShortfallReason.NORMATIVE_HEAVY: "переважно нормативний текст",
}


def channel_label(channel: Channel) -> str:
    """Людська назва типу запиту без внутрішньої літерної абревіатури."""

    return CHANNEL_LABELS[channel]


def rejection_reason_label(reason: str) -> str:
    """Перетворити внутрішній код відсіву на пояснення для експерта."""

    threshold_prefix = "score_below_threshold_2:"
    if reason.startswith(threshold_prefix):
        channel_code = reason[len(threshold_prefix):]
        try:
            label = channel_label(Channel(channel_code)).lower()
        except ValueError:
            return "недостатньо ознак для створення запиту"
        return f"недостатньо ознак: {label}"
    return _REJECTION_REASON_LABELS.get(reason, "інша технічна причина відсіву")


@dataclass(frozen=True)
class CopyFieldView:
    label: str
    text: str


@dataclass(frozen=True)
class EngineLinkView:
    engine_code: str
    label: str
    url: str | None
    target_url: str
    home_url: str
    is_prefilled: bool
    action_label: str
    warning: str | None
    block_reason: str | None
    block_reason_label: str | None
    copy_query: str


@dataclass(frozen=True)
class AssistantLinkView:
    label: str
    url: str | None


@dataclass(frozen=True)
class CalqueIndicatorView:
    rule_id: str
    tier: int
    matched_text: str | None
    normative_text: str
    label: str


@dataclass(frozen=True)
class StatusActionView:
    code: str
    label: str
    selected: bool


@dataclass(frozen=True)
class QueryCardView:
    query_id: str
    channel_label: str
    attributed_channel_labels: tuple[str, ...]
    subtype_label: str | None
    query_text: str
    page_label: str
    donor_text: str
    donor_html: str
    block_text: str | None
    block_html: str | None
    anchor_text: str
    status_label: str
    engine_links: tuple[EngineLinkView, ...]
    calque_indicators: tuple[CalqueIndicatorView, ...]
    ru_reference_reason: str | None
    needs_review: bool
    needs_review_message: str | None
    previous_status_label: str | None
    prior_snapshot: str | None
    found_engine: str | None
    source_url: str | None
    comment: str
    failed_engines: tuple[str, ...]
    copy_fields: tuple[CopyFieldView, ...]
    status_actions: tuple[StatusActionView, ...]
    assistant_links: tuple[AssistantLinkView, ...] = ()


@dataclass(frozen=True)
class ChannelUsefulnessView:
    channel: Channel
    found: int
    checked: int
    hit_rate: float | None
    hit_rate_label: str


@dataclass(frozen=True)
class EngineFailureView:
    engine_code: str
    label: str
    count: int


@dataclass(frozen=True)
class ShortfallReasonView:
    reason: ShortfallReason
    label: str
    primary_count: int
    contributing_count: int


@dataclass(frozen=True)
class CalqueSummaryView:
    author_words: int
    tier1_hits: int
    tier2_hits: int
    tier3_hits: int
    tier1_density: float
    band: str
    excluded_zone_hits: tuple[tuple[str, int], ...]
    notice: str | None


@dataclass(frozen=True)
class BibliographySummaryView:
    total: int
    ru: int
    uk: int
    mixed: int
    unknown: int
    expected_count: int | None
    coverage_label: str
    ru_percentage_label: str
    show_ru_percentage: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SectionCalqueSummaryView:
    section_id: str
    heading: str
    tier1_hits: int
    tier2_hits: int
    tier3_hits: int
    density: float
    locally_dense: bool


@dataclass(frozen=True)
class SearchSummaryView:
    n_pages: int
    expected_body_pages: int
    extractable_body_pages: int
    coverage_label: str
    query_count: int
    calques: CalqueSummaryView
    bibliography: BibliographySummaryView
    section_calques: tuple[SectionCalqueSummaryView, ...]
    channel_usefulness: tuple[ChannelUsefulnessView, ...]
    engine_failures: tuple[EngineFailureView, ...]
    shortfall_section_count: int
    shortfall_reasons: tuple[ShortfallReasonView, ...]
    rejected_by_reason: tuple[tuple[str, int], ...]
    generated_by_channel: tuple[tuple[Channel, int], ...]
    retained_primary_by_channel: tuple[tuple[Channel, int], ...]
    attributed_by_channel: tuple[tuple[Channel, int], ...]
    warnings: tuple[str, ...]


def render_highlighted_text(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    """Екранувати сирий текст після нормалізації та об'єднання інтервалів."""

    cleaned = sorted(
        (max(0, start), min(len(text), end))
        for start, end in spans
        if min(len(text), end) > max(0, start)
    )
    merged: list[tuple[int, int]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(html.escape(text[cursor:start], quote=True))
        pieces.append(f"<mark>{html.escape(text[start:end], quote=True)}</mark>")
        cursor = end
    pieces.append(html.escape(text[cursor:], quote=True))
    return "".join(pieces)


def _matched_calque_text(query: SearchQuery, rule_id: str) -> tuple[str | None, tuple[int, int] | None]:
    for part in query.parts:
        if part.origin.value != "calque_rule" or part.origin_id != rule_id or part.source is None:
            continue
        for raw_span in part.source.parts:
            for donor_span in query.donor_source.parts:
                if raw_span.block_id != donor_span.block_id:
                    continue
                start = raw_span.raw_start - donor_span.raw_start
                end = raw_span.raw_end - donor_span.raw_start
                if 0 <= start < end <= len(query.donor_text):
                    return query.donor_text[start:end], (start, end)

    rule = rule_by_id(rule_id)
    normalized = normalize_text(query.donor_text)
    match = re.search(rule.pattern, normalized.text, re.IGNORECASE | re.UNICODE)
    if match is None:
        return None, None
    offsets = map_normalized_offsets(normalized, match.start(), match.end())
    start, end = offsets[0][0], offsets[-1][1]
    return query.donor_text[start:end], (start, end)


def _calque_views(query: SearchQuery) -> tuple[tuple[CalqueIndicatorView, ...], tuple[tuple[int, int], ...]]:
    views: list[CalqueIndicatorView] = []
    spans: list[tuple[int, int]] = []
    rule_ids = sorted({reason[2:] for reason in query.reasons if reason.startswith("K.")})
    for rule_id in rule_ids:
        rule = rule_by_id(rule_id)
        if rule.tier is None:
            continue
        matched, span = _matched_calque_text(query, rule_id)
        if span is not None:
            spans.append(span)
        views.append(CalqueIndicatorView(
            rule_id=rule_id,
            tier=rule.tier,
            matched_text=matched,
            normative_text=rule.uk_norm,
            label=(
                f"{CALQUE_LEVEL_LABELS[rule.tier]}: {matched} → {rule.uk_norm}"
                if matched is not None
                else f"{CALQUE_LEVEL_LABELS[rule.tier]}: пов'язаний маркер → {rule.uk_norm}"
            ),
        ))
    return tuple(views), tuple(spans)


def _ru_reference_reason(query: SearchQuery, document: SearchDocument | None) -> str | None:
    ru_parts = tuple(part for part in query.parts if part.origin.value == "ru_reference")
    if ru_parts:
        if document is not None:
            by_id = {entry.entry_id: entry for entry in document.bibliography}
            ordinals = tuple(
                by_id[part.origin_id].ordinal
                for part in ru_parts
                if part.origin_id in by_id and by_id[part.origin_id].ordinal is not None
            )
            if ordinals:
                labels = ", ".join(f"[{ordinal}]" for ordinal in dict.fromkeys(ordinals))
                return f"Джерело {labels} процитовано в цьому реченні."
        return "Російськомовне джерело пов'язане з цим реченням."
    if any(part.origin.value == "surname_transliteration" for part in query.parts):
        return "Прізвище взято з донорського речення та транслітеровано."
    return None


def _assistant_links(
    paragraph: str | None, *, has_calque: bool, metadata: DissertationMetadata | None = None,
) -> tuple[AssistantLinkView, ...]:
    """Передати повний сирий абзац у промпт без лімітів короткого запиту."""

    metadata = metadata or DissertationMetadata()
    instruction = (
        "Найди возможный русскоязычный оригинал этого украинского текста. "
        "Ищи дословные, переведенные и слегка перефразированные совпадения "
        "во всех типах источников. Покажи совпадающие фрагменты и дай ссылки."
        if has_calque else
        "Знайди можливе джерело цього українського тексту. "
        "Шукай дослівні та злегка перефразовані збіги у всіх типах джерел. "
        "Виключи роботу, що перевіряється, її автореферат, копії, а також публікації цього ж автора.\n"
        f"Автор: {metadata.author or 'не вказано'}\n"
        f"Дисертація: {metadata.title or 'не вказано'}\n"
        f"Рік роботи: {metadata.year or 'не вказано'}\n"
        "Шукай більш ранні незалежні джерела. Покажи фрагменти, що збігаються, дати та посилання."
    )
    encoded = (
        quote(f"{instruction}\n\n{paragraph}", safe="")
        if paragraph and paragraph.strip() else None
    )
    return tuple(
        AssistantLinkView(label, f"{base_url}{encoded}" if encoded is not None else None)
        for label, base_url in (
            ("ChatGPT", "https://chatgpt.com/?q="),
            ("Perplexity", "https://www.perplexity.ai/search?q="),
        )
    )


def build_query_card(
    query: SearchQuery,
    state: QueryState,
    engines: tuple[EngineSpec, ...],
    today: date,
    *,
    document: SearchDocument | None = None,
    metadata: DissertationMetadata | None = None,
) -> QueryCardView:
    """Побудувати повну чисту модель картки з доказами та діями (§17)."""

    engine_links: list[EngineLinkView] = []
    for engine in engines:
        if query.primary_channel not in engine.channels:
            continue
        resolved = resolve_engine_link(engine, query.query_text, today=today)
        reason = resolved.block_reason.value if resolved.block_reason is not None else None
        engine_links.append(EngineLinkView(
            engine_code=engine.code,
            label=engine.label,
            url=resolved.url if resolved.is_prefilled else None,
            target_url=resolved.url,
            home_url=engine.home_url,
            is_prefilled=resolved.is_prefilled,
            action_label=engine.label if resolved.is_prefilled else f"{engine.label} · відкрити сайт",
            warning=resolved.warning,
            block_reason=reason,
            block_reason_label=_PREFILL_REASON_LABELS.get(reason) if reason else None,
            copy_query=resolved.query_text,
        ))

    indicators, spans = _calque_views(query)
    previous_label = STATUS_LABELS.get(state.previous_status) if state.previous_status else None
    block_text = None
    block_html = None
    if document is not None:
        block = next((item for item in document.blocks if item.block_id == query.block_id), None)
        if block is not None:
            block_text = block.raw_text
            donor_base = next(
                (
                    part.raw_start
                    for part in query.donor_source.parts
                    if part.block_id == query.block_id
                ),
                0,
            )
            block_html = render_highlighted_text(
                block.raw_text,
                tuple((donor_base + start, donor_base + end) for start, end in spans),
            )
    return QueryCardView(
        query_id=query.query_id,
        channel_label=channel_label(query.primary_channel),
        attributed_channel_labels=tuple(
            channel_label(channel) for channel in query.attributed_channels
        ),
        subtype_label=CALQUE_SUBTYPE_LABELS.get(query.subtype, query.subtype),
        query_text=query.query_text,
        page_label=f"Аркуш PDF {query.physical_page}",
        donor_text=query.donor_text,
        donor_html=render_highlighted_text(query.donor_text, spans),
        block_text=block_text,
        block_html=block_html,
        anchor_text=query.pdf_anchor,
        status_label=STATUS_LABELS[state.status],
        engine_links=tuple(engine_links),
        calque_indicators=indicators,
        ru_reference_reason=_ru_reference_reason(query, document),
        needs_review=state.needs_review,
        needs_review_message=(
            "Запит змінився після попередньої перевірки; рішення треба підтвердити знову."
            if state.needs_review else None
        ),
        previous_status_label=previous_label,
        prior_snapshot=state.prior_snapshot,
        found_engine=state.found_engine,
        source_url=state.source_url,
        comment=state.comment,
        failed_engines=state.failed_engines,
        copy_fields=(
            CopyFieldView("Запит", query.query_text),
        ),
        status_actions=tuple(
            StatusActionView(code, label, code == state.status)
            for code, label in STATUS_LABELS.items()
        ),
        assistant_links=_assistant_links(block_text, has_calque=bool(indicators), metadata=metadata),
    )


def _percent_label(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def build_search_summary(
    result: SearchResult,
    states: dict[str, QueryState],
    engines: tuple[EngineSpec, ...],
) -> SearchSummaryView:
    """Звести K, мови, охоплення, недобір і ручну корисність (§§17, 19)."""

    channel_views: list[ChannelUsefulnessView] = []
    for channel in (Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L):
        channel_states = tuple(
            states.get(query.query_id, QueryState(query.query_id))
            for query in result.queries
            if query.primary_channel == channel
        )
        checked = sum(is_counted_as_checked(state) for state in channel_states)
        found = sum(
            state.status == "found" and not state.needs_review for state in channel_states
        )
        hit_rate = found / checked if checked else None
        channel_views.append(ChannelUsefulnessView(
            channel=channel,
            found=found,
            checked=checked,
            hit_rate=hit_rate,
            hit_rate_label=_percent_label(hit_rate),
        ))

    current_ids = {query.query_id for query in result.queries}
    engine_failures = tuple(EngineFailureView(
        engine_code=engine.code,
        label=engine.label,
        count=sum(
            engine.code in state.failed_engines
            for query_id, state in states.items()
            if query_id in current_ids
        ),
    ) for engine in engines)

    shortfall_views = tuple(ShortfallReasonView(
        reason=reason,
        label=_SHORTFALL_LABELS[reason],
        primary_count=sum(item.primary_reason == reason for item in result.shortfalls),
        contributing_count=sum(reason in item.contributing_reasons for item in result.shortfalls),
    ) for reason in ShortfallReason)

    calques = result.calque_metrics
    band = density_band(calques.tier1_density)
    calque_view = CalqueSummaryView(
        author_words=calques.author_words,
        tier1_hits=calques.tier1_hits,
        tier2_hits=calques.tier2_hits,
        tier3_hits=calques.tier3_hits,
        tier1_density=calques.tier1_density,
        band=band,
        excluded_zone_hits=tuple(
            (TEXT_ZONE_LABELS[zone], count) for zone, count in calques.excluded_zone_hits
        ),
        notice=(
            "Це ознака перекладу, а не доказ запозичення. Перевірте російськомовні джерела."
            if band == "prominent" else None
        ),
    )

    language = bibliography_language_stats(
        result.document.bibliography, result.document.body_biblio_confidence
    )
    bibliography_view = BibliographySummaryView(
        total=language.total,
        ru=language.ru,
        uk=language.uk,
        mixed=language.mixed,
        unknown=language.unknown,
        expected_count=language.expected_count,
        coverage_label=_percent_label(language.coverage_ratio),
        ru_percentage_label=(
            _percent_label(language.ru_ratio) if language.show_ru_percentage else "—"
        ),
        show_ru_percentage=language.show_ru_percentage,
        reasons=language.reasons,
    )

    section_calques: list[SectionCalqueSummaryView] = []
    if result.section_calque_metrics:
        metrics_by_section = {
            item.section_id: item for item in result.section_calque_metrics
        }
        for section in result.document.sections:
            if section.kind not in CONTENT_SECTION_KINDS:
                continue
            metrics = metrics_by_section[section.section_id]
            section_calques.append(SectionCalqueSummaryView(
                section_id=section.section_id,
                heading=section.heading,
                tier1_hits=metrics.tier1_hits,
                tier2_hits=metrics.tier2_hits,
                tier3_hits=metrics.tier3_hits,
                density=metrics.density,
                locally_dense=metrics.locally_dense,
            ))
    else:
        for section in result.document.sections:
            if section.kind not in CONTENT_SECTION_KINDS:
                continue
            hits = tuple(
                hit
                for block in result.document.blocks
                if block.section_id == section.section_id
                for hit in collapse_components(find_calques(block))
                if hit.zone == TextZone.AUTHOR_TEXT
            )
            by_tier = {tier: sum(hit.tier == tier for hit in hits) for tier in (1, 2, 3)}
            section_density = 1000 * by_tier[1] / section.author_words if section.author_words else 0.0
            section_calques.append(SectionCalqueSummaryView(
                section_id=section.section_id,
                heading=section.heading,
                tier1_hits=by_tier[1],
                tier2_hits=by_tier[2],
                tier3_hits=by_tier[3],
                density=section_density,
                locally_dense=section_is_locally_dense(
                    section.author_words, by_tier[1], section_density
                ),
            ))

    return SearchSummaryView(
        n_pages=result.document.n_pages,
        expected_body_pages=result.document.expected_body_pages,
        extractable_body_pages=result.document.extractable_body_pages,
        coverage_label=_percent_label(result.document.coverage_ratio),
        query_count=len(result.queries),
        calques=calque_view,
        bibliography=bibliography_view,
        section_calques=tuple(section_calques),
        channel_usefulness=tuple(channel_views),
        engine_failures=engine_failures,
        shortfall_section_count=len(result.shortfalls),
        shortfall_reasons=shortfall_views,
        rejected_by_reason=tuple(
            (rejection_reason_label(reason), count)
            for reason, count in result.candidate_metrics.rejected_by_reason
        ),
        generated_by_channel=result.candidate_metrics.generated_by_channel,
        retained_primary_by_channel=result.candidate_metrics.retained_primary_by_channel,
        attributed_by_channel=result.candidate_metrics.attributed_by_channel,
        warnings=result.warnings,
    )
