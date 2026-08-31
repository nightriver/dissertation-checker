# План безопасной оптимизации режима ручного поиска

Статус: готов к реализации в отдельной сессии.

Эталон реализации: commit `f94dc16fcb8c4b908cda9989bc9b08e124eb2015`
на ветке `main`.

Источник измерений: локальный отчёт `REVIEW_SEARCH_PERF.md`. Отчёт нужен для
обоснования приоритетов, но исполнителю не требуется открывать его: все
необходимые числа и выводы перенесены в этот документ.

## 1. Как работать по этому плану

Этот файл самодостаточен. Для реализации оптимизаций читать `PLAN_SEARCH.md`,
`PLAN_SEARCH_JOURNAL.md` и старые пакеты `steps/step-*.md` **не нужно**.

Перед началом исполнитель читает только:

1. `CLAUDE.md` — правила репозитория;
2. этот файл;
3. исходники и тесты, прямо перечисленные в выполняемом этапе.

Этапы выполняются строго по порядку. После каждого этапа результат режима
поиска должен остаться идентичным эталону. Если машинный golden, порядок
запросов, provenance, shortfall или метрики изменились, исполнитель не
обновляет фикстуры и не «подгоняет» ожидания, а останавливается и сообщает:

- этап и последнюю правку;
- первый документ с расхождением;
- путь JSON до первого отличия;
- старое и новое значение;
- предполагаемую причину.

Использовать только `.venv312\Scripts\python.exe`. Не менять и не удалять
пользовательский `out.txt`. Не выполнять `checkout`, `reset`, `clean` или
другие операции, способные уничтожить локальные данные. Не трогать
`examples/`, `.venv*`, `tmp/`, ручные ожидания
`tests/fixtures/search_corpus_expectations.json` и пользовательский отчёт
`REVIEW_SEARCH_PERF.md`.

Коммиты делает главный исполнитель/оркестратор. Сабагентам запрещено делать
`git push`. Публикация выполняется только по отдельной команде пользователя.

## 2. Что делает оптимизируемая функция

Режим Streamlit `?mode=search` принимает PDF до 30 МиБ с текстовым слоем и
готовит запросы для **ручного** поиска источников. Он не обходит поисковую
выдачу, не определяет плагиат и не использует LLM.

Путь данных:

```text
PDF bytes
  -> parser/searchdoc.py:parse_search_document
  -> SearchDocument
  -> search/query_builder.py:build_search_result
  -> SearchResult
  -> search/ui_logic.py:build_search_screen
  -> app.py:render_manual_search_page
```

Запросы строятся по каналам `A/N/B/K/T/L`. Канал `D` зарезервирован и не
порождается. Каждый запрос содержит исходное предложение, координаты,
`QueryPart` с происхождением, PDF-anchor и стабильный `query_id`. Если раздел
не набирает 10 запросов, создаётся честный `SectionShortfall`; заглушки
запрещены.

Состояние ручного триажа экспортируется в JSON. Импорт требует совпадения SHA
PDF и версии парсера. При изменении алгоритма несовпавшие запросы получают
`needs_review`.

## 3. Неприкосновенные инварианты

Любая оптимизация обязана сохранить одновременно:

1. те же `SearchDocument`, `SearchResult` и порядок элементов;
2. те же 639 запросов на эталонном корпусе из девяти PDF;
3. те же `query_id`, `donor_id`, `QueryPart`, `SourceSpan` и PDF-anchor;
4. те же `SignalHit`, причины отсева и диагностические счётчики;
5. те же `SectionShortfall` и квоты 10–12;
6. те же calque/bibliography/language/candidate/dedup metrics;
7. отсутствие канала `D`;
8. отсутствие придуманных содержательных слов;
9. детерминизм второго `build_search_result(document)`;
10. тот же JSON import/export и поведение `needs_review`;
11. публичный интерфейс `parser/*`;
12. порог покрытия 90% и стандартную команду полного pytest.

