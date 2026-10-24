import 'dart:convert';

import 'package:flutter/services.dart';

import '../features/events/calendar_logic.dart';
import '../features/events/event_preview.dart';
import '../features/operations/structure.dart';
import '../format/format.dart';
import 'ai.dart';
import 'debug.dart';
import 'errors.dart';
import 'kassandra_api.dart';
import 'models.dart';
import 'v11.dart';
import 'webhooks.dart';
import 'views.dart';

/// Offline API backed by `assets/fixtures/c003_2026-04-26.json`.
/// The canned scenario (revenue −20% and 300 000 ₽ on 8 May) is returned as-is.
/// Other scenarios and saved events are applied with the same linear rules as the preview.
class FixtureApi implements KassandraApi {
  FixtureApi({this.sourcesDemo = false});

  /// Calendar and evening balances of the sources demo (gap 18 May, 10 payments).
  /// The default fixture keeps the base forecast the other screens were accepted on.
  final bool sourcesDemo;

  Map<String, dynamic>? _root;
  Map<String, dynamic>? _v11;
  Map<String, dynamic>? _calendarFile;
  ForecastResult? _forecast;
  RiskResult? _riskBeforeChange;
  String? _originalRunId;
  final Map<String, ScenarioResult> _scenarios = {};
  final Map<String, GapExplanation> _scenarioExplain = {};
  final Map<String, String> _idempotentForecasts = {};

  Future<void> _ensure() async {
    if (_root != null) return;
    final loaded = await Future.wait([
      rootBundle.loadString('assets/fixtures/c003_2026-04-26.json'),
      rootBundle.loadString('assets/fixtures/c003_v1_1.json'),
      rootBundle.loadString('assets/fixtures/c003_v1_1_calendar.json'),
    ]);
    _root = jsonDecode(loaded[0]) as Map<String, dynamic>;
    _v11 = jsonDecode(loaded[1]) as Map<String, dynamic>;
    _calendarFile = jsonDecode(loaded[2]) as Map<String, dynamic>;
    if (sourcesDemo) {
      final block =
          _v11!['refresh_with_netting_and_stress'] as Map<String, dynamic>;
      _forecast = ForecastResult.fromJson(
        block['forecast'] as Map<String, dynamic>,
      );
    } else {
      _forecast = ForecastResult.fromJson(
        _root!['forecast'] as Map<String, dynamic>,
      );
    }
    _originalRunId = _forecast!.runId;
    _txPool = _loadTransactions();
  }

  List<TransactionItem> _txPool = [];
  final Set<String> _editedAssumptions = {};

  List<TransactionItem> _loadTransactions() {
    final file = _calendarFile;
    if (file == null) return const [];
    final seen = <String>{};
    final items = <TransactionItem>[];
    for (final key in ['transactions_page', 'transactions_filtered']) {
      final page = file[key];
      if (page is! Map<String, dynamic>) continue;
      for (final raw in (page['items'] as List? ?? const [])) {
        if (raw is! Map<String, dynamic>) continue;
        final item = TransactionItem.fromJson(raw);
        if (seen.add(item.operationId)) items.add(item);
      }
    }
    return items;
  }

  Map<String, dynamic> _extra(String key) =>
      (_v11?[key] as Map<String, dynamic>?) ?? <String, dynamic>{};

  ForecastResult get _current {
    final forecast = _forecast;
    if (forecast == null) throw StateError('Фикстура не загружена');
    return forecast;
  }

  void _guard(String accountId) {
    if (accountId != 'C003') {
      throw ForbiddenException('Нет доступа к этому счёту');
    }
  }

  @override
  Future<List<Account>> accounts() async {
    await _ensure();
    final account = Account.fromJson(_root!['account'] as Map<String, dynamic>);
    final daily = await this.daily(account.id);
    return [
      Account(
        id: account.id,
        name: account.name,
        demo: account.demo,
        dataVersion: account.dataVersion,
        eventVersion: _current.eventVersion,
        firstDate: daily.first.date,
        lastDate: _current.asOf,
        quality: {
          'first_date': isoDate(daily.first.date),
          'last_date': isoDate(_current.asOf),
          'history_days': _current.confidence.historyDays,
        },
      ),
    ];
  }

  @override
  Future<ForecastResult> createForecast(
    ForecastRequest request, {
    required String idempotencyKey,
  }) async {
    await _ensure();
    _guard(request.accountId);
    if (!sameDate(request.asOf, DateTime.utc(2026, 4, 26))) {
      throw ApiFailure('В офлайн-фикстуре есть только срез 26.04.2026');
    }
    final cached = _idempotentForecasts[idempotencyKey];
    if (cached != null && cached == _current.runId) return _current;
    _idempotentForecasts[idempotencyKey] = _current.runId;
    return _current;
  }

