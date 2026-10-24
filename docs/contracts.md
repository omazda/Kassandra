# Контракты

Исполняемые Pydantic-схемы — `src/cashflow_domain/schemas.py`, OpenAPI — `/docs` API. Все поля с суффиксом `_minor` — целые копейки. `amount` входного CSV — десятичные рубли, не float. В API horizon=30 фиксирован.

ForecastRequest содержит account_id/as_of/model (constant, mean, weekday, hgb, structural; по умолчанию structural)/model_version/calendar/reserve_minor/credit_limit_minor и необязательные ожидаемые data_version/event_version. Версии проверяются оптимистически. ForecastResult содержит run_id, model_version, trained_to, data_version, event_version, as_of, начальный остаток, 30 ForecastPoint, RiskResult, события и ограничения. Прогноз — неизменяемый JSON snapshot.

PlannedEvent: ID, account_id, component_id, category, amount_minor, direction, due_date, expected_date, known_at, source, status, replacement_id, paid_minor и необязательные counterparty_inn, counterparty_name, document_ref (null по умолчанию; события хранятся JSON-ревизиями, миграция SQLite не нужна, старые записи читаются). Регулярные события получают контрагента из истории до as_of. Источник source: regular (механизм по истории), tax_calendar (налоговый календарь), documents (реестр документов), manual, csv; три первых - предположения системы, их подтверждённый ввод может заместить. Структурная модель (model=structural) строит события этих источников; реестр документов восстановлен из архива выписки (ретро-режим D6) и доступен, пока в архиве известны оплаты после as_of. Применение known_at <= as_of обязательно. Подтверждённый реестр заменяет конкретный ID предположения, либо пользователь явно выбирает дополнительное движение. Оплата связывается с конкретной операцией и имеет дату знания; история состояния события версионируется.

ScenarioSpec ссылается на base_run_id: delay(event_id, delay_days), revenue_drop(revenue_drop_pct), extra_expense(amount_minor, expense_date). ScenarioResult сохраняет spec и собственную траекторию/риск; за базовый результат отвечает неизменяемый snapshot. Перенос за горизонт остаётся в списке событий сценария.

Сценарии X9. fixed_cost_increase(fixed_cost_pct, start_date?, categories?): будущие неоплаченные исходящие события выбранных категорий (по умолчанию rent, utilities, telecom, software, accounting, facility_services, bank_fee) с даты start_date (по умолчанию после as_of) растут на pct; остаточный отток растёт на pct × доля этих категорий в оттоке за 90 дней до as_of (без own_transfer и owner_withdrawal); допущение записывается в assumptions. client_delay(counterparty, delay_days 1..60): counterparty — ИНН или название; сдвигаются все будущие неоплаченные поступления клиента; если их нет, 422. combined(parts: 2..5 изменений kind ∈ delay, revenue_drop, extra_expense, fixed_cost_increase, client_delay): применяются одновременно; revenue_drop и fixed_cost_increase — не более одного раза.

GET `/accounts/{id}/top-clients?as_of=&limit=&run_id=`: топ клиентов по сумме операционной выручки за 90 дней до as_of включительно (поля counterparty_inn/name, amount_minor, share, operations, last_date; с run_id ещё upcoming_events и upcoming_minor по базовому прогнозу). GET `/forecasts/{run_id}/explanation` и `/scenarios/{scenario_id}/explanation`: шаблонное объяснение разрыва GapExplanation (has_gap, gap_date, shortfall_minor, when, shortfall, why, actions, text). Без LLM, все числа из расчёта; дефицит равен risk.liquidity_need_minor. Действия по правилам: перенос платежа поставщику, ускорение оплаты или предоплата клиента, овердрафт или кредитная линия на сумму дефицита, резерв.

Вероятность null означает недоступность. Денежный расчёт: B[d]=B[d-1]+event_in-event_out+residual_in-residual_out. Сценарий снижения выручки меняет только revenue, не owner_funding/own_transfer.