Чистая оптимизация не меняет `PARSER_VERSION`, `ALGO_VERSION`, `DICT_VERSION`
или schema version. Если для реализации требуется изменить результат или
версию, это выход за объём плана и повод остановиться.

## 4. Исходный профиль

Замеры выполнены на Windows 11, Python из `.venv312`, один процесс, без
coverage.

| Фаза на девяти PDF | Время |
|---|---:|
| Парсер | 31,8 с |
| Построение запросов | 1073,1 с |
| Дополнительные метрики | 17,2 с |

Один query-проход занимает примерно 17,9 минуты. Текущий полный набор делает
три query-прохода и четыре parser-прохода. С coverage полный pytest занимал
2:10:37 и завершился результатом `1024 passed`, coverage `95,76%`.

Основные горячие места:

- повторное построение индекса доноров для каждой цитаты — до 42% query-фазы;
- полная нормализация каждого окна — около 39%;
- повторное вычисление имён и признаков токенов — около 15,5 с и миллионы
  вызовов;
- отдельные одинаковые corpus-сборки gate 16 и gate 17;
- повторный поиск калек при каждом Streamlit rerun — 0,5–2 с на клик;
- 403 639 объектов `CharOrigin` дают около 98,6 МиБ живых объектов уже на
  PDF размером 0,5 МиБ.

## 5. Общий протокол проверки

### 5.1. Перед первой правкой

Снять строгий машинный эталон по `CORPUS_FILES` из
`tools/audit_search_golden.py` и сохранить его во временный файл **вне
репозитория**. В эталон входят:

- canonical JSON от `build_golden_payload(collect_golden(paths))`;
- SHA-256 canonical JSON;
- списки `query_id` по документам;
- shortfalls;
- signal/candidate/dedup/calque metrics.

Сравнение после этапов должно быть побайтовым. Допуск `1e-6` из текущего gate
не применяется к оптимизационному доказательству.

### 5.2. После каждого этапа

1. Запустить точечные тесты изменённых модулей с `--no-cov`.
2. Запустить `tests/test_project_rules.py --no-cov`.
3. Воспроизвести машинный golden на всех девяти PDF.
4. Побайтово сравнить его с эталоном.
5. Замерить хотя бы `dis2005_bayar_kandidat.PDF` тем же способом, что до
   правки.
6. Проверить `git diff`; golden и ручные фикстуры не должны меняться.

Полный pytest не запускается после каждого этапа: до оптимизации это стоит
более двух часов. Два полных запуска обязательны после последней правки.

---

# Этап 1. Усилить provenance системных литералов

## Цель

Закрыть единственную найденную возможность провести произвольный текст как
`SYSTEM_LITERAL` до рефакторинга горячего пути. Текущие продуктовые запросы
не меняются.

## Читать

- `search/query_builder.py`: `compose_query_parts`,
  `validate_query_parts`, `_phrase_parts`, `_space_join_parts`,
  `_build_k3_query`;
- `tests/test_search_query_builder_internals.py`;
- `tests/test_gate_step_10.py` только для понимания существующего контракта;
- `tests/test_project_rules.py`.

## Изменить

- `search/query_builder.py`;
- `tests/test_search_query_builder_internals.py` или новый обычный unit-тест;
- `tests/test_project_rules.py`, если правило можно проверить механически без
  дублирования всей логики валидатора.

Gate-файлы этого этапа не редактировать.

## Точная реализация

Ввести внутреннее разрешённое соответствие `origin_id -> text`:

| `origin_id` | Разрешённый текст |
|---|---|
| `quote_open`, `open` | `«` |
| `quote_close`, `close` | `»` |
| `quote_close_space` | `» ` |
| `space` | один пробел |
| `space_<целое N>` | один пробел |
| `definition_literal` | `определение` |

Алиасы `open` и `close` нужны для обратной совместимости существующего gate
10. Любой другой ID или несовпадающий текст отклоняется.

`validate_query_parts()` по-прежнему обязан проверять композицию, длину,
наличие provenance и source. Новая проверка добавляется только к ветке
`SYSTEM_LITERAL`.

## Тесты этапа

