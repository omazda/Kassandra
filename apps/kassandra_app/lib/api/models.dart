import 'dart:math';

import 'v11.dart';

int asInt(Object? value, [int fallback = 0]) {
  if (value is int) return value;
  if (value is num) return value.round();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
}

int? asIntOpt(Object? value) {
  if (value == null) return null;
  return asInt(value);
}

double? asDoubleOpt(Object? value) {
  if (value == null) return null;
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value);
  return null;
}

bool asBool(Object? value) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) return value == 'true' || value == '1';
  return false;
}

DateTime asDate(Object? value) {
  if (value is DateTime) {
    return DateTime.utc(value.year, value.month, value.day);
  }
  final text = value.toString().split('T').first;
  final parts = text.split('-');
  return DateTime.utc(
    int.parse(parts[0]),
    int.parse(parts[1]),
    int.parse(parts[2]),
  );
}

DateTime? asDateOpt(Object? value) {
  if (value == null || value == '') return null;
  return asDate(value);
}

String isoDate(DateTime date) {
  final y = date.year.toString().padLeft(4, '0');
  final m = date.month.toString().padLeft(2, '0');
  final d = date.day.toString().padLeft(2, '0');
  return '$y-$m-$d';
}

bool sameDate(DateTime a, DateTime b) =>
    a.year == b.year && a.month == b.month && a.day == b.day;

DateTime dateOnly(DateTime date) =>
    DateTime.utc(date.year, date.month, date.day);

String newId() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  String hex(int b) => b.toRadixString(16).padLeft(2, '0');
  final h = bytes.map(hex).join();
  return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
}

const categoryLabels = <String, String>{
  'revenue': 'Выручка',
  'operating_expense': 'Основные расходы',
  'tax': 'Налоги',
  'salary': 'Зарплата',
  'rent': 'Аренда',
  'debt_service': 'Обслуживание долга',
  'owner_funding': 'Финансирование владельцем',
  'own_transfer': 'Собственные переводы',
  'refund': 'Возвраты',
  'bank_fee': 'Банковские комиссии',
  'unknown': 'Не определено',
  'acquiring': 'Эквайринг',
  'sbp': 'СБП',
  'cash_in': 'Инкассация и взнос наличных',
  'customer_payment': 'Оплата клиентов',
  'marketplace_payout': 'Выплаты маркетплейса',
  'contract_prepayment': 'Предоплата по договору',
  'contract_settlement': 'Окончательный расчёт по договору',
  'other_income': 'Прочие доходы',
  'loan_in': 'Кредит и заём',
  'owner_withdrawal': 'Выводы владельца',
  'supplier': 'Поставщики и закупки',
  'contractor': 'Подрядчики и самозанятые',
  'utilities': 'Коммунальные услуги',
  'telecom': 'Связь',
  'software': 'ПО и учётные системы',
  'accounting': 'Бухгалтерия',
  'facility_services': 'Охрана и клининг',
  'marketing': 'Реклама',
  'logistics': 'Логистика и топливо',
  'legal_claim': 'Судебные взыскания',
};

const sourceLabels = <String, String>{
  'regular': 'Повтор по истории',
  'tax_calendar': 'Налоговый календарь',
  'documents': 'Счёт / договор',
  'manual': 'Ручной ввод',
  'csv': 'Импорт',
};

const statusLabels = <String, String>{
  'assumption': 'В прогнозе',
  'confirmed': 'Подтверждено',
  'partially_paid': 'Частично оплачено',
  'paid': 'Оплачено',
  'cancelled': 'Не участвует',
  'review': 'На проверке',
};

String categoryLabel(String category) => categoryLabels[category] ?? category;

String sourceLabel(String source) => sourceLabels[source] ?? 'Другой источник';

class Account {
  const Account({
    required this.id,
    required this.name,
    required this.demo,
    required this.dataVersion,
    required this.eventVersion,
    this.firstDate,
    this.lastDate,
    this.quality = const {},
    this.debug = false,
  });

  factory Account.fromJson(Map<String, dynamic> json) {
    final quality = json['quality'];
    final map = quality is Map<String, dynamic> ? quality : <String, dynamic>{};
    return Account(
      id: json['id'].toString(),
      name: (json['name'] ?? json['id']).toString(),
      demo: asBool(json['demo']),
      dataVersion: asInt(json['data_version']),
      eventVersion: asInt(json['event_version']),
      firstDate: asDateOpt(map['first_date']),
      lastDate: asDateOpt(map['last_date']),
      quality: map,
      debug: json['owner'] == 'debug',
    );
  }