  @override
  Future<GapExplanation> forecastExplanation(String runId) async {
    await _ensure();
    final canned = GapExplanation.fromJson(
      _root!['explanation'] as Map<String, dynamic>,
    );
    if (runId == _originalRunId && _current.runId == _originalRunId) {
      return canned;
    }
    final risk = _current.risk;
    return GapExplanation(
      hasGap: risk.firstGapDate != null || risk.currentGapMinor > 0,
      gapDate: risk.firstGapDate,
      shortfallMinor: risk.liquidityNeedMinor,
      when: risk.firstGapDate == null
          ? ''
          : formatDateDayMonth(risk.firstGapDate!),
      shortfall: formatMoneyShort(risk.liquidityNeedMinor, ceil: true),
      why: canned.why,
      actions: canned.actions,
      text: canned.text,
    );
  }

  @override
  Future<List<DailyPoint>> daily(String accountId) async {
    await _ensure();
    _guard(accountId);
    return [
      for (final item in _root!['daily_tail'] as List)
        DailyPoint.fromJson(item as Map<String, dynamic>),
    ];
  }

  @override
  Future<List<TopClient>> topClients(String accountId, DateTime asOf) async {
    await _ensure();
    _guard(accountId);
    return [
      for (final item in _root!['top_clients'] as List)
        TopClient.fromJson(item as Map<String, dynamic>),
    ];
  }

  @override
  Future<ScenarioResult> evaluateScenario(
    ScenarioSpec spec, {
    required String idempotencyKey,
  }) async {
    await _ensure();
    if (_isCanned(spec) &&
        spec.baseRunId == _originalRunId &&
        _current.runId == _originalRunId) {
      final result = ScenarioResult.fromJson(
        _root!['scenario'] as Map<String, dynamic>,
      );
      _scenarios[result.scenarioId] = result;
      _scenarioExplain[result.scenarioId] = GapExplanation.fromJson(
        _root!['scenario_explanation'] as Map<String, dynamic>,
      );
      return result;
    }
    final result = _localScenario(spec);
    _scenarios[result.scenarioId] = result;
    _scenarioExplain[result.scenarioId] = GapExplanation(
      hasGap: result.risk.firstGapDate != null,
      gapDate: result.risk.firstGapDate,
      shortfallMinor: result.risk.liquidityNeedMinor,
      when: result.risk.firstGapDate == null
          ? ''
          : formatDateDayMonth(result.risk.firstGapDate!),
      shortfall: formatMoneyShort(result.risk.liquidityNeedMinor, ceil: true),
      why: result.assumptions,
      actions: const [],
      text: result.assumptions.join(' '),
    );
    return result;
  }

  bool _isCanned(ScenarioSpec spec) {
    if (spec.kind != 'combined' || spec.parts.length != 2) return false;
    final revenue = spec.parts
        .where((part) => part.kind == 'revenue_drop')
        .toList();
    final extra = spec.parts
        .where((part) => part.kind == 'extra_expense')
        .toList();
    if (revenue.length != 1 || extra.length != 1) return false;
    final date = extra.first.expenseDate;
    return revenue.first.revenueDropPct == 20 &&
        extra.first.amountMinor == 30000000 &&
        date != null &&
        sameDate(date, DateTime.utc(2026, 5, 8));
  }

  ScenarioResult _localScenario(ScenarioSpec spec) {
    final base = _current;
    final parts = spec.kind == 'combined'
        ? spec.parts
        : [if (spec.part != null) spec.part!];
    final closings = _apply(base, parts);
    final points = [
      for (var i = 0; i < base.points.length; i++)
        base.points[i].copyWith(
          closingBalanceMinor: closings[i],
          clearBand: true,
        ),
    ];
    final risk = deriveRisk(
      openingMinor: base.openingBalanceMinor,
      points: points,
      reserveMinor: base.reserveMinor,
      creditLimitMinor: base.creditLimitMinor,
    );
    final assumptions = [
      'Условный сценарий; история и сохранённый базовый план не изменены.',
      for (final part in parts) _assumption(part),
      if (parts.length > 1)
        'Комбинированный сценарий: ${parts.length} негативных изменения применены одновременно.',
    ];
    return ScenarioResult(
      scenarioId: 'local-${newId()}',
      baseRunId: spec.baseRunId,
      points: points,
      events: base.events,
      risk: risk,
      deltaEndMinor:
          points.last.closingBalanceMinor -
          base.points.last.closingBalanceMinor,
      deltaLiquidityMinor:
          risk.liquidityNeedMinor - base.risk.liquidityNeedMinor,
      baseFirstGapDate: base.risk.firstGapDate,
      assumptions: assumptions,
      specKind: spec.kind,
    );
  }

