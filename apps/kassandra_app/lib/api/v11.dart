import '../format/format.dart';
import 'models.dart';

class CodeLabel {
  const CodeLabel({required this.code, required this.label, this.group});

  factory CodeLabel.fromJson(Map<String, dynamic> json) => CodeLabel(
    code: (json['code'] ?? '').toString(),
    label: (json['label'] ?? json['code'] ?? '').toString(),
    group: json['group']?.toString(),
  );

  final String code;
  final String label;
  final String? group;
}

class Dictionaries {
  const Dictionaries({
    required this.categories,
    required this.sources,
    required this.certainty,
  });

  factory Dictionaries.fromJson(Map<String, dynamic> json) => Dictionaries(
    categories: [
      for (final item in (json['categories'] as List? ?? const []))
        if (item is Map<String, dynamic>) CodeLabel.fromJson(item),
    ],
    sources: [
      for (final item in (json['sources'] as List? ?? const []))
        if (item is Map<String, dynamic>) CodeLabel.fromJson(item),
    ],
    certainty: [
      for (final item in (json['certainty'] as List? ?? const []))
        if (item is Map<String, dynamic>) CodeLabel.fromJson(item),
    ],
  );

  final List<CodeLabel> categories;
  final List<CodeLabel> sources;
  final List<CodeLabel> certainty;

  String sourceLabel(String code) {
    for (final item in sources) {
      if (item.code == code) return item.label;
    }
    return 'Другой источник';
  }

  String categoryLabel(String code) {
    for (final item in categories) {
      if (item.code == code) return item.label;
    }
    return code;
  }
}

class RefreshChanges {
  const RefreshChanges({
    required this.gapAppeared,
    required this.gapResolved,
    required this.gapShiftDays,
    required this.needDeltaMinor,
    required this.minimumDeltaMinor,
  });

  factory RefreshChanges.fromJson(Map<String, dynamic> json) => RefreshChanges(
    gapAppeared: json['gap_appeared'] == true,
    gapResolved: json['gap_resolved'] == true,
    gapShiftDays: asIntOpt(json['gap_shift_days']),
    needDeltaMinor: asInt(json['need_delta_minor']),
    minimumDeltaMinor: asInt(json['minimum_delta_minor']),
  );

  final bool gapAppeared;
  final bool gapResolved;
  final int? gapShiftDays;
  final int needDeltaMinor;
  final int minimumDeltaMinor;
}

class RefreshResult {
  const RefreshResult({
    required this.mode,
    required this.changes,
    required this.forecast,
  });

  factory RefreshResult.fromJson(Map<String, dynamic> json) => RefreshResult(
    mode: (json['mode'] ?? 'unchanged').toString(),
    changes: RefreshChanges.fromJson(
      (json['changes'] as Map<String, dynamic>?) ?? const {},
    ),
    forecast: ForecastResult.fromJson(json['forecast'] as Map<String, dynamic>),
  );

  final String mode;
  final RefreshChanges changes;
  final ForecastResult forecast;
}

/// Toast after a refresh. A registered payment that only displaced the model's
/// average flow barely moves the gap, so the usual «прогноз обновлён» is misleading.
String refreshNotice(
  RefreshChanges changes, {
  int? removedMinor,
  String? categoryLabel,
  bool registered = false,
}) {
  final label = categoryLabel?.trim() ?? '';
  if (registered &&
      removedMinor != null &&
      removedMinor > 0 &&
      label.isNotEmpty &&
      changes.needDeltaMinor.abs() * 2 < removedMinor) {
    return 'Платёж убран. Модель снова ждёт обычные платежи по категории «$label» по среднему, поэтому нехватка почти не изменилась';
  }
  return refreshMessage(changes);
}