HTTP: `/health`; `/api/v1/accounts`; `/imports/transactions`, `/imports/events`; `/accounts/{id}/transactions`, `/daily`, `/quality`, `/events`; `/transactions/{account_id}/{operation_id}/category`; `/forecasts` и `/forecasts/{run_id}`; `/events`, `/events/{event_id}/payments`; `/scenarios/evaluate`; `/plan-fact?run_id=...`; `/experiments`. Каждый объект проверяется через доступ к его account_id. Повторный импорт уникален по (account_id, operation_id); конфликт содержимого отклоняется целиком. POST прогнозов/сценариев поддерживает Idempotency-Key в пределах principal и типа запроса, с проверкой тела.



Точный HTTP-контракт экспортирован в `docs/openapi.json`. Для категории можно указать known_at; без него используется дата правки. GET `/scenarios/{scenario_id}` читает сохранённый сценарий с проверкой доступа. POST `/accounts/{id}/coverage-gaps` подтверждает пропуск с датой обнаружения.

GET `/accounts/{id}/events?as_of=...` возвращает исторические версии с учётом даты знания. Без as_of возвращается весь актуальный реестр, включая позднее известные события; этот режим предназначен для просмотра и не подменяет вход исторического прогноза. Право на счёт проверяется в обоих режимах. В POST `/events` переданный base_run_id обязан относиться к тому же счёту и для дополнительного события. UI использует устойчивый ID одной формы: повторное нажатие не создаёт копию; «Новое событие» начинает новую форму.

## Изменения финальной интеграции

- `ForecastRequest.model` по умолчанию `hgb` (решение D1 по вариантам без знания будущего; structural остаётся доступным).
- `ForecastRequest.retro_registry` (по умолчанию `false`): только для `structural`. При `true` реестр документов строится по оплатам после даты расчёта (бэктест с утечкой); в `quality_flags` добавляется «БЭКТЕСТ С УТЕЧКОЙ». При `false` реестр отключён, как на правом краю данных.
- `ForecastResult.confidence`: `{level: none|very_low|low|medium|high, label, history_days, refuse, limitation, warnings}`; при низком доверии добавляется флаг в `quality_flags`. История меньше 7 дней: `refuse=true`, прогноз выдаётся с предупреждением. Для `structural` на 7–89 днях применяется отраслевой prior с P10/P90 в точках.

## API v1.1 (24.10.2026): источники вне выписки

Изменения только добавляющие: старые запросы и ответы не менялись.

**PlannedEvent.**
- `source` расширен: `contract`, `invoice`, `tax`, `bank`, `accounting`, `marketplace`, `ofd` (к прежним `regular`, `tax_calendar`, `documents`, `manual`, `csv`).
- Новое необязательное поле `certainty`: `promised` | `agreed` | `documented`. Оно информационное, прогноз считает событие подтверждённым.
- Реестр по-прежнему принимает только `status: "confirmed"`.
- Подписи — `GET /dictionaries`. Клиент должен показывать неизвестный `source` как «Другой источник».

**Единый реестр.** Каждый источник превращается в события реестра со стабильным `id`:
- повторная синхронизация того же документа не создаёт копию: изменённое событие получает новую ревизию, неизменённое пропускается;
- с `base_run_id` событие замещает совпадающее предположение по истории — та же сторона, тот же поток, ±7 дней;
- ответ содержит `created`, `revised`, `unchanged`, `replaced_assumptions`, `events`;
- дата знания — `known_at`, иначе `as_of` базового прогноза, иначе последняя дата выписки.