  final String id;
  final String name;
  final bool demo;

  /// Uploaded on the debug page (dbgN- ids): kept out of the account switcher.
  final bool debug;
  final int dataVersion;
  final int eventVersion;
  final DateTime? firstDate;
  final DateTime? lastDate;
  final Map<String, dynamic> quality;

  String get shortName {
    final stripped = name.replaceAll(RegExp(r'[«»"]'), '').trim();
    final parts = stripped.split(RegExp(r'\s+'));
    return parts.length > 1 ? parts.last : stripped;
  }
}

class HistoryPoint {
  const HistoryPoint({
    required this.date,
    required this.balanceMinor,
    this.inflowMinor = 0,
    this.outflowMinor = 0,
  });

  factory HistoryPoint.fromJson(Map<String, dynamic> json) => HistoryPoint(
    date: asDate(json['date']),
    balanceMinor: asInt(json['balance_minor']),
    inflowMinor: asInt(json['inflow_minor']),
    outflowMinor: asInt(json['outflow_minor']),
  );

  final DateTime date;
  final int balanceMinor;
  final int inflowMinor;
  final int outflowMinor;
}

class DailyPoint {
  const DailyPoint({
    required this.date,
    required this.balanceMinor,
    this.inflowMinor = 0,
    this.outflowMinor = 0,
    this.valid = true,
    this.coverage = '',
  });

  factory DailyPoint.fromJson(Map<String, dynamic> json) => DailyPoint(
    date: asDate(json['date']),
    balanceMinor: asInt(json['balance_minor']),
    inflowMinor: asInt(json['inflow_minor']),
    outflowMinor: asInt(json['outflow_minor']),
    valid: json['valid'] == null ? true : asBool(json['valid']),
    coverage: (json['coverage'] ?? '').toString(),
  );

  final DateTime date;
  final int balanceMinor;
  final int inflowMinor;
  final int outflowMinor;
  final bool valid;
  final String coverage;
}

class ForecastPoint {
  const ForecastPoint({
    required this.date,
    required this.eventInMinor,
    required this.eventOutMinor,
    required this.residualInMinor,
    required this.residualOutMinor,
    required this.residualRevenueMinor,
    required this.closingBalanceMinor,
    this.p10Minor,
    this.p90Minor,
  });

  factory ForecastPoint.fromJson(Map<String, dynamic> json) => ForecastPoint(
    date: asDate(json['date']),
    eventInMinor: asInt(json['event_in_minor']),
    eventOutMinor: asInt(json['event_out_minor']),
    residualInMinor: asInt(json['residual_in_minor']),
    residualOutMinor: asInt(json['residual_out_minor']),
    residualRevenueMinor: asInt(json['residual_revenue_minor']),
    closingBalanceMinor: asInt(json['closing_balance_minor']),
    p10Minor: asIntOpt(json['p10_minor']),
    p90Minor: asIntOpt(json['p90_minor']),
  );

  final DateTime date;
  final int eventInMinor;
  final int eventOutMinor;
  final int residualInMinor;
  final int residualOutMinor;
  final int residualRevenueMinor;
  final int closingBalanceMinor;
  final int? p10Minor;
  final int? p90Minor;

  int get inflowMinor => eventInMinor + residualInMinor;
  int get outflowMinor => eventOutMinor + residualOutMinor;

  ForecastPoint copyWith({
    int? closingBalanceMinor,
    int? p10Minor,
    int? p90Minor,
    bool clearBand = false,
  }) {
    return ForecastPoint(
      date: date,
      eventInMinor: eventInMinor,
      eventOutMinor: eventOutMinor,
      residualInMinor: residualInMinor,
      residualOutMinor: residualOutMinor,
      residualRevenueMinor: residualRevenueMinor,
      closingBalanceMinor: closingBalanceMinor ?? this.closingBalanceMinor,
      p10Minor: clearBand ? null : (p10Minor ?? this.p10Minor),
      p90Minor: clearBand ? null : (p90Minor ?? this.p90Minor),
    );
  }
}

class RiskResult {
  const RiskResult({
    required this.thresholdMinor,
    required this.currentGapMinor,
    required this.newRisk,
    required this.firstGapDate,
    required this.minimumBalanceMinor,
    required this.liquidityNeedMinor,
    required this.probability30d,
    required this.calibrationStatus,
    required this.probabilitySource,
    required this.trajectoryCount,
    required this.simulations,
    required this.limitation,
    this.stress,
    this.alarm,
  });

