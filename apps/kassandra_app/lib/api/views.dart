import 'models.dart';

class CalendarItem {
  const CalendarItem({
    required this.id,
    required this.date,
    required this.dueDate,
    required this.direction,
    required this.amountMinor,
    required this.label,
    required this.title,
    required this.componentId,
    required this.category,
    required this.categoryLabel,
    required this.group,
    required this.source,
    required this.sourceLabel,
    required this.sourceShort,
    required this.kind,
    required this.status,
    required this.certainty,
    required this.certaintyLabel,
    required this.counterpartyName,
    required this.counterpartyInn,
    required this.obligatory,
    required this.overdue,
    required this.actions,
  });

  factory CalendarItem.fromJson(Map<String, dynamic> json) => CalendarItem(
    id: (json['id'] ?? '').toString(),
    date: asDate(json['date']),
    dueDate: asDate(json['due_date'] ?? json['date']),
    direction: (json['direction'] ?? 'out').toString(),
    amountMinor: asInt(json['amount_minor']),
    label: (json['label'] ?? '').toString(),
    title: (json['title'] ?? json['label'] ?? '').toString(),
    componentId: (json['component_id'] ?? '').toString(),
    category: (json['category'] ?? '').toString(),
    categoryLabel: (json['category_label'] ?? '').toString(),
    group: (json['group'] ?? '').toString(),
    source: (json['source'] ?? '').toString(),
    sourceLabel: (json['source_label'] ?? '').toString(),
    sourceShort: (json['source_short'] ?? '').toString(),
    kind: (json['kind'] ?? 'registered').toString(),
    status: (json['status'] ?? '').toString(),
    certainty: json['certainty']?.toString(),
    certaintyLabel: json['certainty_label']?.toString(),
    counterpartyName: json['counterparty_name']?.toString(),
    counterpartyInn: json['counterparty_inn']?.toString(),
    obligatory: json['obligatory'] == true,
    overdue: json['overdue'] == true,
    actions: [
      for (final item in (json['actions'] as List? ?? const []))
        item.toString(),
    ],
  );

  final String id;
  final DateTime date;
  final DateTime dueDate;
  final String direction;
  final int amountMinor;
  final String label;
  final String title;
  final String componentId;
  final String category;
  final String categoryLabel;
  final String group;
  final String source;
  final String sourceLabel;
  final String sourceShort;
  final String kind;
  final String status;
  final String? certainty;
  final String? certaintyLabel;
  final String? counterpartyName;
  final String? counterpartyInn;
  final bool obligatory;
  final bool overdue;
  final List<String> actions;

  bool get assumption => kind == 'assumption';
  bool get cancelled => status == 'cancelled';
  int get signedMinor => direction == 'out' ? -amountMinor : amountMinor;
  bool hasAction(String action) => actions.contains(action);

  /// Title without a trailing « · counterparty»: the line below already names it.
  String get shortTitle {
    final who = (counterpartyName ?? '').trim();
    final suffix = ' · $who';
    if (who.isNotEmpty &&
        title.endsWith(suffix) &&
        title.length > suffix.length) {
      return title.substring(0, title.length - suffix.length);
    }
    return title;
  }

  /// Counterparty and category, skipping the category when the title already says it
  /// («Зарплатный проект: зарплата» · «Зарплата»).
  String get metaLine {
    final who = (counterpartyName ?? '').trim();
    final category = _repeats(title, categoryLabel) ? '' : categoryLabel.trim();
    if (who.isEmpty) return category;
    if (category.isEmpty) return who;
    return '$who · $category';
  }

  /// The certainty note only adds something for manual entries; for documents it
  /// restates the source («Договор» · «подписан договор»).
  bool get showsCertainty =>
      source == 'manual' && (certaintyLabel ?? '').trim().isNotEmpty;
}

String _plain(String value) => value.toLowerCase().replaceAll('ё', 'е');

/// True when [text] already contains [label] or the stem of its first word.
bool _repeats(String text, String label) {
  final haystack = _plain(text);
  final needle = _plain(label.trim());
  if (needle.isEmpty) return false;
  if (haystack.contains(needle)) return true;
  final first = needle.split(RegExp(r'[\s,]+')).first;
  // Cut the ending so «зарплата» matches «зарплатный», «предоплата» — «предоплаты».
  return first.length >= 5 &&
      haystack.contains(first.substring(0, first.length - 2));
}