| Метод | Назначение |
|---|---|
| `GET /dictionaries` | категории (группа, выручка, финансирование, постоянные расходы), источники, уровни уверенности |
| `GET /accounts/{id}/categories/summary?date_from&date_to` | обороты по категориям и доли (по умолчанию 90 дней) |
| `GET /accounts/{id}/recurring?as_of` | регулярные поступления и списания по истории до `as_of`, следующие даты |
| `GET /accounts/{id}/tax-calendar?as_of&horizon_days&run_id` | режим налогообложения, правила (ЕНП, УСН, НДС, ЕСХН), ближайшие налоги и замещаемые предположения |
| `POST /accounts/{id}/tax-calendar/apply` `{base_run_id}` | налоги горизонта в реестр (`source: tax`); при риске двойного учёта — `warning` |
| `POST /accounts/{id}/contracts` | договор: предоплата (%), поставка, отсрочка, рассрочка или явный график → события `contract` |
| `POST /accounts/{id}/invoices` | счёт: срок оплаты, для входящих — сдвиг на типичную задержку клиента по истории (`payment_dates[].level`: client/account/due_date) |
| `POST /accounts/{id}/imports/invoices` | CSV счетов из ЭДО или 1С (`number;issue_date;due_date;payment_terms_days;amount;direction;counterparty_inn;counterparty_name`) |
| `POST /accounts/{id}/imports/1c` | выписка 1С «Обмен с банком» (1CClientBankExchange, cp1251 или UTF-8); остаток на конец дня восстанавливается от начального (`opening_balance`, если его нет в файле) |
| `POST /accounts/{id}/integrations/bank` | кредиты с графиком, зарплатный проект, созданные и не исполненные платёжки → `bank` |
| `POST /accounts/{id}/integrations/marketplace` | график выплат маркетплейса → `marketplace` |
| `POST /accounts/{id}/integrations/ofd` | чеки онлайн-кассы: карта и СБП зачисляются через рабочий день за вычетом комиссии → `ofd` |
| `POST /events/parse` `{text, today}` | черновик события из фразы («клиент обещал заплатить 300 000 рублей 20-го»), ничего не сохраняет |
| `GET /counterparties/{inn}/check` | статус в ЕГРЮЛ/ЕГРИП (DaData при `DADATA_API_KEY`, кэш 7 дней); вымышленные ИНН датасета — только `examples/registry_demo.json` |
| `GET /accounts/{id}/counterparty-risks?as_of` | 10 крупнейших клиентов и поставщиков за 90 дней с проверкой; для рискованного клиента — готовый сценарий `client_delay` |
| `POST /forecasts/{run_id}/refresh` | `mode`: `events_only` (без запуска модели), `new_data` (прогноз на последнюю дату), `unchanged`; `changes`: разрыв появился, исчез или сдвинулся, изменение потребности |
| `GET /accounts/{id}/alerts?run_id` | предупреждения: `current_deficit`, `new_gap`, `gap_moved`, `gap_resolved`, `overdue_payment` (до даты прогноза), `data_quality`; `severity` critical, warning или info; `action` для кнопки |
| `GET /accounts/{id}/sources` | выписка, события по источникам, последние синхронизации |
| `GET /plan-fact?run_id&granularity=day\|week` | добавлены `rows[].deviation`, `overdue[]` с текстом предупреждения, `summary`, `weeks[]` |

CLI: `cashflow build-error-bank` — банк ошибок для вероятности модели по умолчанию, пишет только в `artifacts/`.

### API v1.1, вторая итерация: точность прогноза с внешними событиями

**Один владелец потока** (`ForecastResult.netting`).
- Подтверждённые события из интеграций (`contract`, `invoice`, `tax`, `bank`, `accounting`, `marketplace`, `ofd`) с `decision: additional` вытесняют из среднего потока модели свою долю.
- Доля = доля группы потока за 90 дней: клиенты, карты, маркетплейс, наличные, поставщики, постоянные расходы, зарплата, налоги, кредиты.
- Вытесняется `min(сумма событий, доля × поток модели за 30 дней)`, пропорционально по дням. Сверх доли деньги остаются дополнительными.
- В ответе: `factors` (множители трёх потоков), `displaced_minor`, `groups[]` (`events_minor`, `capacity_minor`, `displaced_minor`).
- Ручные события (`manual`) и CSV-реестр не вытесняют ничего: пользователь сам выбирает «заменить» или «дополнительно».

**Просроченные события** (`ForecastResult.overdue_events`). Неоплаченное подтверждённое обязательство с датой не позже `as_of` переносится на ближайший рабочий день (`treatment: rolled_forward`, `new_date`): долг не исчезает из прогноза. Просроченное поступление не учитывается (`treatment: excluded`), пока у него нет новой даты или оплаты.

**Стресс-оценка** (`RiskResult.stress`, `null`, если исключать нечего). Тот же прогноз без поступлений, которые могут не прийти: `certainty: promised` и контрагенты с риском в реестре (critical или warning, без сетевых запросов — кэш или демо-реестр). Поля: `excluded[]` (с причиной), `excluded_minor`, `first_gap_date`, `liquidity_need_minor`, `minimum_balance_minor`.