1. Все строки из таблицы принимаются только с правильным текстом.
2. `QueryPart("вигадане", SYSTEM_LITERAL, "quote_open", None)` отклоняется.
3. Неизвестный `origin_id` отклоняется.
4. `space_1`, `space_2` принимаются; `space_x` отклоняется.
5. Существующие K2/K3 и quoted A/N/T проходят без изменений.

## Стоп-условия

Остановиться, если существующий продукт создаёт иной системный литерал, не
перечисленный выше. Не расширять список догадкой — сначала сообщить точный
call site.

---

# Этап 2. Один индекс цитат и доноров на документ

## Цель

Убрать повторный обход и сортировку всех предложений документа для каждой
цитаты, а также повторный линейный поиск блока. Результат связи цитат с
донорами должен остаться идентичным.

## Читать

- `search/bibliography.py`: `_donors_by_block`, `_block_brackets`,
  `_linked_donors`, `donor_ids_for_mention`;
- `search/query_builder.py`: `build_k_queries`, `_linked_ru_entries`,
  `build_search_result_with_candidates`;
- `tests/test_search_bibliography.py`;
- `tests/test_gate_step_06.py` только как контракт;
- `tests/test_gate_step_10.py` только как контракт.

## Изменить

- `search/bibliography.py`;
- `search/query_builder.py`;
- обычные unit-тесты библиографии и query builder.

Публичный интерфейс `parser/*` не менять. Gate-файлы не редактировать.

## Точная реализация

1. В `search/bibliography.py` создать внутренний индекс документа:
   - `block_by_id`;
   - `donors_by_block`;
   - `brackets_by_block`;
   - `donor_ids_by_citation_id`.
2. Каждый блок, список доноров и список brackets строить ровно один раз.
3. Существующий `donor_ids_for_mention(document, mention)` оставить
   совместимой обёрткой. Основной pipeline не должен вызывать эту обёртку в
   цикле по каждому донору.
4. В `query_builder.py` один раз построить:
   - RU bibliography entries по `entry_id`;
   - связанные `(entry, confidence, distance)` по `donor_id`.
5. `build_k_queries` получает предрассчитанные связи через необязательный
   keyword-only аргумент. При отсутствии аргумента используется старый путь,
   чтобы прямые unit-тесты и внешние вызовы сохранили совместимость.
6. `build_search_result_with_candidates` всегда передаёт новый индекс.

## Что нельзя изменить

- порядок `document.citations`;
- `seen` по `entry_id` для конкретного донора;
- вычисление `donor_mid` и расстояния;
- сортировку: confidence, title confidence, distance, `entry_id`;
- правила numeric/surname mentions;
- `citation_id` и `donor_id`;
- диагностические причины.

Словари используются только для доступа. Итоговые кортежи сортируются теми
же ключами, что сейчас; порядок вставки dict не становится новым контрактом.

## Тесты этапа

1. Новый индекс и старая обёртка дают те же donor IDs для numeric и surname
   mention.
2. Несуществующий block ID даёт пустой кортеж.
3. Две citations одного entry не создают дубль RU entry у донора.
4. Порядок связей совпадает при разных confidence и distance.
5. Инструментированный тест подтверждает один вызов построения донорского
   индекса на один `build_search_result`.

## Ожидаемый эффект

Существенное сокращение главного горячего участка. На контрольном PDF
сравнить число вызовов `_donors_by_block`: оно должно зависеть от числа
документов, а не от числа упоминаний.

---

# Этап 3. Контекст оценки окон одного предложения

## Цель

Не пересчитывать имена собственные и свойства одного токена для каждого
окна и каждого канала одного донора.

## Читать

- `search/query_builder.py`: `build_source_channel_query`, `_score_window`,
  `_select_best_window`, `_select_best_unanchored_window`,
  `_proper_name_indexes`, `_is_number_token`, `_is_meaningful_number`,
  `_is_rare_form_token`, `_is_long_content_word`, `_make_query`,
  `_number_parts`;