  List<int> _apply(ForecastResult base, List<ScenarioPart> parts) {
    final deltas = List<int>.filled(base.points.length, 0);
    for (final part in parts) {
      if (part.kind == 'revenue_drop') {
        var cumulative = 0;
        for (var i = 0; i < base.points.length; i++) {
          cumulative +=
              (base.points[i].residualRevenueMinor * part.revenueDropPct / 100)
                  .round();
          deltas[i] -= cumulative;
        }
      } else if (part.kind == 'extra_expense' && part.expenseDate != null) {
        for (var i = 0; i < base.points.length; i++) {
          if (!base.points[i].date.isBefore(part.expenseDate!)) {
            deltas[i] -= part.amountMinor;
          }
        }
      } else if (part.kind == 'fixed_cost_increase') {
        var cumulative = 0;
        for (var i = 0; i < base.points.length; i++) {
          final started =
              part.startDate == null ||
              !base.points[i].date.isBefore(part.startDate!);
          if (started) {
            cumulative +=
                (base.points[i].residualOutMinor * part.fixedCostPct / 100)
                    .round();
          }
          deltas[i] -= cumulative;
        }
      } else if (part.kind == 'client_delay') {
        final moved = List<int>.filled(base.points.length, 0);
        for (var i = 0; i < base.points.length; i++) {
          final amount = base.points[i].residualInMinor;
          if (amount == 0) continue;
          final target = base.points[i].date.add(
            Duration(days: part.delayDays),
          );
          final destination = base.points.indexWhere(
            (point) => !point.date.isBefore(target),
          );
          moved[i] -= amount;
          if (destination != -1) moved[destination] += amount;
        }
        var cumulative = 0;
        for (var i = 0; i < base.points.length; i++) {
          cumulative += moved[i];
          deltas[i] += cumulative;
        }
      }
    }
    return [
      for (var i = 0; i < base.points.length; i++)
        base.points[i].closingBalanceMinor + deltas[i],
    ];
  }

  String _assumption(ScenarioPart part) => switch (part.kind) {
    'revenue_drop' =>
      'Снижена операционная выручка на ${part.revenueDropPct}%.',
    'extra_expense' =>
      'Добавлен внеплановый расход ${part.amountMinor} коп. на ${part.expenseDate == null ? '—' : isoDate(part.expenseDate!)}.',
    'client_delay' =>
      'Оплата клиента «${part.counterparty}» сдвинута на ${part.delayDays} дн.',
    'fixed_cost_increase' =>
      'Постоянные расходы выросли на ${part.fixedCostPct}%.',
    _ => 'Изменение ${part.kind}.',
  };

  @override
  Future<GapExplanation> scenarioExplanation(String scenarioId) async {
    await _ensure();
    return _scenarioExplain[scenarioId] ?? GapExplanation.empty;
  }

  @override
  Future<PlannedEvent> createEvent(
    PlannedEvent event, {
    String? baseRunId,
  }) async {
    await _ensure();
    _guard(event.accountId);
    final base = _current;
    _riskBeforeChange = base.risk;
    final signed = event.direction == 'in'
        ? event.amountMinor
        : -event.amountMinor;
    final points = [
      for (final point in base.points)
        point.copyWith(
          closingBalanceMinor: point.date.isBefore(event.expectedDate)
              ? point.closingBalanceMinor
              : point.closingBalanceMinor + signed,
        ),
    ];
    final risk = deriveRisk(
      openingMinor: base.openingBalanceMinor,
      points: points,
      reserveMinor: base.reserveMinor,
      creditLimitMinor: base.creditLimitMinor,
    );
    _forecast = base.copyWith(
      runId: newId(),
      points: points,
      events: [...base.events, event],
      risk: risk,
      eventVersion: base.eventVersion + 1,
    );
    return event;
  }

  @override
  Future<List<PlannedEvent>> events(String accountId, DateTime asOf) async {
    await _ensure();
    _guard(accountId);
    final rows = [..._current.events]
      ..sort((a, b) => a.expectedDate.compareTo(b.expectedDate));
    return rows;
  }