class CalendarFacets {
  const CalendarFacets({
    required this.sources,
    required this.categories,
    required this.directions,
    required this.obligatory,
    required this.cancelled,
  });

  factory CalendarFacets.fromJson(Map<String, dynamic> json) => CalendarFacets(
    sources: _facets(json['sources']),
    categories: _facets(json['categories']),
    directions: _facets(json['directions']),
    obligatory: asInt(json['obligatory']),
    cancelled: asInt(json['cancelled']),
  );

  final List<FacetCount> sources;
  final List<FacetCount> categories;
  final List<FacetCount> directions;
  final int obligatory;
  final int cancelled;
}

List<FacetCount> _facets(Object? raw) => [
  for (final item in (raw as List? ?? const []))
    if (item is Map<String, dynamic>) FacetCount.fromJson(item),
];

class CalendarView {
  const CalendarView({
    required this.runId,
    required this.accountId,
    required this.asOf,
    required this.dateTo,
    required this.items,
    required this.totals,
    required this.facets,
  });

  factory CalendarView.fromJson(Map<String, dynamic> json) => CalendarView(
    runId: (json['run_id'] ?? '').toString(),
    accountId: (json['account_id'] ?? '').toString(),
    asOf: asDate(json['as_of']),
    dateTo: asDate(json['date_to']),
    items: [
      for (final item in (json['items'] as List? ?? const []))
        if (item is Map<String, dynamic>) CalendarItem.fromJson(item),
    ],
    totals: FlowTotals.fromJson(
      (json['totals'] as Map<String, dynamic>?) ?? const {},
    ),
    facets: CalendarFacets.fromJson(
      (json['facets'] as Map<String, dynamic>?) ?? const {},
    ),
  );

  final String runId;
  final String accountId;
  final DateTime asOf;
  final DateTime dateTo;
  final List<CalendarItem> items;
  final FlowTotals totals;
  final CalendarFacets facets;
}

class CalendarQuery {
  const CalendarQuery({
    this.dateFrom,
    this.dateTo,
    this.direction,
    this.sources = const [],
    this.category,
    this.obligatory = false,
    this.includeCancelled = false,
    this.query = '',
    this.amountMin,
    this.amountMax,
  });

  final DateTime? dateFrom;
  final DateTime? dateTo;
  final String? direction;
  final List<String> sources;
  final String? category;
  final bool obligatory;
  final bool includeCancelled;
  final String query;
  final int? amountMin;
  final int? amountMax;

  Map<String, dynamic> toParams() => {
    if (dateFrom != null) 'date_from': isoDate(dateFrom!),
    if (dateTo != null) 'date_to': isoDate(dateTo!),
    if (direction != null) 'direction': direction,
    if (sources.isNotEmpty) 'source': sources,
    if (category != null && category!.isNotEmpty) 'category': category,
    if (obligatory) 'obligatory': true,
    if (includeCancelled) 'include_cancelled': true,
    if (query.trim().isNotEmpty) 'q': query.trim(),
    if (amountMin != null) 'amount_min': amountMin,
    if (amountMax != null) 'amount_max': amountMax,
  };
}

class AmountRow {
  const AmountRow({
    required this.direction,
    required this.label,
    required this.amountMinor,
    required this.operations,
    required this.share,
    this.category = '',
    this.group = '',
  });

  factory AmountRow.fromJson(Map<String, dynamic> json) => AmountRow(
    direction: (json['direction'] ?? 'out').toString(),
    label: (json['label'] ?? '').toString(),
    amountMinor: asInt(json['amount_minor']),
    operations: asInt(json['operations']),
    share: asDouble(json['share']),
    category: (json['category'] ?? '').toString(),
    group: (json['group'] ?? '').toString(),
  );

  final String direction;
  final String label;
  final int amountMinor;
  final int operations;
  final double share;
  final String category;
  final String group;
}

double asDouble(Object? value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0;
}