- `tests/test_search_query_builder_internals.py`;
- `tests/test_gate_step_10.py` только как контракт.

## Изменить

- `search/query_builder.py`;
- обычные unit-тесты query builder.

## Точная реализация

Создать внутренний `_DonorQueryContext`, который строится один раз для
`SentenceDonor` и содержит:

- результат `normalize_text(donor.raw_text)`;
- `word_tokens`;
- `proper_name_indexes`;
- по одному флагу на токен: meaningful number, rare form, long content word;
- числовой бонус каждого токена;
- prefix sum токенных бонусов;
- извлечённые surname evidence;
- number parts;
- кэш нормативного штрафа по ключу `(start_idx, end_idx)`.

Для окна `[start, end)` сумма токенных бонусов вычисляется как разность двух
элементов prefix sum. Нормативный штраф на этом этапе считается старой
`normative_marker_ids(window_text)`, но не более одного раза для одинакового
окна одного донора.

Контекст создаётся в `build_search_result_with_candidates` перед генерацией
каналов конкретного донора и передаётся во все A/N/B/K/T/L builders.

Существующие функции `_score_window`, `_select_best_window`,
`_select_best_unanchored_window`, `build_source_channel_query` и
`build_k_queries` остаются совместимыми обёртками. Если контекст не передан,
обёртка создаёт его сама.

## Арифметика и tie-break

Порядок сложения бонусов сохранить. Не переходить на целые числа, Decimal,
NumPy или иной тип. Выбор лучшего окна остаётся:

1. больший score;
2. меньше слов;
3. более раннее окно.

Не менять `WINDOW_MIN_WORDS`, `WINDOW_MAX_WORDS`, бонусы, штрафы или предел
длины запроса.

## Тесты этапа

1. Старый wrapper и context-path дают одинаковый score каждого тестового
   окна.
2. Проверить отдельно proper name, number, rare form, long word и normative
   penalty.
3. Проверить tie: короче, затем раньше.
4. Инструментированный тест: `_proper_name_indexes` вызывается один раз на
   контекст, а не один раз на окно.
5. Два последовательных построения дают полностью равные результаты.

## Стоп-условия

Остановиться при любом изменении float score или winner window. Не менять
golden и не считать расхождение допустимым из-за одинакового query text:
`rank_score` также часть результата.

---

# Этап 4. Безопасная нормализация без лишних origins

## Цель

Сократить стоимость нормализации нормативных окон, сохранив точный текст и
карту координат там, где она нужна.

## Читать

- весь `search/normalization.py`;
- `search/markers.py`: `normative_marker_ids`;
- `tests/test_search_normalization.py`;
- `tests/test_search_markers.py`;
- `tests/test_gate_step_04.py` только как контракт.

## Изменить

- `search/normalization.py`;
- `search/markers.py`;
- обычные unit-тесты normalization/markers.

## Подэтап 4A. Быстрый путь полного `normalize_text`

Быстрый путь разрешён, только если одновременно:

- `unicodedata.is_normalized("NFKC", raw_text)` истинно;
- нет `SOFT_HYPHEN`;
- нет ни одного символа из `_APOSTROPHE_CHARS`.

В этом случае первичный текст остаётся один-к-одному, а каждый origin равен
`CharOrigin(i, i + 1)`. После этого штатно выполняются hyphenation join и
homoglyph pass. Быстрый путь не пропускает эти два этапа.

Медленный существующий путь сохранить отдельной внутренней функцией, чтобы
его можно было напрямую сравнить с быстрым в unit-тестах.

## Подэтап 4B. Text-only нормализация

Добавить внутреннюю функцию, которая выполняет те же шаги 1–5, но возвращает
только строку и не создаёт `CharOrigin`, `RawSpan` или `NormalizedText`.

Обязательный контракт для любого входа:

```python
normalize_for_matching(raw_text) == normalize_text(raw_text).text
```

Перевести `normative_marker_ids` на text-only функцию. Других потребителей
переводить только при наличии теста эквивалентности.

## Набор эквивалентности

Проверить детерминированно:

- пустую и ASCII-строку;
- `ʼ ’ ‘ ´ \``;
- soft hyphen в начале, середине и конце;
- `-\n`, `-\r\n`, дефис с пробелами перед newline;
- перенос, который не должен склеиваться;
- decomposed Unicode и combining marks;
- fullwidth characters и ligatures;
- одиночную латиницу;
- латинские гомоглифы внутри кириллического слова;
- все строки `raw_text` из блоков девяти корпусных PDF.

Последний тест можно пометить `corpus`; он не должен дублировать query build.

## Стоп-условия

Если text-only путь хоть на одной строке отличается от `normalize_text().text`,
не добавлять исключение под конкретный PDF. Исправить общий алгоритм или
отказаться от подэтапа 4B.

Нормализацию предложения с последующим вырезанием normalized substring на
этом этапе не реализовывать.

---

# Этап 5. Сократить память `CharOrigin`

## Цель

Уменьшить память карты происхождения символов без изменения представления
`NormalizedText.origins`.

## Читать

- `search/types.py`: `CharOrigin`, `NormalizedText`;
- `search/normalization.py`;
- тесты normalization/state.

## Изменить

- `search/types.py`;
- обычный unit-тест типов или normalization.

## Точная реализация

Единственное продуктовое изменение:

```python
@dataclass(frozen=True, slots=True)
class CharOrigin:
    raw_start: int
    raw_end: int
```

На этом этапе не добавлять `slots` другим dataclass и не заменять origins на
массивы.

## Тесты этапа

1. Equality и hash совпадают с прежней семантикой.
2. Объект не имеет изменяемого `__dict__`.
3. Все map normalized/raw tests проходят.
4. SearchDocument и SearchResult сравниваются детерминированно.
5. JSON export/import не зависит от внутреннего представления CharOrigin.

Снять `tracemalloc` тем же способом и на том же PDF, что в исходном профиле.
Не вводить wall-clock или byte-size assert в pytest: такие пороги нестабильны.

---

# Этап 6. Переиспользовать анализ калек и убрать его из UI rerun

## Цель

Один раз вычислить calque hits при построении SearchResult и использовать их
для общих и секционных метрик. Нажатие статуса в Streamlit больше не должно
запускать regex-анализ всех блоков.

## Читать

- `search/calques.py`: `find_calques_with_rejections`, `collapse_components`,
  `compute_metrics`, `section_is_locally_dense`;
- `search/query_builder.py`: создание `calque_analysis` и SearchResult;
- `search/types.py`: `CalqueMetrics`, `SearchResult`;
- `search/presentation.py`: `build_search_summary`;
- `search/ui_logic.py`: `build_search_screen`;
- тесты calques, presentation и UI logic.

## Изменить

- `search/calques.py`;
- `search/query_builder.py`;
- `search/types.py`;
- `search/presentation.py`;
- обычные unit-тесты этих модулей.

## Точная реализация

1. Добавить immutable тип `SectionCalqueMetrics` со следующими полями:
   - `section_id`;
   - `tier1_hits`, `tier2_hits`, `tier3_hits`;
   - `density`;
   - `locally_dense`.
2. Добавить в конец `SearchResult` поле
   `section_calque_metrics: tuple[SectionCalqueMetrics, ...] = ()`, чтобы
   существующие искусственные SearchResult в тестах не требовали массовой
   правки.
3. Создать внутренний helper, принимающий уже готовый
   `calque_analysis[block_id]` и возвращающий общие и секционные метрики.
4. Для каждого блока использовать
   `collapse_components(calque_analysis[block_id][0])` ровно один раз для
   метрик.
5. Существующий `compute_metrics(document)` сохранить как совместимую
   обёртку для внешних вызовов и тестов.
6. `build_search_result_with_candidates` заполняет оба вида метрик из уже
   рассчитанного анализа.
7. `build_search_summary` использует `result.section_calque_metrics`.
8. Fallback к старому вычислению разрешён только для вручную созданного в
   unit-тесте SearchResult с пустым новым полем. Продуктовый pipeline всегда
   заполняет поле.