  @override
  Future<PlannedEvent> rescheduleEvent(
    String eventId,
    DateTime expectedDate,
    DateTime knownAt,
  ) async {
    await _ensure();
    final index = _current.events.indexWhere((event) => event.id == eventId);
    if (index < 0) throw ApiFailure('Событие не найдено');
    final previous = _current.events[index];
    if (knownAt.isBefore(previous.knownAt)) {
      throw ValidationException('Дата знания должна сохранять порядок версий');
    }
    final next = PlannedEvent(
      id: previous.id,
      accountId: previous.accountId,
      componentId: previous.componentId,
      category: previous.category,
      amountMinor: previous.amountMinor,
      direction: previous.direction,
      dueDate: previous.dueDate,
      expectedDate: expectedDate,
      knownAt: knownAt,
      source: previous.source,
      status: previous.status,
      paidMinor: previous.paidMinor,
      decision: previous.decision,
      label: previous.label,
      counterpartyName: previous.counterpartyName,
      counterpartyInn: previous.counterpartyInn,
    );
    final events = [..._current.events];
    events[index] = next;
    _forecast = _current.copyWith(
      events: events,
      eventVersion: _current.eventVersion + 1,
    );
    return next;
  }

  @override
  Future<void> markPayment(
    String eventId,
    String operationId,
    int amountMinor,
    DateTime knownAt,
  ) async {
    await _ensure();
    if (operationId.trim().isEmpty || amountMinor <= 0) {
      throw ValidationException('Укажите операцию и сумму оплаты');
    }
    final index = _current.events.indexWhere((event) => event.id == eventId);
    // Сверка в фикстуре v1.1 ссылается на события вне базового прогноза.
    // Вызов принимается, цифры среза 26.04 не меняются.
    if (index < 0) return;
    final previous = _current.events[index];
    final paid = previous.paidMinor + amountMinor;
    if (paid > previous.amountMinor) {
      throw ValidationException('Оплата превышает сумму события');
    }
    final next = PlannedEvent(
      id: previous.id,
      accountId: previous.accountId,
      componentId: previous.componentId,
      category: previous.category,
      amountMinor: previous.amountMinor,
      direction: previous.direction,
      dueDate: previous.dueDate,
      expectedDate: previous.expectedDate,
      knownAt: knownAt,
      source: previous.source,
      status: paid == previous.amountMinor ? 'paid' : 'partially_paid',
      paidMinor: paid,
      decision: previous.decision,
      label: previous.label,
      counterpartyName: previous.counterpartyName,
      counterpartyInn: previous.counterpartyInn,
    );
    final events = [..._current.events];
    events[index] = next;
    _forecast = _current.copyWith(
      events: events,
      eventVersion: _current.eventVersion + 1,
    );
  }

  @override
  Future<ImportReport> importEvents(
    List<int> bytes,
    String filename, {
    String? baseRunId,
  }) async {
    await _ensure();
    final text = utf8.decode(bytes, allowMalformed: true);
    final rows = text
        .split(RegExp(r'\r?\n'))
        .where((line) => line.trim().isNotEmpty)
        .length;
    final count = rows > 0 ? rows - 1 : 0;
    return ImportReport([
      'В фикстуре файл не записывается на сервер',
      'Строк в файле: $count',
    ]);
  }

  @override
  Future<TransactionPage> transactions(
    String accountId,
    TransactionQuery query,
  ) async {
    await _ensure();
    _guard(accountId);
    final summary = await categorySummary(accountId, query);
    final filtered =
        [
          for (final item in _txPool)
            if (_txKeep(item, query)) item,
        ]..sort(
          (a, b) => compareOperations(
            sortFromCode(query.sort),
            TransactionLike(date: a.date, amountMinor: a.amountMinor),
            TransactionLike(date: b.date, amountMinor: b.amountMinor),
          ),
        );
    final totals = _txTotals(filtered, summary, query);
    final start = (query.page - 1) * query.pageSize;
    final pageItems = start >= filtered.length
        ? const <TransactionItem>[]
        : filtered.skip(start).take(query.pageSize).toList();
    final facets = _calendarFile?['transactions_page'];
    final facetMap = facets is Map<String, dynamic>
        ? facets['facets'] as Map<String, dynamic>?
        : null;
    return TransactionPage(
      items: pageItems,
      page: query.page,
      pageSize: query.pageSize,
      total: totals.count,
      totals: totals,
      categories: [
        for (final item in (facetMap?['categories'] as List? ?? const []))
          if (item is Map<String, dynamic>) FacetCount.fromJson(item),
      ],
      counterparties: [
        for (final item in (facetMap?['counterparties'] as List? ?? const []))
          if (item is Map<String, dynamic>) CounterpartyFacet.fromJson(item),
      ],
    );
  }