**Вероятность с событиями реестра.** Для моделей с банком ошибок вероятность больше не пропадает при появлении событий: события считаются надёжными, `probability_source` и `limitation` прямо говорят об этом, нижнюю границу показывает `stress`. Структурная модель с калибровкой при внешних событиях по-прежнему отвечает «недоступно».

**Сверка с выпиской** — `POST /accounts/{id}/reconcile?dry_run=`; также запускается автоматически при каждом импорте выписки (поле `reconciliation` в отчёте импорта).
- Кандидаты: операции того же направления в окне от −10 до +45 дней от ожидаемой даты.
- Баллы: ИНН — 3, название — 2, номер документа в назначении — 3, точная сумма — 3 (±1% — 2, частичная оплата — 1), дата ±3 дня — 1, категория — 1.
- Связывается лучший кандидат с баллом ≥ 6, опознанный по контрагенту или документу и опережающий второго на 2 балла. Остальные идут в `needs_review` (до трёх кандидатов).
- Повторный запуск ничего не дублирует.

**Предупреждения** (`/alerts`): добавлены `risky_counterparty` (от контрагента с риском ожидаются деньги, `action.scenario` — готовый `client_delay`) и `stress_gap` (без ненадёжных поступлений разрыв раньше или появляется).

**Обновление**: `refresh` в режиме `events_only` снимает прежнее вытеснение и считает его заново. Если поток был вытеснен целиком и восстановить его нельзя, выполняется полный пересчёт на ту же дату (`mode: events_full`).

**Типизированные ответы**: у всех эндпоинтов v1.1 есть модели ответов (`src/cashflow_domain/views.py`); `docs/openapi.json` описывает их полностью.

### API v1.1, третья итерация: календарь платежей, правка событий, фильтры операций

**Правка события реестра** — `PATCH /events/{id}`, тело `EventEdit`: обязательный `known_at` и любые из полей `expected_date`, `due_date`, `amount_minor`, `category`, `label`, `counterparty_name`, `counterparty_inn`, `certainty`.
- Не переданные поля не меняются. Прежний запрос `{expected_date, known_at}` работает как перенос.
- Правка — новая ревизия с датой знания `known_at`: прогноз на более раннюю дату видит прежнюю версию. `known_at` не может быть раньше последней ревизии.
- Сумма не может быть меньше уже оплаченной части. Те же значения повторно новую версию не создают.

**Удаление события** — `DELETE /events/{id}?known_at=`. Событие не стирается: пишется ревизия `status: cancelled`, прогнозы с даты `known_at` его не учитывают. Повторный вызов ничего не меняет. Частично оплаченное событие остаётся отменённым, оплата сохраняется.

**Правка ожидания из истории** (регулярный платёж, налог, документ) — `POST /forecasts/{run_id}/assumptions/{assumption_id}`, тело `{action, known_at, expected_date?, amount_minor?, label?}`.
- `edit` — в реестр записывается версия пользователя (`source: manual`, `decision: replace`, id `override:<assumption_id>`), исходное ожидание из прогноза убирается.
- `skip` — то же замещение сразу отменяется: платёж исчезает из прогноза.
- Повторная правка того же ожидания — 409 с id события, которое нужно править дальше.

**Календарь платежей** — `GET /forecasts/{run_id}/calendar`. Все ожидаемые платежи прогноза до конца горизонта.
- Поля строки: `date`, `direction`, `amount_minor` (остаток к оплате), `label` и `title` (без хвоста «· повтор по истории»), `component_id` (связь с `/recurring`: ритм и обычный день), `category_label`, `group`, `source_label` и `source_short` (для бейджа: «Банк», «Счёт», «По истории»), `kind` (`assumption` — из истории, `registered` — реестр), `status`, `certainty_label`, контрагент, `obligatory` (списание на налоги, зарплату, платежи по кредитам и постоянные расходы), `overdue`.
- `actions` — что можно сделать: `edit` и `delete` у событий реестра, `edit` и `skip` у ожиданий, пусто у отменённых.
- Фильтры (через И, списки повторением параметра): `date_from`, `date_to`, `direction`, `kind`, `source`, `category`, `group`, `obligatory`, `include_cancelled`, `q` (подпись, контрагент, ИНН), `amount_min`, `amount_max`.
- `totals` — число, поступления и списания отфильтрованных строк. `facets` — источники (короткие подписи), категории и направления по всему календарю с количеством, `obligatory` — сколько обязательных, `cancelled` — сколько отменено.