## Что нельзя изменить

- `collapse_components`;
- зоны включения и исключения;
- знаменатель author words;
- пороги density и locally dense;
- порядок секций;
- поля существующего `CalqueMetrics`;
- JSON schema проекта.

## Тесты этапа

1. Метрики из precomputed analysis равны `compute_metrics(document)`.
2. Секционные метрики равны старому presentation-расчёту.
3. Вызов `build_search_screen` для готового продуктового SearchResult не
   вызывает `find_calques`.
4. Изменение QueryState меняет usefulness, но не статические K-метрики.
5. Три последовательных screen build дают равные модели.

Цель benchmark: готовый screen build менее 0,1 с на контрольном документе.

---

# Этап 7. Один общий корпус для gate 16 и gate 17

## Цель

Удалить один из трёх одинаково дорогих query-проходов полного pytest, не
удаляя независимую проверку детерминизма.

## Особое правило владения

Этот этап меняет `tests/test_gate_step_16.py` и
`tests/test_gate_step_17.py`, поэтому его выполняет только главный
оркестратор, а не `plan-implementer`. Изменение gate-файлов ограничено
фикстурами и не меняет ни одного проверяемого утверждения.

## Читать

- `tests/conftest.py`;
- `tests/test_gate_step_16.py`;
- `tests/test_gate_step_17.py`;
- `tools/audit_search_quality.py`: `CorpusItem`, `collect_corpus`;
- `tools/audit_search_golden.py`: `GoldenCorpusItem`, `collect_golden`.

## Изменить

- `tests/conftest.py`;
- `tests/test_gate_step_16.py`;
- `tests/test_gate_step_17.py`;
- при необходимости `tools/audit_search_golden.py`;
- `pytest.ini` только для регистрации marker, не для coverage options.

## Точная реализация

1. Добавить session-scoped fixture, которая ровно один раз вызывает
   `collect_corpus` для канонических девяти `CORPUS_FILES`.
2. Gate 16 строит tier1/query samples и payload из этой фикстуры.
3. Для gate 17 добавить helper, который принимает уже готовые CorpusItem и
   добавляет только `measure_document`, создавая GoldenCorpusItem.
4. Обычный `collect_golden(paths)` сохранить: он вызывает `collect_corpus`,
   затем новый helper. CLI и прямое использование не меняются.
5. Строку
   `assert build_search_result(item.document) == result` в gate 17 не
   удалять, не сокращать и не заменять checksum. Это обязательный второй
   query-проход для проверки детерминизма.

## Marker `corpus`

Зарегистрировать marker `corpus` в `pytest.ini`. Пометить им только тесты,
которые реально запрашивают дорогую corpus fixture. Статические проверки
schema/CLI/fixture size по возможности оставить в быстром наборе.

Не удалять из `pytest.ini`:

- `--cov=parser`;
- `--cov=ui_helpers`;
- `--cov=compare`;
- `--cov=search`;
- `--cov-fail-under=90`.

Стандартный `.venv312\Scripts\python.exe -m pytest` остаётся официальной
приёмкой и запускает corpus tests.

## Тесты этапа

1. Счётчик подтверждает один `collect_corpus` для gate 16+17 в одной pytest
   session.
2. Gate 16 payload совпадает с ручной quality fixture.
3. Gate 17 candidate совпадает с golden.
4. Проверка детерминизма реально вызывает второй `build_search_result` для
   каждого из девяти документов.
5. `collect_golden(paths)` отдельно остаётся рабочим и read-only.

---

# Этап 8. Механическая очистка и быстрые команды разработки

## Цель

Удалить доказанный мёртвый код и задокументировать быстрый цикл разработки,
не ослабляя официальный gate.

## Читать

- `tools/audit_search_golden.py`;
- `pytest.ini`;
- раздел «Команды» в `CLAUDE.md`.

## Изменить

- `tools/audit_search_golden.py`;
- при необходимости `CLAUDE.md` только для добавления команд, не для
  изменения правил приёмки.