  factory RiskResult.fromJson(Map<String, dynamic> json) => RiskResult(
    thresholdMinor: asInt(json['threshold_minor']),
    currentGapMinor: asInt(json['current_gap_minor']),
    newRisk: asBool(json['new_risk']),
    firstGapDate: asDateOpt(json['first_gap_date']),
    minimumBalanceMinor: asInt(json['minimum_balance_minor']),
    liquidityNeedMinor: asInt(json['liquidity_need_minor']),
    probability30d: asDoubleOpt(json['probability_30d']),
    calibrationStatus: (json['calibration_status'] ?? 'unavailable').toString(),
    probabilitySource: (json['probability_source'] ?? '').toString(),
    trajectoryCount: asInt(json['trajectory_count']),
    simulations: asInt(json['simulations']),
    limitation: (json['limitation'] ?? '').toString(),
    stress: json['stress'] is Map<String, dynamic>
        ? StressView.fromJson(json['stress'] as Map<String, dynamic>)
        : null,
    alarm: json['alarm'] == null ? null : asBool(json['alarm']),
  );

  final int thresholdMinor;
  final int currentGapMinor;
  final bool newRisk;
  final DateTime? firstGapDate;
  final int minimumBalanceMinor;
  final int liquidityNeedMinor;
  final double? probability30d;
  final String calibrationStatus;
  final String probabilitySource;
  final int trajectoryCount;
  final int simulations;
  final String limitation;
  final StressView? stress;

  /// X11: the calibrated probability reached the alarm threshold; null without calibration.
  final bool? alarm;

  Map<String, dynamic> toJson() => {
    'threshold_minor': thresholdMinor,
    'current_gap_minor': currentGapMinor,
    'new_risk': newRisk,
    'first_gap_date': firstGapDate == null ? null : isoDate(firstGapDate!),
    'minimum_balance_minor': minimumBalanceMinor,
    'liquidity_need_minor': liquidityNeedMinor,
    'probability_30d': probability30d,
    'calibration_status': calibrationStatus,
    'probability_source': probabilitySource,
    'trajectory_count': trajectoryCount,
    'simulations': simulations,
    'limitation': limitation,
    'alarm': alarm,
  };
}

class Confidence {
  const Confidence({
    this.level,
    this.historyDays,
    this.refuse = false,
    this.label,
    this.limitation,
    this.warnings = const [],
  });

  factory Confidence.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const Confidence();
    return Confidence(
      level: json['level']?.toString(),
      historyDays: asIntOpt(json['history_days']),
      refuse: asBool(json['refuse']),
      label: json['label']?.toString(),
      limitation: json['limitation']?.toString(),
      warnings: [
        for (final item in (json['warnings'] as List?) ?? const [])
          item.toString(),
      ],
    );
  }

  final String? level;
  final int? historyDays;
  final bool refuse;
  final String? label;
  final String? limitation;
  final List<String> warnings;
}

class PlannedEvent {
  const PlannedEvent({
    required this.id,
    required this.accountId,
    required this.componentId,
    required this.category,
    required this.amountMinor,
    required this.direction,
    required this.dueDate,
    required this.expectedDate,
    required this.knownAt,
    required this.source,
    required this.status,
    this.paidMinor = 0,
    this.decision = 'additional',
    this.label = '',
    this.counterpartyName,
    this.counterpartyInn,
    this.replacementId,
    this.certainty,
  });

  factory PlannedEvent.fromJson(Map<String, dynamic> json) => PlannedEvent(
    id: json['id'].toString(),
    accountId: (json['account_id'] ?? '').toString(),
    componentId: (json['component_id'] ?? '').toString(),
    category: (json['category'] ?? 'unknown').toString(),
    amountMinor: asInt(json['amount_minor']),
    direction: (json['direction'] ?? 'out').toString(),
    dueDate: asDate(json['due_date'] ?? json['expected_date']),
    expectedDate: asDate(json['expected_date'] ?? json['due_date']),
    knownAt: asDate(json['known_at']),
    source: (json['source'] ?? 'manual').toString(),
    status: (json['status'] ?? 'assumption').toString(),
    paidMinor: asInt(json['paid_minor']),
    decision: (json['decision'] ?? 'additional').toString(),
    label: (json['label'] ?? '').toString(),
    counterpartyName: json['counterparty_name']?.toString(),
    counterpartyInn: json['counterparty_inn']?.toString(),
    replacementId: json['replacement_id']?.toString(),
    certainty: json['certainty']?.toString(),
  );