**Операции** — `GET /accounts/{id}/transactions` принимает фильтры `date_from`, `date_to`, `direction`, `category`, `group`, `counterparty_inn`, `q` (контрагент, ИНН, назначение), `amount_min`, `amount_max`, `sort` (`date_desc`, `date_asc`, `amount_desc`, `amount_asc`).
- Для среза прошлого клиент передаёт `date_to = as_of`, иначе в список попадут операции после даты расчёта.
- Новые поля ответа: `totals` (по отфильтрованным операциям) и `facets` (категории с количеством и группой, 20 самых частых контрагентов).

**Структура** — `GET /accounts/{id}/categories/summary` принимает те же фильтры (`direction`, `group`, `counterparty_inn`, `q`, `amount_min`, `amount_max`), поэтому диаграмма и список описывают одни и те же операции. Новое поле `groups` — доли восьми групп (выручка, кредиты и собственник, налоги, зарплата, постоянные расходы, закупки и подрядчики, прочее, без категории) для круговой диаграммы. Подписи групп — в `GET /dictionaries`, поле `groups`.

**Короткие подписи источников** — `GET /dictionaries`, поле `sources[].short_label`: для бейджей и фильтров, где полная подпись не помещается.

## Отладка: загрузка датасета в формате хакатона

Включается переменной `CASHFLOW_DEBUG_DATASETS=true` (на стенде включена), иначе эндпоинтов нет (404).

- `POST /api/v1/debug/datasets?history=true` — multipart `file`: `all_operations.csv`, одна выписка `statements/C001.csv` или ZIP (`all_operations.csv` + `companies.csv`, либо `statements/*.csv`; копия `dataset/dataset/` пропускается). До 100 МБ. Строки проверяются по одной: хорошие импортируются, плохие перечислены в `errors` (`line`, `reason`, первые 50) и посчитаны в `rejected`. Набор получает id `dbgN`, счета — `dbgN-<company_id>`, поэтому не пересекаются с демо-счетами. Ответ `DebugDataset`: строки, дубли, период, качество (несверки остатка, подозрительные дубли, пустые назначения и ИНН, дни под маской), категоризация (доля без категории, топ-8), список компаний. Не больше 3 наборов одновременно (403). Файл, близкий к формату, приводится к нему (`notes`): кодировка Windows-1251, разделитель `,`/`\t`/`|`, русские названия колонок, сумма со знаком вместо направления, даты `ДД.ММ.ГГГГ`, нет номеров операций или компании (компания — имя файла). Обязательны дата, сумма и остаток. `history=true` (по умолчанию): если в файле продолжение компаний основного датасета, их история до начала файла копируется в набор (`history_companies`, `history_operations`, у счёта `data_from` и `history_copied`); проверка на истории берёт даты только из периода файла.
- `GET /api/v1/debug/datasets` — наборы без списка компаний; `GET /api/v1/debug/datasets/{id}` — полный отчёт.
- `GET /api/v1/debug/accounts/{account_id}/check` — прогноз `hgb` на последнюю дату счёта (дата и сумма разрыва, вероятность) и проверка на истории: прогноз от даты за 30 дней до конца выписки против фактических остатков — MAE, MAE constant, skill, минимум, дата разрыва в прогнозе и в факте. Без 90 дней истории до этой даты — `backtest_note`.
- `POST /api/v1/debug/datasets/{id}/evaluate` — запускает в фоне проверку всего набора (`EvaluationState`, `status=running`); `GET .../evaluation` — состояние: `stage`, `done`/`total`, `elapsed`, `log` (пошаговый журнал «Шаг 1…8»), `result`, `error`. Результат хранится в SQLite; прерванный расчёт — `error`. Для каждой компании — прогноз ансамбля на последнюю дату и до 4 прогнозов от более ранних дат (шаг 14 дней, горизонт 30 дней полностью известен, только данные до даты расчёта). Факт — остаток без новых пополнений после даты расчёта (D3), разрыв — метка D2, тревога — калиброванная вероятность от порога X11 (без калибровки — прогноз ниже нуля). `result.metrics`: `balance` (MAE, MSE, RMSE, WAPE, bias, то же у «не изменится», skill по MAE и MSE, MAE минимума, MAE по дням горизонта), `gaps` и `gaps_trajectory` (матрица TP/FP/FN/TN, accuracy, precision, recall, F1, specificity, дата ±3 дня, заблаговременность), `probability` (Brier, Brier skill, ROC-AUC, базовая частота, надёжность по корзинам 20%). Закрытые счета (остаток 0, операции кончились раньше набора больше чем на 7 дней) помечены `closed` и не входят в разрывы.
- `GET /api/v1/debug/datasets/{id}/evaluation/companies/{account_id}` — компания: текущий прогноз с P10/P90, 60 дней истории, окна проверки (прогноз, P10/P90, факт D3, наблюдаемый остаток, пополнения, день разрыва и тревоги), журнал расчёта прогноза.
- `GET /api/v1/debug/datasets/{id}/evaluation/report.md` — отчёт Markdown для скачивания: журнал, таблицы метрик, матрица ошибок, надёжность вероятности.
- `DELETE /api/v1/debug/datasets/{id}` — удаляет набор со всеми операциями, прогнозами, событиями и расчётом.