## Точная реализация

1. Удалить неиспользуемый `query_by_section` в `_document_payload`.
2. Удалить только подтверждённо неиспользуемые импорты.
3. Для `--verbose` выбрать одно из двух после проверки CLI-тестов:
   - реализовать дополнительный progress output; или
   - удалить аргумент, если ни тест, ни пользовательский контракт его не
     требуют.
4. Не совмещать этот этап с рефакторингом golden schema.

Команды разработки:

```powershell
# быстрые unit/integration без тяжёлого корпуса и coverage
.venv312\Scripts\python.exe -m pytest --no-cov -m "not corpus"

# только корпус без coverage
.venv312\Scripts\python.exe -m pytest --no-cov -m corpus

# официальная полная приёмка
.venv312\Scripts\python.exe -m pytest
```

## Стоп-условия

Не удалять CLI-аргумент, если он проверяется тестом или документирован как
пользовательский контракт. Не менять формат golden JSON.

---

# Этап 9. Итоговая приёмка и профиль

## Цель

Доказать, что оптимизация ускорила продукт и тесты, не изменив результата.

## До запуска

1. Убедиться, что последняя продуктовая правка уже внесена.
2. Проверить `git status` и `git diff`.
3. Убедиться, что не изменены:
   - `tests/fixtures/search_corpus_golden.json`;
   - `tests/fixtures/search_quality_review.json`;
   - `tests/fixtures/search_corpus_expectations.json`;
   - PDF в `examples/`;
   - `out.txt`.

## Обязательные проверки

1. Точечные тесты всех изменённых модулей с `--no-cov`.
2. `tests/test_project_rules.py --no-cov`.
3. Строгий побайтовый golden по девяти PDF.
4. Проверка полного списка query IDs и shortfalls.
5. Проверка JSON export/import и `needs_review`.
6. Ручной Streamlit smoke:
   - загрузка PDF;
   - карта разделов и override;
   - первые и скрытые карточки;
   - смена статуса, comment, source URL, failed engine;
   - экспорт и импорт JSON;
   - несколько последовательных кликов без повторного calque scan.
7. Повторный профиль времени по тем же документам и в том же порядке.
8. Повторный `tracemalloc` на том же PDF.
9. **Два полных запуска** без дополнительных флагов после последней правки:

```powershell
.venv312\Scripts\python.exe -m pytest
.venv312\Scripts\python.exe -m pytest
```

Если между двумя полными запусками была любая правка кода или тестов, оба
запуска начинаются заново.

## Критерии успеха

Обязательные функциональные:

- полный pytest зелёный дважды;
- coverage не ниже 90%;
- strict golden идентичен;
- версии и schema не изменены;
- UI и JSON-проект работают как раньше.

Целевые, но не wall-clock asserts:

- query build корпуса быстрее минимум в 3 раза;
- полный pytest быстрее минимум в 2 раза;
- один лишний corpus query-проход устранён;
- готовый screen build менее 0,1 с на контрольном документе;
- живая память CharOrigin существенно ниже исходного профиля.

Если функциональные критерии выполнены, но ускорение меньше целевого,
зафиксировать новый cProfile и остановиться перед дополнительным
рефакторингом. Не расширять объём работ без нового плана.

## 6. Что намеренно не делать

В рамках этого плана запрещено:

- переписывать дедупликацию `_duplicate_candidate_pairs`;
- удалять повторный `build_search_result` из gate 17;
- отключать coverage в официальной команде;
- обновлять golden из-за расхождения;
- менять parser/algo/dictionary/schema versions;
- вводить общий публичный `AnalysisContext`;
- хранить origins в `array`, NumPy или сторонней структуре;
- кэшировать весь Streamlit pipeline;
- оптимизировать скрытые карточки — их измеренная стоимость около 0,01 с;
- менять публичный API `parser/*`;
- менять пороги, квоты, scoring, сортировки или правила предметной области;
- устанавливать новые зависимости.

После этапа 9 дальнейшие оптимизации допускаются только по новому профилю и
отдельному плану.