  final String id;
  final String accountId;
  final String componentId;
  final String category;
  final int amountMinor;
  final String direction;
  final DateTime dueDate;
  final DateTime expectedDate;
  final DateTime knownAt;
  final String source;
  final String status;
  final int paidMinor;
  final String decision;
  final String label;
  final String? counterpartyName;
  final String? counterpartyInn;
  final String? replacementId;
  final String? certainty;

  int get remainingMinor => amountMinor - paidMinor;

  String get title {
    final named = categoryLabel(category);
    if (named != category) return named;
    final head = label.split('·').first.trim();
    return head.isEmpty ? 'Платёж' : head;
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'account_id': accountId,
    'component_id': componentId,
    'category': category,
    'amount_minor': amountMinor,
    'direction': direction,
    'due_date': isoDate(dueDate),
    'expected_date': isoDate(expectedDate),
    'known_at': isoDate(knownAt),
    'source': source,
    'status': status,
    'paid_minor': paidMinor,
    'decision': decision,
    'label': label,
    if (counterpartyName != null && counterpartyName!.isNotEmpty)
      'counterparty_name': counterpartyName,
    if (counterpartyInn != null && counterpartyInn!.isNotEmpty)
      'counterparty_inn': counterpartyInn,
    if (replacementId != null) 'replacement_id': replacementId,
    if (certainty != null && certainty!.isNotEmpty) 'certainty': certainty,
  };
}

class ForecastResult {
  const ForecastResult({
    required this.runId,
    required this.accountId,
    required this.asOf,
    required this.model,
    required this.modelVersion,
    required this.dataVersion,
    required this.eventVersion,
    required this.calendar,
    required this.openingBalanceMinor,
    required this.reserveMinor,
    required this.creditLimitMinor,
    required this.points,
    required this.events,
    required this.risk,
    required this.qualityFlags,
    required this.confidence,
    required this.latestOperationDate,
    required this.history,
    this.trainedTo,
    this.createdAt,
    this.netting,
    this.overdueEvents = const [],
  });

  factory ForecastResult.fromJson(Map<String, dynamic> json) => ForecastResult(
    runId: json['run_id'].toString(),
    accountId: json['account_id'].toString(),
    asOf: asDate(json['as_of']),
    model: (json['model'] ?? 'hgb').toString(),
    modelVersion: (json['model_version'] ?? '').toString(),
    trainedTo: asDateOpt(json['trained_to']),
    dataVersion: asInt(json['data_version']),
    eventVersion: asInt(json['event_version']),
    calendar: asBool(json['calendar']),
    openingBalanceMinor: asInt(json['opening_balance_minor']),
    reserveMinor: asInt(json['reserve_minor']),
    creditLimitMinor: asInt(json['credit_limit_minor']),
    points: [
      for (final item in (json['points'] as List? ?? const []))
        ForecastPoint.fromJson(item as Map<String, dynamic>),
    ],
    events: [
      for (final item in (json['events'] as List? ?? const []))
        PlannedEvent.fromJson(item as Map<String, dynamic>),
    ],
    risk: RiskResult.fromJson(json['risk'] as Map<String, dynamic>),
    qualityFlags: [
      for (final item in (json['quality_flags'] as List? ?? const []))
        item.toString(),
    ],
    confidence: Confidence.fromJson(
      json['confidence'] as Map<String, dynamic>?,
    ),
    latestOperationDate: asDate(json['latest_operation_date'] ?? json['as_of']),
    history: [
      for (final item in (json['history'] as List? ?? const []))
        HistoryPoint.fromJson(item as Map<String, dynamic>),
    ],
    createdAt: json['created_at']?.toString(),
    netting: json['netting'] is Map<String, dynamic>
        ? NettingView.fromJson(json['netting'] as Map<String, dynamic>)
        : null,
    overdueEvents: [
      for (final item in (json['overdue_events'] as List? ?? const []))
        if (item is Map<String, dynamic>) OverdueItem.fromJson(item),
    ],
  );

  final String runId;
  final String accountId;
  final DateTime asOf;
  final String model;
  final String modelVersion;
  final DateTime? trainedTo;
  final int dataVersion;
  final int eventVersion;
  final bool calendar;
  final int openingBalanceMinor;
  final int reserveMinor;
  final int creditLimitMinor;
  final List<ForecastPoint> points;
  final List<PlannedEvent> events;
  final RiskResult risk;
  final List<String> qualityFlags;
  final Confidence confidence;
  final DateTime latestOperationDate;
  final List<HistoryPoint> history;
  final String? createdAt;
  final NettingView? netting;
  final List<OverdueItem> overdueEvents;