class CategorySummary {
  const CategorySummary({
    required this.dateFrom,
    required this.dateTo,
    required this.inflowMinor,
    required this.outflowMinor,
    required this.rows,
    required this.groups,
  });

  factory CategorySummary.fromJson(Map<String, dynamic> json) =>
      CategorySummary(
        dateFrom: asDate(json['date_from']),
        dateTo: asDate(json['date_to']),
        inflowMinor: asInt(json['inflow_minor']),
        outflowMinor: asInt(json['outflow_minor']),
        rows: [
          for (final item in (json['rows'] as List? ?? const []))
            if (item is Map<String, dynamic>) AmountRow.fromJson(item),
        ],
        groups: [
          for (final item in (json['groups'] as List? ?? const []))
            if (item is Map<String, dynamic>) AmountRow.fromJson(item),
        ],
      );

  final DateTime dateFrom;
  final DateTime dateTo;
  final int inflowMinor;
  final int outflowMinor;
  final List<AmountRow> rows;
  final List<AmountRow> groups;

  int totalFor(String direction) =>
      direction == 'in' ? inflowMinor : outflowMinor;
}

class TransactionQuery {
  const TransactionQuery({
    this.dateFrom,
    this.dateTo,
    this.direction,
    this.categories = const [],
    this.group,
    this.counterpartyInn,
    this.query = '',
    this.amountMin,
    this.amountMax,
    this.sort = 'date_desc',
    this.page = 1,
    this.pageSize = 50,
  });

  final DateTime? dateFrom;
  final DateTime? dateTo;
  final String? direction;
  final List<String> categories;
  final String? group;
  final String? counterpartyInn;
  final String query;
  final int? amountMin;
  final int? amountMax;
  final String sort;
  final int page;
  final int pageSize;

  Map<String, dynamic> toParams() => {
    if (dateFrom != null) 'date_from': isoDate(dateFrom!),
    if (dateTo != null) 'date_to': isoDate(dateTo!),
    if (direction != null) 'direction': direction,
    if (categories.isNotEmpty) 'category': categories,
    if (group != null && group!.isNotEmpty) 'group': group,
    if (counterpartyInn != null && counterpartyInn!.isNotEmpty)
      'counterparty_inn': counterpartyInn,
    if (query.trim().isNotEmpty) 'q': query.trim(),
    if (amountMin != null) 'amount_min': amountMin,
    if (amountMax != null) 'amount_max': amountMax,
    'sort': sort,
    'page': page,
    'page_size': pageSize,
  };

  Map<String, dynamic> summaryParams() => {
    if (dateFrom != null) 'date_from': isoDate(dateFrom!),
    if (dateTo != null) 'date_to': isoDate(dateTo!),
    if (direction != null) 'direction': direction,
    if (group != null && group!.isNotEmpty) 'group': group,
    if (counterpartyInn != null && counterpartyInn!.isNotEmpty)
      'counterparty_inn': counterpartyInn,
    if (query.trim().isNotEmpty) 'q': query.trim(),
    if (amountMin != null) 'amount_min': amountMin,
    if (amountMax != null) 'amount_max': amountMax,
  };
}

class RecurringItem {
  const RecurringItem({
    required this.componentId,
    required this.label,
    required this.rhythm,
    required this.rhythmLabel,
    required this.direction,
    required this.amountMinor,
  });

  factory RecurringItem.fromJson(Map<String, dynamic> json) => RecurringItem(
    componentId: (json['component_id'] ?? '').toString(),
    label: (json['label'] ?? '').toString(),
    rhythm: (json['rhythm'] ?? '').toString(),
    rhythmLabel: (json['rhythm_label'] ?? '').toString(),
    direction: (json['direction'] ?? 'out').toString(),
    amountMinor: asInt(json['amount_minor']),
  );

  final String componentId;
  final String label;
  final String rhythm;
  final String rhythmLabel;
  final String direction;
  final int amountMinor;
}

String? assumptionEventId(String message) {
  const marker = 'событие ';
  final index = message.lastIndexOf(marker);
  if (index < 0) return null;
  final id = message.substring(index + marker.length).trim();
  return id.isEmpty ? null : id;
}