String refreshMessage(RefreshChanges changes) {
  if (changes.gapResolved) return 'Разрыв закрыт';
  if (changes.gapAppeared) return 'Появился кассовый разрыв';
  final shift = changes.gapShiftDays;
  if (shift != null && shift != 0) {
    if (shift < 0) {
      return 'Разрыв сдвинулся на ${daysWord(-shift)} раньше';
    }
    return 'Разрыв сдвинулся на ${daysWord(shift)} позже';
  }
  if (changes.needDeltaMinor > 0) {
    return 'Потребность выросла на ${formatMoneyShort(changes.needDeltaMinor, ceil: true)}';
  }
  if (changes.needDeltaMinor < 0) {
    return 'Потребность снизилась на ${formatMoneyShort(-changes.needDeltaMinor)}';
  }
  return 'Прогноз обновлён';
}

class StressItem {
  const StressItem({
    required this.eventId,
    required this.label,
    required this.expectedDate,
    required this.amountMinor,
    required this.reason,
  });

  factory StressItem.fromJson(Map<String, dynamic> json) => StressItem(
    eventId: (json['event_id'] ?? '').toString(),
    label: (json['label'] ?? '').toString(),
    expectedDate: asDate(json['expected_date']),
    amountMinor: asInt(json['amount_minor']),
    reason: (json['reason'] ?? '').toString(),
  );

  final String eventId;
  final String label;
  final DateTime expectedDate;
  final int amountMinor;
  final String reason;
}

class StressView {
  const StressView({
    required this.description,
    required this.excluded,
    required this.excludedMinor,
    required this.firstGapDate,
    required this.liquidityNeedMinor,
  });

  factory StressView.fromJson(Map<String, dynamic> json) => StressView(
    description: (json['description'] ?? '').toString(),
    excluded: [
      for (final item in (json['excluded'] as List? ?? const []))
        if (item is Map<String, dynamic>) StressItem.fromJson(item),
    ],
    excludedMinor: asInt(json['excluded_minor']),
    firstGapDate: asDateOpt(json['first_gap_date']),
    liquidityNeedMinor: asInt(json['liquidity_need_minor']),
  );

  final String description;
  final List<StressItem> excluded;
  final int excludedMinor;
  final DateTime? firstGapDate;
  final int liquidityNeedMinor;
}

class NettingView {
  const NettingView({required this.displacedMinor, required this.rule});

  factory NettingView.fromJson(Map<String, dynamic> json) => NettingView(
    displacedMinor: asInt(json['displaced_minor']),
    rule: (json['rule'] ?? '').toString(),
  );

  final int displacedMinor;
  final String rule;
}

class OverdueItem {
  const OverdueItem({
    required this.eventId,
    required this.label,
    required this.direction,
    required this.expectedDate,
    required this.remainingMinor,
    required this.treatment,
    this.newDate,
    this.note = '',
  });

  factory OverdueItem.fromJson(Map<String, dynamic> json) => OverdueItem(
    eventId: (json['event_id'] ?? '').toString(),
    label: (json['label'] ?? '').toString(),
    direction: (json['direction'] ?? 'out').toString(),
    expectedDate: asDate(json['expected_date']),
    remainingMinor: asInt(json['remaining_minor']),
    treatment: (json['treatment'] ?? 'excluded').toString(),
    newDate: asDateOpt(json['new_date']),
    note: (json['note'] ?? '').toString(),
  );

  final String eventId;
  final String label;
  final String direction;
  final DateTime expectedDate;
  final int remainingMinor;
  final String treatment;
  final DateTime? newDate;
  final String note;
}

class AlertAction {
  const AlertAction({
    required this.type,
    this.runId,
    this.eventId,
    this.scenario,
  });

  factory AlertAction.fromJson(Map<String, dynamic> json) => AlertAction(
    type: (json['type'] ?? '').toString(),
    runId: json['run_id']?.toString(),
    eventId: json['event_id']?.toString(),
    scenario: json['scenario'] is Map<String, dynamic>
        ? json['scenario'] as Map<String, dynamic>
        : null,
  );

  final String type;
  final String? runId;
  final String? eventId;
  final Map<String, dynamic>? scenario;
}

class AlertItem {
  const AlertItem({
    required this.kind,
    required this.severity,
    required this.title,
    required this.text,
    this.date,
    this.amountMinor = 0,
    this.action,
  });