  DateTime get horizonEnd => points.isEmpty ? asOf : points.last.date;

  int get inflowMinor =>
      points.fold(0, (sum, point) => sum + point.inflowMinor);
  int get outflowMinor =>
      points.fold(0, (sum, point) => sum + point.outflowMinor);

  List<PlannedEvent> get obligatoryOut {
    final rows = events.where((event) {
      return event.direction == 'out' &&
          event.status != 'cancelled' &&
          event.status != 'paid' &&
          !event.expectedDate.isBefore(asOf);
    }).toList()..sort((a, b) => a.expectedDate.compareTo(b.expectedDate));
    return rows;
  }

  ForecastResult copyWith({
    String? runId,
    List<ForecastPoint>? points,
    List<PlannedEvent>? events,
    RiskResult? risk,
    int? eventVersion,
  }) {
    return ForecastResult(
      runId: runId ?? this.runId,
      accountId: accountId,
      asOf: asOf,
      model: model,
      modelVersion: modelVersion,
      trainedTo: trainedTo,
      dataVersion: dataVersion,
      eventVersion: eventVersion ?? this.eventVersion,
      calendar: calendar,
      openingBalanceMinor: openingBalanceMinor,
      reserveMinor: reserveMinor,
      creditLimitMinor: creditLimitMinor,
      points: points ?? this.points,
      events: events ?? this.events,
      risk: risk ?? this.risk,
      qualityFlags: qualityFlags,
      confidence: confidence,
      latestOperationDate: latestOperationDate,
      history: history,
      createdAt: createdAt,
      netting: netting,
      overdueEvents: overdueEvents,
    );
  }
}

class GapAction {
  const GapAction({
    required this.code,
    required this.text,
    this.amountMinor = 0,
  });

  factory GapAction.fromJson(Map<String, dynamic> json) => GapAction(
    code: (json['code'] ?? '').toString(),
    text: (json['text'] ?? '').toString(),
    amountMinor: asInt(json['amount_minor']),
  );

  final String code;
  final String text;
  final int amountMinor;

  /// The server writes one sentence, «what to do: why». The card shows the
  /// action as the title and only the rest below it, so nothing is said twice.
  String get title => _parts.$1;
  String get detail => _parts.$2;

  (String, String) get _parts {
    final colon = text.indexOf(': ');
    if (colon > 0) {
      return (text.substring(0, colon), _sentence(text.substring(colon + 2)));
    }
    final lead = switch (code) {
      'credit_line' => 'Оформить овердрафт или кредитную линию',
      'reserve' => 'Сформировать резерв',
      _ => null,
    };
    if (lead != null && text.startsWith('$lead ')) {
      return (lead, _sentence(text.substring(lead.length + 1)));
    }
    final fallback = switch (code) {
      'speed_up_client' => 'Попросить предоплату у клиента',
      'postpone_supplier' => 'Перенести платёж поставщику',
      'credit_line' => 'Подготовить кредитную линию',
      // The other reserve sentence says the existing reserve already covers the gap.
      'reserve' => 'Использовать резерв',
      _ => 'Что сделать',
    };
    return (fallback, text);
  }

  static String _sentence(String value) =>
      value.isEmpty ? value : value[0].toUpperCase() + value.substring(1);
}

class GapExplanation {
  const GapExplanation({
    required this.hasGap,
    required this.gapDate,
    required this.shortfallMinor,
    required this.when,
    required this.shortfall,
    required this.why,
    required this.actions,
    required this.text,
  });

  factory GapExplanation.fromJson(Map<String, dynamic> json) => GapExplanation(
    hasGap: asBool(json['has_gap']),
    gapDate: asDateOpt(json['gap_date']),
    shortfallMinor: asInt(json['shortfall_minor']),
    when: (json['when'] ?? '').toString(),
    shortfall: (json['shortfall'] ?? '').toString(),
    why: [
      for (final item in (json['why'] as List? ?? const [])) item.toString(),
    ],
    actions: [
      for (final item in (json['actions'] as List? ?? const []))
        GapAction.fromJson(item as Map<String, dynamic>),
    ],
    text: (json['text'] ?? '').toString(),
  );

  final bool hasGap;
  final DateTime? gapDate;
  final int shortfallMinor;
  final String when;
  final String shortfall;
  final List<String> why;
  final List<GapAction> actions;
  final String text;

  static const empty = GapExplanation(
    hasGap: false,
    gapDate: null,
    shortfallMinor: 0,
    when: '',
    shortfall: '',
    why: [],
    actions: [],
    text: '',
  );
}