  bool _txKeep(TransactionItem item, TransactionQuery query) {
    if (query.dateFrom != null &&
        item.date.isBefore(dateOnly(query.dateFrom!))) {
      return false;
    }
    if (query.dateTo != null && item.date.isAfter(dateOnly(query.dateTo!))) {
      return false;
    }
    if (query.direction != null && item.direction != query.direction) {
      return false;
    }
    if (query.categories.isNotEmpty &&
        !query.categories.contains(item.category)) {
      return false;
    }
    if (query.counterpartyInn != null &&
        query.counterpartyInn!.isNotEmpty &&
        item.counterpartyInn != query.counterpartyInn) {
      return false;
    }
    if (query.amountMin != null && item.amountMinor < query.amountMin!) {
      return false;
    }
    if (query.amountMax != null && item.amountMinor > query.amountMax!) {
      return false;
    }
    final needle = query.query.trim().toLowerCase();
    if (needle.isNotEmpty) {
      final haystack =
          '${item.counterpartyName} ${item.counterpartyInn} ${item.purpose}'
              .toLowerCase();
      if (!haystack.contains(needle)) return false;
    }
    return true;
  }

  FlowTotals _txTotals(
    List<TransactionItem> filtered,
    CategorySummary summary,
    TransactionQuery query,
  ) {
    if (query.categories.length == 1 && query.direction != null) {
      final code = query.categories.first;
      for (final row in summary.rows) {
        if (row.direction == query.direction && row.category == code) {
          return FlowTotals(
            count: row.operations,
            inflowMinor: row.direction == 'in' ? row.amountMinor : 0,
            outflowMinor: row.direction == 'out' ? row.amountMinor : 0,
          );
        }
      }
    }
    if (query.categories.isEmpty && query.direction == null) {
      final count = summary.rows.fold<int>(
        0,
        (sum, row) => sum + row.operations,
      );
      if (count > 0) {
        return FlowTotals(
          count: count,
          inflowMinor: summary.inflowMinor,
          outflowMinor: summary.outflowMinor,
        );
      }
    }
    if (query.categories.isEmpty && query.direction == 'out') {
      final count = summary.rows
          .where((row) => row.direction == 'out')
          .fold<int>(0, (sum, row) => sum + row.operations);
      if (count > 0) {
        return FlowTotals(
          count: count,
          inflowMinor: 0,
          outflowMinor: summary.outflowMinor,
        );
      }
    }
    if (query.categories.isEmpty && query.direction == 'in') {
      final count = summary.rows
          .where((row) => row.direction == 'in')
          .fold<int>(0, (sum, row) => sum + row.operations);
      if (count > 0) {
        return FlowTotals(
          count: count,
          inflowMinor: summary.inflowMinor,
          outflowMinor: 0,
        );
      }
    }
    var inflow = 0;
    var outflow = 0;
    for (final item in filtered) {
      if (item.direction == 'in') {
        inflow += item.amountMinor;
      } else {
        outflow += item.amountMinor;
      }
    }
    return FlowTotals(
      count: filtered.length,
      inflowMinor: inflow,
      outflowMinor: outflow,
    );
  }

  @override
  Future<void> updateCategory(
    String accountId,
    String operationId,
    String category,
    DateTime knownAt,
  ) async {
    await _ensure();
    _guard(accountId);
    _txPool = [
      for (final item in _txPool)
        if (item.operationId == operationId)
          item.copyWith(category: category)
        else
          item,
    ];
  }

  @override
  Future<PlanFact> planFact(String runId) async {
    await _ensure();
    final forecast = _current;
    final actual = {
      for (final point in await daily(forecast.accountId))
        isoDate(point.date): point,
    };
    return PlanFact(
      runId: runId,
      rows: [
        for (final point in forecast.points)
          PlanFactRow(
            date: point.date,
            plannedMinor: point.closingBalanceMinor,
            actualMinor: actual[isoDate(point.date)]?.valid == true
                ? actual[isoDate(point.date)]!.balanceMinor
                : null,
            deltaMinor: actual[isoDate(point.date)]?.valid == true
                ? actual[isoDate(point.date)]!.balanceMinor -
                      point.closingBalanceMinor
                : null,
            plannedInMinor: point.inflowMinor,
            plannedOutMinor: point.outflowMinor,
            actualInMinor: actual[isoDate(point.date)]?.inflowMinor,
            actualOutMinor: actual[isoDate(point.date)]?.outflowMinor,
            coverage:
                actual[isoDate(point.date)]?.coverage ?? 'outside_coverage',
          ),
      ],
      matchingReview: const [],
    );
  }