`GET /api/v1/forecasts/{run_id}/trace` — как построен прогноз, 8 шагов (`TraceStep`: `n`, `title`, `detail`, `items`): история, категоризация, регулярные платежи, известные события, ML-модель с весами ансамбля, сборка траектории, вероятность, разрыв и действия.

## Webhook: автоматический сбор данных

Внешняя система (банк, 1С, маркетплейс, онлайн-касса, CRM) сама отправляет данные на адрес счёта. Код — `src/cashflow_services/webhooks.py`, тесты — `tests/test_webhooks.py`, интерфейс — «Источники данных» → «Автоматический сбор — webhook».

- `POST /api/v1/accounts/{id}/webhooks` `{kind, label?}` → webhook и **ключ (`secret`) — один раз**. Типы `kind`: `statement` (новые операции выписки: JSON `{"operations": [...]}` или CSV формата хакатона), `1c` (1CClientBankExchange), `bank` (BankFeed), `marketplace`, `ofd`, `invoices` (`{"invoices": [...]}`), `contract` (ContractSpec). `GET /api/v1/webhooks/kinds` — подписи и примеры тел.
- `GET /api/v1/accounts/{id}/webhooks` — список с последней доставкой; `GET /api/v1/accounts/{id}/webhooks/deliveries` — журнал; `POST /api/v1/webhooks/{hook}/rotate` — новый ключ; `DELETE /api/v1/webhooks/{hook}`.
- `POST /api/v1/webhooks/{hook}/check` — состояние по настоящим доставкам: `waiting` (доставок не было), `working`, `failing` (последняя с ошибкой), `disabled`. Ничего не выдумывает и не пишет.
- `POST /api/v1/webhooks/{hook}/validate` — тело запроса, которое отправит система пользователя: разбор и ответ «что было бы добавлено», без записи в прогноз и журнал. (Прежняя проверка на встроенном образце убрана: она сообщала о «найденных» документах, которых пользователь не отправлял, а «Отправить пример» писал выдуманные платежи в прогноз.)
- **Приём:** `POST /api/v1/hooks/{hook}` без сессии пользователя. Подлинность — `X-Kassandra-Signature: sha256=<hex HMAC-SHA256(ключ, тело)>` или `Authorization: Bearer <ключ>` (сравнение за постоянное время). `X-Delivery-Id` — повтор не применяется дважды (`status: duplicate`). Данные пишутся с правами только на свой счёт: операция другого счёта отклоняет доставку. Ответы: 200 `{status, summary}`, 401 — подпись не сошлась, 422 — ошибка данных (доставка всё равно в журнале).
- На стенде весь сайт за nginx basic auth: отправитель передаёт пароль сайта (`curl -u логин:пароль`) и подпись; заголовок `Authorization` nginx к API не передаёт, поэтому вариант с Bearer-ключом там не работает.