class TopClient {
  const TopClient({
    required this.name,
    required this.inn,
    required this.amountMinor,
    required this.operations,
    this.lastDate,
    this.share,
  });

  factory TopClient.fromJson(Map<String, dynamic> json) => TopClient(
    name: (json['counterparty_name'] ?? '').toString(),
    inn: (json['counterparty_inn'] ?? '').toString(),
    amountMinor: asInt(json['amount_minor']),
    operations: asInt(json['operations']),
    lastDate: asDateOpt(json['last_date']),
    share: asDoubleOpt(json['share']),
  );

  final String name;
  final String inn;
  final int amountMinor;
  final int operations;
  final DateTime? lastDate;
  final double? share;

  String get label => name.isEmpty ? inn : name;
}

class ScenarioPart {
  const ScenarioPart({
    required this.kind,
    this.delayDays = 14,
    this.revenueDropPct = 20,
    this.amountMinor = 0,
    this.expenseDate,
    this.fixedCostPct = 10,
    this.startDate,
    this.counterparty,
    this.categories = const [],
  });

  factory ScenarioPart.fromJson(Map<String, dynamic> json) => ScenarioPart(
    kind: json['kind'].toString(),
    delayDays: asInt(json['delay_days'], 14),
    revenueDropPct: asDoubleOpt(json['revenue_drop_pct'])?.round() ?? 20,
    amountMinor: asInt(json['amount_minor']),
    expenseDate: asDateOpt(json['expense_date']),
    fixedCostPct: asDoubleOpt(json['fixed_cost_pct'])?.round() ?? 10,
    startDate: asDateOpt(json['start_date']),
    counterparty: json['counterparty']?.toString(),
    categories: [
      for (final item in (json['categories'] as List?) ?? const [])
        item.toString(),
    ],
  );

  final String kind;
  final int delayDays;
  final int revenueDropPct;
  final int amountMinor;
  final DateTime? expenseDate;
  final int fixedCostPct;
  final DateTime? startDate;
  final String? counterparty;
  final List<String> categories;

  Map<String, dynamic> toJson() => {
    'kind': kind,
    if (kind == 'client_delay' || kind == 'delay') 'delay_days': delayDays,
    if (kind == 'revenue_drop') 'revenue_drop_pct': revenueDropPct,
    if (kind == 'extra_expense') 'amount_minor': amountMinor,
    if (kind == 'extra_expense' && expenseDate != null)
      'expense_date': isoDate(expenseDate!),
    if (kind == 'fixed_cost_increase') 'fixed_cost_pct': fixedCostPct,
    if (kind == 'fixed_cost_increase' && startDate != null)
      'start_date': isoDate(startDate!),
    if (counterparty != null && counterparty!.isNotEmpty)
      'counterparty': counterparty,
    if (categories.isNotEmpty) 'categories': categories,
  };
}

class ScenarioSpec {
  const ScenarioSpec({
    required this.baseRunId,
    required this.kind,
    this.parts = const [],
    this.part,
  });

  final String baseRunId;
  final String kind;
  final List<ScenarioPart> parts;
  final ScenarioPart? part;

  Map<String, dynamic> toJson() {
    if (kind == 'combined') {
      return {
        'base_run_id': baseRunId,
        'kind': 'combined',
        'parts': [for (final item in parts) item.toJson()],
      };
    }
    final single = part ?? (parts.isNotEmpty ? parts.first : null);
    final body = <String, dynamic>{'base_run_id': baseRunId, 'kind': kind};
    if (single != null) {
      final fields = Map<String, dynamic>.from(single.toJson())..remove('kind');
      body.addAll(fields);
    }
    return body;
  }
}

class ScenarioResult {
  const ScenarioResult({
    required this.scenarioId,
    required this.baseRunId,
    required this.points,
    required this.events,
    required this.risk,
    required this.deltaEndMinor,
    required this.deltaLiquidityMinor,
    required this.baseFirstGapDate,
    required this.assumptions,
    this.specKind = '',
  });