  @override
  Future<ImportReport> importTransactions(
    List<int> bytes,
    String filename,
  ) async {
    await _ensure();
    throw ForbiddenException(
      'Для загрузки своей выписки настройте личный доступ по README',
    );
  }

  @override
  Future<String> experimentsSummary() async {
    return 'Офлайн-режим: реестр экспериментов доступен только у живого API';
  }

  @override
  Future<Dictionaries> dictionaries() async {
    await _ensure();
    return Dictionaries.fromJson(_extra('dictionaries'));
  }

  @override
  Future<RefreshResult> refreshForecast(String runId) async {
    await _ensure();
    final current = _current;
    final before = _riskBeforeChange ?? current.risk;
    final shift =
        current.risk.firstGapDate == null || before.firstGapDate == null
        ? null
        : current.risk.firstGapDate!.difference(before.firstGapDate!).inDays;
    final changes = RefreshChanges(
      gapAppeared:
          before.firstGapDate == null && current.risk.firstGapDate != null,
      gapResolved:
          before.firstGapDate != null && current.risk.firstGapDate == null,
      gapShiftDays: shift,
      needDeltaMinor:
          current.risk.liquidityNeedMinor - before.liquidityNeedMinor,
      minimumDeltaMinor:
          current.risk.minimumBalanceMinor - before.minimumBalanceMinor,
    );
    return RefreshResult(
      mode: 'events_only',
      changes: changes,
      forecast: current,
    );
  }

  @override
  Future<List<AlertItem>> alerts(String accountId, String runId) async {
    await _ensure();
    _guard(accountId);
    final raw = _extra('alerts_before');
    return [
      for (final item in (raw['alerts'] as List? ?? const []))
        if (item is Map<String, dynamic>) AlertItem.fromJson(item),
    ];
  }

  @override
  Future<EventParse> parseEvent(String text, DateTime today) async {
    await _ensure();
    return EventParse.fromJson(
      _extra('events_parse')['response'] as Map<String, dynamic>? ?? const {},
    );
  }

  @override
  Future<Map<String, dynamic>> taxCalendar(
    String accountId,
    DateTime asOf,
    String? runId,
  ) async {
    await _ensure();
    return _extra('tax_calendar');
  }

  @override
  Future<RegisterResult> applyTaxCalendar(
    String accountId,
    String? baseRunId,
  ) async {
    await _ensure();
    return const RegisterResult(
      created: 1,
      revised: 0,
      unchanged: 0,
      warning: 'Суммы налогового календаря выведены по истории. Проверьте их перед оплатой.',
    );
  }

  @override
  Future<List<RecurringItem>> recurring(String accountId, DateTime asOf) async {
    await _ensure();
    final raw = _extra('recurring');
    final items = [
      for (final item in (raw['components'] as List? ?? const []))
        if (item is Map<String, dynamic>) RecurringItem.fromJson(item),
    ];
    const software = 'component:4dc0eb2a8062f9b3';
    if (items.every((item) => item.componentId != software)) {
      items.add(
        const RecurringItem(
          componentId: software,
          label: 'ПО и учётные системы',
          rhythm: 'monthly',
          rhythmLabel: 'каждый месяц',
          direction: 'out',
          amountMinor: 530000,
        ),
      );
    }
    return items;
  }

  @override
  Future<CategorySummary> categorySummary(
    String accountId,
    TransactionQuery query,
  ) async {
    await _ensure();
    final file = _calendarFile ?? const {};
    final from = query.dateFrom;
    final to = query.dateTo ?? from;
    Map<String, dynamic>? picked;
    if (from != null && to != null && to.difference(from).inDays >= 300) {
      picked = file['categories_summary_year_out'] as Map<String, dynamic>?;
    }
    picked ??= file['categories_summary_90d'] as Map<String, dynamic>?;
    picked ??= _extra('categories_summary');
    final summary = CategorySummary.fromJson(picked);
    if (query.direction == null) return summary;
    return CategorySummary(
      dateFrom: summary.dateFrom,
      dateTo: summary.dateTo,
      inflowMinor: summary.inflowMinor,
      outflowMinor: summary.outflowMinor,
      rows: [
        for (final row in summary.rows)
          if (row.direction == query.direction) row,
      ],
      groups: [
        for (final row in summary.groups)
          if (row.direction == query.direction) row,
      ],
    );
  }