  factory AlertItem.fromJson(Map<String, dynamic> json) => AlertItem(
    kind: (json['kind'] ?? '').toString(),
    severity: (json['severity'] ?? 'info').toString(),
    title: (json['title'] ?? '').toString(),
    text: (json['text'] ?? '').toString(),
    date: asDateOpt(json['date']),
    amountMinor: asInt(json['amount_minor']),
    action: json['action'] is Map<String, dynamic>
        ? AlertAction.fromJson(json['action'] as Map<String, dynamic>)
        : null,
  );

  final String kind;
  final String severity;
  final String title;
  final String text;
  final DateTime? date;
  final int amountMinor;
  final AlertAction? action;
}

class EventParse {
  const EventParse({
    required this.direction,
    required this.amountMinor,
    required this.expectedDate,
    required this.category,
    required this.label,
    required this.certainty,
    required this.counterpartyName,
    required this.missing,
    required this.notes,
  });

  factory EventParse.fromJson(Map<String, dynamic> json) {
    final draft = (json['draft'] as Map<String, dynamic>?) ?? const {};
    return EventParse(
      direction: draft['direction']?.toString(),
      amountMinor: asIntOpt(draft['amount_minor']),
      expectedDate: asDateOpt(draft['expected_date']),
      category: draft['category']?.toString(),
      label: draft['label']?.toString(),
      certainty: draft['certainty']?.toString(),
      counterpartyName: draft['counterparty_name']?.toString(),
      missing: [
        for (final item in (json['missing'] as List? ?? const []))
          item.toString(),
      ],
      notes: [
        for (final item in (json['notes'] as List? ?? const []))
          item.toString(),
      ],
    );
  }

  final String? direction;
  final int? amountMinor;
  final DateTime? expectedDate;
  final String? category;
  final String? label;
  final String? certainty;
  final String? counterpartyName;
  final List<String> missing;
  final List<String> notes;
}

class RegisterResult {
  const RegisterResult({
    required this.created,
    required this.revised,
    required this.unchanged,
    this.warning,
    this.lines = const [],
  });

  factory RegisterResult.fromJson(Map<String, dynamic> json) {
    final lines = <String>[];
    final warning = json['warning']?.toString();
    if (warning != null && warning.isNotEmpty) lines.add(warning);
    final note = json['note']?.toString();
    if (note != null && note.isNotEmpty) lines.add(note);
    final dates = json['payment_dates'];
    if (dates is List && dates.isNotEmpty) {
      lines.add('Ожидаемых дат оплаты: ${dates.length}');
    }
    final replaced = json['replaced_assumptions'];
    if (replaced is List && replaced.isNotEmpty) {
      lines.add('Заменено прогнозных платежей: ${replaced.length}');
    }
    return RegisterResult(
      created: asInt(json['created']),
      revised: asInt(json['revised']),
      unchanged: asInt(json['unchanged']),
      warning: warning,
      lines: lines,
    );
  }

  final int created;
  final int revised;
  final int unchanged;
  final String? warning;
  final List<String> lines;

  String get summary =>
      'Обновлено ${created + revised}, без изменений $unchanged';
}

class CounterpartyRisk {
  const CounterpartyRisk({
    required this.name,
    required this.inn,
    required this.statusLabel,
    required this.risk,
    this.delayDays = 28,
    this.scenarioKind = 'client_delay',
  });

  factory CounterpartyRisk.fromJson(Map<String, dynamic> json) {
    final check = json['check'];
    final scenario = json['suggested_scenario'];
    return CounterpartyRisk(
      name: (json['counterparty_name'] ?? '').toString(),
      inn: (json['counterparty_inn'] ?? '').toString(),
      statusLabel: check is Map
          ? (check['status_label'] ?? check['status'] ?? '').toString()
          : '',
      risk: check is Map ? (check['risk'] ?? '').toString() : '',
      delayDays: scenario is Map ? asInt(scenario['delay_days'], 28) : 28,
      scenarioKind: scenario is Map
          ? (scenario['kind'] ?? 'client_delay').toString()
          : 'client_delay',
    );
  }

  final String name;
  final String inn;
  final String statusLabel;
  final String risk;
  final int delayDays;
  final String scenarioKind;
}