  factory ScenarioResult.fromJson(Map<String, dynamic> json) => ScenarioResult(
    scenarioId: json['scenario_id'].toString(),
    baseRunId: json['base_run_id'].toString(),
    points: [
      for (final item in (json['points'] as List? ?? const []))
        ForecastPoint.fromJson(item as Map<String, dynamic>),
    ],
    events: [
      for (final item in (json['events'] as List? ?? const []))
        PlannedEvent.fromJson(item as Map<String, dynamic>),
    ],
    risk: RiskResult.fromJson(json['risk'] as Map<String, dynamic>),
    deltaEndMinor: asInt(json['delta_end_minor']),
    deltaLiquidityMinor: asInt(json['delta_liquidity_minor']),
    baseFirstGapDate: asDateOpt(json['base_first_gap_date']),
    assumptions: [
      for (final item in (json['assumptions'] as List? ?? const []))
        item.toString(),
    ],
    specKind: ((json['spec'] as Map?)?['kind'] ?? '').toString(),
  );

  final String scenarioId;
  final String baseRunId;
  final List<ForecastPoint> points;
  final List<PlannedEvent> events;
  final RiskResult risk;
  final int deltaEndMinor;
  final int deltaLiquidityMinor;
  final DateTime? baseFirstGapDate;
  final List<String> assumptions;
  final String specKind;
}

class TransactionItem {
  const TransactionItem({
    required this.operationId,
    required this.date,
    required this.amountMinor,
    required this.direction,
    required this.category,
    this.counterpartyName = '',
    this.counterpartyInn = '',
    this.purpose = '',
  });

  factory TransactionItem.fromJson(Map<String, dynamic> json) =>
      TransactionItem(
        operationId: (json['operation_id'] ?? '').toString(),
        date: asDate(json['operation_date']),
        amountMinor: asInt(json['amount_minor']),
        direction: (json['direction'] ?? 'out').toString(),
        category: (json['category'] ?? 'unknown').toString(),
        counterpartyName: (json['counterparty_name'] ?? '').toString(),
        counterpartyInn: (json['counterparty_inn'] ?? '').toString(),
        purpose: (json['payment_purpose'] ?? '').toString(),
      );

  final String operationId;
  final DateTime date;
  final int amountMinor;
  final String direction;
  final String category;
  final String counterpartyName;
  final String counterpartyInn;
  final String purpose;

  int get signedMinor => direction == 'out' ? -amountMinor : amountMinor;

  TransactionItem copyWith({String? category}) => TransactionItem(
    operationId: operationId,
    date: date,
    amountMinor: amountMinor,
    direction: direction,
    category: category ?? this.category,
    counterpartyName: counterpartyName,
    counterpartyInn: counterpartyInn,
    purpose: purpose,
  );
}

class FacetCount {
  const FacetCount({
    required this.code,
    required this.label,
    required this.count,
    this.group = '',
  });

  factory FacetCount.fromJson(Map<String, dynamic> json) => FacetCount(
    code: (json['code'] ?? '').toString(),
    label: (json['label'] ?? json['code'] ?? '').toString(),
    count: asInt(json['count']),
    group: (json['group'] ?? '').toString(),
  );

  final String code;
  final String label;
  final int count;
  final String group;
}

class CounterpartyFacet {
  const CounterpartyFacet({
    required this.name,
    required this.inn,
    required this.count,
    required this.amountMinor,
  });

  factory CounterpartyFacet.fromJson(Map<String, dynamic> json) =>
      CounterpartyFacet(
        name: (json['name'] ?? json['counterparty_name'] ?? '').toString(),
        inn: (json['inn'] ?? json['counterparty_inn'] ?? '').toString(),
        count: asInt(json['count']),
        amountMinor: asInt(json['amount_minor']),
      );

  final String name;
  final String inn;
  final int count;
  final int amountMinor;
}

class FlowTotals {
  const FlowTotals({
    required this.count,
    required this.inflowMinor,
    required this.outflowMinor,
  });

  factory FlowTotals.fromJson(Map<String, dynamic> json) => FlowTotals(
    count: asInt(json['count']),
    inflowMinor: asInt(json['inflow_minor']),
    outflowMinor: asInt(json['outflow_minor']),
  );

  final int count;
  final int inflowMinor;
  final int outflowMinor;

  int signedFor(String? direction) {
    if (direction == 'out') return -outflowMinor;
    if (direction == 'in') return inflowMinor;
    return inflowMinor - outflowMinor;
  }
}

class TransactionPage {
  const TransactionPage({
    required this.items,
    required this.page,
    required this.pageSize,
    required this.total,
    this.totals = const FlowTotals(count: 0, inflowMinor: 0, outflowMinor: 0),
    this.categories = const [],
    this.counterparties = const [],
  });