  @override
  Future<CalendarView> paymentCalendar(
    String runId,
    CalendarQuery query,
  ) async {
    await _ensure();
    final file = _calendarFile ?? const {};
    final key = sourcesDemo ? 'demo_sources_calendar' : 'calendar';
    final raw = file[key];
    final view = raw is Map<String, dynamic>
        ? CalendarView.fromJson(raw)
        : CalendarView(
            runId: runId,
            accountId: 'C003',
            asOf: DateTime.utc(2026, 4, 26),
            dateTo: DateTime.utc(2026, 5, 26),
            items: const [],
            totals: const FlowTotals(count: 0, inflowMinor: 0, outflowMinor: 0),
            facets: const CalendarFacets(
              sources: [],
              categories: [],
              directions: [],
              obligatory: 0,
              cancelled: 0,
            ),
          );
    return applyCalendarFilters(view, query);
  }

  @override
  Future<void> patchEvent(String eventId, Map<String, dynamic> fields) async {
    await _ensure();
    _mutateCalendar(eventId, (item) {
      if (fields['expected_date'] != null) {
        item['date'] = fields['expected_date'];
      }
      if (fields['amount_minor'] != null) {
        item['amount_minor'] = fields['amount_minor'];
      }
      if (fields['category'] != null) item['category'] = fields['category'];
      if (fields['label'] != null) item['label'] = fields['label'];
      if (fields['counterparty_name'] != null) {
        item['counterparty_name'] = fields['counterparty_name'];
      }
      if (fields['certainty'] != null) item['certainty'] = fields['certainty'];
    });
  }

  @override
  Future<void> deleteEvent(String eventId, DateTime knownAt) async {
    await _ensure();
    _mutateCalendar(eventId, (item) {
      item['status'] = 'cancelled';
      item['actions'] = <String>[];
    });
  }

  @override
  Future<void> editAssumption(
    String runId,
    String assumptionId,
    Map<String, dynamic> body,
  ) async {
    await _ensure();
    final action = (body['action'] ?? 'edit').toString();
    if (_editedAssumptions.contains(assumptionId)) {
      throw ConflictException(
        'Ожидание уже исправлено: измените событие override:$assumptionId',
        'override:$assumptionId',
      );
    }
    _editedAssumptions.add(assumptionId);
    _mutateCalendar(assumptionId, (item) {
      if (action == 'skip') {
        item['status'] = 'cancelled';
        item['actions'] = <String>[];
        return;
      }
      if (body['expected_date'] != null) item['date'] = body['expected_date'];
      if (body['amount_minor'] != null) {
        item['amount_minor'] = body['amount_minor'];
      }
      if (body['label'] != null) item['label'] = body['label'];
      item['kind'] = 'registered';
      item['source'] = 'manual';
      item['source_short'] = 'Вручную';
      item['actions'] = ['edit', 'delete'];
      item['id'] = 'override:$assumptionId';
    });
  }

  void _mutateCalendar(String id, void Function(Map<String, dynamic>) edit) {
    final file = _calendarFile;
    if (file == null) return;
    for (final key in ['calendar', 'demo_sources_calendar']) {
      final view = file[key];
      if (view is! Map<String, dynamic>) continue;
      final items = view['items'];
      if (items is! List) continue;
      for (final raw in items) {
        if (raw is Map<String, dynamic> && raw['id'] == id) edit(raw);
      }
    }
  }

  @override
  Future<Map<String, dynamic>> sources(String accountId) async {
    await _ensure();
    return _extra('sources');
  }

  @override
  Future<RegisterResult> createContract(
    String accountId,
    Map<String, dynamic> body,
  ) async {
    await _ensure();
    return RegisterResult.fromJson(_extra('contract'));
  }

  @override
  Future<RegisterResult> createInvoice(
    String accountId,
    Map<String, dynamic> body,
  ) async {
    await _ensure();
    return RegisterResult.fromJson(_extra('supplier_invoice'));
  }

  @override
  Future<RegisterResult> importFile(
    String path,
    List<int> bytes,
    String filename,
  ) async {
    return _register(path);
  }