  factory TransactionPage.fromJson(Map<String, dynamic> json) {
    final totals = json['totals'];
    final facets = json['facets'];
    return TransactionPage(
      items: [
        for (final item in (json['items'] as List? ?? const []))
          if (item is Map<String, dynamic>) TransactionItem.fromJson(item),
      ],
      page: asInt(json['page'], 1),
      pageSize: asInt(json['page_size'], 50),
      total: asInt(json['total']),
      totals: totals is Map<String, dynamic>
          ? FlowTotals.fromJson(totals)
          : FlowTotals(
              count: asInt(json['total']),
              inflowMinor: 0,
              outflowMinor: 0,
            ),
      categories: [
        for (final item
            in ((facets is Map ? facets['categories'] : null) as List? ??
                const []))
          if (item is Map<String, dynamic>) FacetCount.fromJson(item),
      ],
      counterparties: [
        for (final item
            in ((facets is Map ? facets['counterparties'] : null) as List? ??
                const []))
          if (item is Map<String, dynamic>) CounterpartyFacet.fromJson(item),
      ],
    );
  }

  final List<TransactionItem> items;
  final int page;
  final int pageSize;
  final int total;
  final FlowTotals totals;
  final List<FacetCount> categories;
  final List<CounterpartyFacet> counterparties;
}

class PlanFactRow {
  const PlanFactRow({
    required this.date,
    required this.plannedMinor,
    required this.actualMinor,
    required this.deltaMinor,
    required this.plannedInMinor,
    required this.plannedOutMinor,
    required this.actualInMinor,
    required this.actualOutMinor,
    required this.coverage,
  });

  factory PlanFactRow.fromJson(Map<String, dynamic> json) => PlanFactRow(
    date: asDate(json['date']),
    plannedMinor: asInt(json['planned_minor']),
    actualMinor: asIntOpt(json['actual_minor']),
    deltaMinor: asIntOpt(json['delta_minor']),
    plannedInMinor: asInt(json['planned_in_minor']),
    plannedOutMinor: asInt(json['planned_out_minor']),
    actualInMinor: asIntOpt(json['actual_in_minor']),
    actualOutMinor: asIntOpt(json['actual_out_minor']),
    coverage: (json['coverage'] ?? '').toString(),
  );

  final DateTime date;
  final int plannedMinor;
  final int? actualMinor;
  final int? deltaMinor;
  final int plannedInMinor;
  final int plannedOutMinor;
  final int? actualInMinor;
  final int? actualOutMinor;
  final String coverage;
}

class PlanFact {
  const PlanFact({
    required this.runId,
    required this.rows,
    required this.matchingReview,
  });

  factory PlanFact.fromJson(Map<String, dynamic> json) => PlanFact(
    runId: (json['run_id'] ?? '').toString(),
    rows: [
      for (final item in (json['rows'] as List? ?? const []))
        PlanFactRow.fromJson(item as Map<String, dynamic>),
    ],
    matchingReview: [
      for (final item in (json['matching_review'] as List? ?? const []))
        if (item is Map<String, dynamic>) item,
    ],
  );

  final String runId;
  final List<PlanFactRow> rows;
  final List<Map<String, dynamic>> matchingReview;
}

class ImportReport {
  const ImportReport(this.lines);
  final List<String> lines;

  factory ImportReport.fromResponse(Object? body) {
    if (body is List) {
      return ImportReport(['Импортировано событий: ${body.length}']);
    }
    if (body is Map) {
      final lines = <String>[];
      if (body['inserted'] != null) {
        lines.add('Добавлено операций: ${body['inserted']}');
      }
      if (body['duplicates'] != null) {
        lines.add('Повторов: ${body['duplicates']}');
      }
      final touched = body['touched_accounts'];
      if (touched is List) lines.add('Счетов обновлено: ${touched.length}');
      if (body['status'] != null) lines.add('Статус: ${body['status']}');
      if (lines.isEmpty) lines.add('Импорт выполнен');
      return ImportReport(lines);
    }
    return const ImportReport(['Импорт выполнен']);
  }
}

class ForecastRequest {
  const ForecastRequest({
    required this.accountId,
    required this.asOf,
    this.model = 'hgb',
    this.calendar = true,
    this.reserveMinor = 0,
    this.creditLimitMinor = 0,
  });

  final String accountId;
  final DateTime asOf;
  final String model;
  final bool calendar;
  final int reserveMinor;
  final int creditLimitMinor;

  Map<String, dynamic> toJson() => {
    'account_id': accountId,
    'as_of': isoDate(asOf),
    'model': model,
    'calendar': calendar,
    'reserve_minor': reserveMinor,
    'credit_limit_minor': creditLimitMinor,
  };
}