  @override
  Future<RegisterResult> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    return _register(path);
  }

  Future<RegisterResult> _register(String path) async {
    await _ensure();
    if (path.contains('1c') || path.contains('transactions')) {
      throw ForbiddenException(
        'Для загрузки своей выписки настройте личный доступ по README',
      );
    }
    if (path.contains('invoices')) {
      return RegisterResult.fromJson(_extra('invoices_import'));
    }
    if (path.contains('bank')) return RegisterResult.fromJson(_extra('bank'));
    if (path.contains('marketplace') || path.contains('ofd')) {
      return const RegisterResult(created: 1, revised: 0, unchanged: 0);
    }
    return const RegisterResult(created: 0, revised: 0, unchanged: 0);
  }

  @override
  Future<Map<String, dynamic>> planFactWeek(String runId) async {
    await _ensure();
    return _extra('plan_fact_week');
  }

  @override
  Future<Map<String, dynamic>> reconcile(
    String accountId, {
    bool dryRun = true,
  }) async {
    await _ensure();
    return _extra('reconcile');
  }

  @override
  Future<List<CounterpartyRisk>> counterpartyRisks(
    String accountId,
    DateTime asOf,
  ) async {
    await _ensure();
    final raw = _extra('counterparty_risks');
    return [
      for (final item in (raw['counterparties'] as List? ?? const []))
        if (item is Map<String, dynamic>) CounterpartyRisk.fromJson(item),
    ];
  }

  // The debug page imports real files on the server; fixtures have nothing to show.
  @override
  Future<List<DebugDataset>> debugDatasets() async => const [];

  @override
  Future<DebugDataset> debugDataset(String id) =>
      throw ApiFailure('Отладка работает только с сервером');

  @override
  Future<DebugDataset> uploadDebugDataset(
    List<int> bytes,
    String filename, {
    void Function(int sent, int total)? onProgress,
    bool history = true,
  }) => throw ApiFailure('Отладка работает только с сервером');

  @override
  Future<void> deleteDebugDataset(String id) async {}

  @override
  Future<DebugCheck> debugCheck(String accountId) =>
      throw ApiFailure('Отладка работает только с сервером');

  @override
  Future<EvaluationState> startEvaluation(String datasetId) =>
      throw ApiFailure('Отладка работает только с сервером');

  @override
  Future<EvaluationState> evaluation(String datasetId) async =>
      EvaluationState(
        datasetId: datasetId,
        status: 'none',
        stage: '',
        done: 0,
        total: 0,
        elapsed: 0,
        log: const [],
      );

  @override
  Future<EvaluationCompany> evaluationCompany(
    String datasetId,
    String accountId,
  ) => throw ApiFailure('Отладка работает только с сервером');

  @override
  Future<String> evaluationReport(String datasetId) =>
      throw ApiFailure('Отладка работает только с сервером');

  // Fixtures have no server trace: the forecast page hides the block.
  @override
  Future<List<TraceStep>> forecastTrace(String runId) async => const [];

  // The LLM runs on the server only; fixtures show the hint instead of a review.
  @override
  Future<AiReview?> aiCachedReview(String runId) async => null;

  @override
  Future<AiReview> aiReview(String runId, {bool refresh = false}) =>
      throw ApiFailure('ИИ работает только с сервером');

  @override
  Future<AiModelReview> aiModelReview(String datasetId) =>
      throw ApiFailure('ИИ работает только с сервером');

  @override
  Future<AiOcrResult> aiOcr(
    List<int> bytes,
    String filename,
    String? accountId,
  ) => throw ApiFailure('ИИ работает только с сервером');

  @override
  Future<AiChatReply> aiChat(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  ) => throw ApiFailure('ИИ работает только с сервером');

  @override
  Future<String> aiChatStart(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  ) => throw ApiFailure('ИИ работает только с сервером');

  @override
  Future<AiChatProgress> aiChatProgress(String jobId) =>
      throw ApiFailure('ИИ работает только с сервером');

  // Webhooks receive data on the server; fixtures show an empty list.
  @override
  Future<List<WebhookKind>> webhookKinds() async => const [];

  @override
  Future<List<WebhookInfo>> webhooks(String accountId) async => const [];

  @override
  Future<WebhookInfo> createWebhook(
    String accountId,
    String kind,
    String? label,
  ) => throw ApiFailure('Webhook работает только с сервером');

  @override
  Future<String> rotateWebhook(String id) =>
      throw ApiFailure('Webhook работает только с сервером');

  @override
  Future<void> deleteWebhook(String id) async {}

  @override
  Future<WebhookCheck> checkWebhook(String id) =>
      throw ApiFailure('Webhook работает только с сервером');

  @override
  Future<String> validateWebhook(String id, String body, String contentType) =>
      throw ApiFailure('Webhook работает только с сервером');

  @override
  Future<List<WebhookDelivery>> webhookDeliveries(String accountId) async =>
      const [];
}
