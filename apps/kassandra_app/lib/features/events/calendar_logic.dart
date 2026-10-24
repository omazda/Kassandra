import '../../api/models.dart';
import '../../api/views.dart';
import '../../format/format.dart';

/// Server-shaped filtering so the fixture and the unit tests agree with the API.
CalendarView applyCalendarFilters(CalendarView full, CalendarQuery query) {
  final items = [
    for (final item in full.items)
      if (_keep(item, query)) item,
  ]..sort((a, b) {
    final byDate = a.date.compareTo(b.date);
    if (byDate != 0) return byDate;
    return a.title.compareTo(b.title);
  });
  var inflow = 0;
  var outflow = 0;
  var active = 0;
  for (final item in items) {
    if (item.cancelled) continue;
    active++;
    if (item.direction == 'in') {
      inflow += item.amountMinor;
    } else {
      outflow += item.amountMinor;
    }
  }
  return CalendarView(
    runId: full.runId,
    accountId: full.accountId,
    asOf: full.asOf,
    dateTo: query.dateTo ?? full.dateTo,
    items: items,
    totals: FlowTotals(
      count: active,
      inflowMinor: inflow,
      outflowMinor: outflow,
    ),
    facets: full.facets,
  );
}

bool _keep(CalendarItem item, CalendarQuery query) {
  if (!query.includeCancelled && item.cancelled) return false;
  if (query.dateFrom != null && item.date.isBefore(dateOnly(query.dateFrom!))) {
    return false;
  }
  if (query.dateTo != null && item.date.isAfter(dateOnly(query.dateTo!))) {
    return false;
  }
  if (query.direction != null && item.direction != query.direction) return false;
  if (query.sources.isNotEmpty && !query.sources.contains(item.source)) {
    return false;
  }
  if (query.category != null &&
      query.category!.isNotEmpty &&
      item.category != query.category) {
    return false;
  }
  if (query.obligatory && !item.obligatory) return false;
  if (query.amountMin != null && item.amountMinor < query.amountMin!) {
    return false;
  }
  if (query.amountMax != null && item.amountMinor > query.amountMax!) {
    return false;
  }
  final needle = query.query.trim().toLowerCase();
  if (needle.isNotEmpty) {
    final haystack = [
      item.title,
      item.label,
      item.counterpartyName ?? '',
      item.counterpartyInn ?? '',
      item.categoryLabel,
    ].join(' ').toLowerCase();
    if (!haystack.contains(needle)) return false;
  }
  return true;
}

bool showsCounterpartyRisk(String risk) =>
    risk == 'critical' || risk == 'warning';

int activePayments(Iterable<CalendarItem> items, String direction) => items
    .where((item) => !item.cancelled && item.direction == direction)
    .length;

/// «данные: Банк, Счёт» or, when the forecast only repeats history, the short line.
String calendarSourcesLine(List<String> labels) {
  final names = [
    for (final label in labels)
      if (label.trim().isNotEmpty) label.trim(),
  ];
  if (names.isEmpty || (names.length == 1 && names.single == 'По истории')) {
    return 'платежи по истории счёта';
  }
  return 'данные: ${names.join(', ')}';
}

int? closingOn(List<ForecastPoint> points, DateTime date) {
  for (final point in points) {
    if (sameDate(point.date, date)) return point.closingBalanceMinor;
  }
  return null;
}

({int inflow, int outflow}) residualSums(List<ForecastPoint> points) {
  var inflow = 0;
  var outflow = 0;
  for (final point in points) {
    inflow += point.residualInMinor;
    outflow += point.residualOutMinor;
  }
  return (inflow: inflow, outflow: outflow);
}

/// How a moved payment changes one evening balance.
///
/// An outflow moved later raises the balance on `[old, new)`. Moved earlier,
/// it lowers `[new, old)`. An inflow does the opposite.
int balanceDelta({
  required DateTime day,
  required DateTime oldDate,
  required DateTime newDate,
  required String direction,
  required int amountMinor,
}) {
  if (sameDate(oldDate, newDate) || amountMinor == 0) return 0;
  final later = newDate.isAfter(oldDate);
  final start = later ? oldDate : newDate;
  final end = later ? newDate : oldDate;
  final inside = !day.isBefore(dateOnly(start)) && day.isBefore(dateOnly(end));
  if (!inside) return 0;
  final outflow = direction == 'out';
  if (later) return outflow ? amountMinor : -amountMinor;
  return outflow ? -amountMinor : amountMinor;
}

DateTime? firstNegativeDay(
  List<ForecastPoint> points,
  int Function(ForecastPoint point) balance,
) {
  final ordered = [...points]..sort((a, b) => a.date.compareTo(b.date));
  for (final point in ordered) {
    if (balance(point) < 0) return point.date;
  }
  return null;
}

class MovePreview {
  const MovePreview({
    required this.anchor,
    required this.beforeMinor,
    required this.afterMinor,
    required this.oldGap,
    required this.newGap,
    required this.stillBeforeGap,
  });

  final DateTime anchor;
  final int beforeMinor;
  final int afterMinor;
  final DateTime? oldGap;
  final DateTime? newGap;
  final bool stillBeforeGap;

  String get text {
    final balance =
        '${formatDayMonth(anchor)} на счёте будет ${formatMoneyTrunc(afterMinor)} вместо ${formatMoneyTrunc(beforeMinor)}.';
    return '$balance ${_gapSentence(oldGap, newGap, stillBeforeGap)}';
  }
}

String _gapSentence(DateTime? oldGap, DateTime? newGap, bool stillBefore) {
  if (oldGap == null && newGap == null) return 'Разрыва нет.';
  if (oldGap != null && newGap == null) {
    return 'Разрыв ${formatDayMonth(oldGap)} закроется.';
  }
  if (oldGap == null && newGap != null) {
    return 'Появится разрыв ${formatDayMonth(newGap)}.';
  }
  if (sameDate(oldGap!, newGap!)) {
    if (stillBefore) {
      return 'Разрыв ${formatDayMonth(oldGap)} останется: платёж всё равно уходит до него.';
    }
    return 'Разрыв ${formatDayMonth(oldGap)} останется.';
  }
  return 'Разрыв сдвинется с ${formatDayMonth(oldGap)} на ${formatDayMonth(newGap)}.';
}

MovePreview? previewMove({
  required List<ForecastPoint> points,
  required DateTime oldDate,
  required DateTime newDate,
  required String direction,
  required int amountMinor,
}) {
  if (sameDate(oldDate, newDate) || points.isEmpty) return null;
  int adjusted(ForecastPoint point) =>
      point.closingBalanceMinor +
      balanceDelta(
        day: point.date,
        oldDate: oldDate,
        newDate: newDate,
        direction: direction,
        amountMinor: amountMinor,
      );

  final oldGap = firstNegativeDay(points, (point) => point.closingBalanceMinor);
  final newGap = firstNegativeDay(points, adjusted);
  final oldPoint = _pointOn(points, oldDate);
  final newPoint = _pointOn(points, newDate);
  final anchorPoint = oldPoint != null && adjusted(oldPoint) != oldPoint.closingBalanceMinor
      ? oldPoint
      : newPoint ?? oldPoint;
  if (anchorPoint == null) return null;
  final gap = oldGap;
  final stillBefore = gap != null && !newDate.isAfter(dateOnly(gap));
  return MovePreview(
    anchor: anchorPoint.date,
    beforeMinor: anchorPoint.closingBalanceMinor,
    afterMinor: adjusted(anchorPoint),
    oldGap: oldGap,
    newGap: newGap,
    stillBeforeGap: stillBefore,
  );
}

ForecastPoint? _pointOn(List<ForecastPoint> points, DateTime date) {
  for (final point in points) {
    if (sameDate(point.date, date)) return point;
  }
  return null;
}

String rhythmCaption(RecurringItem item, DateTime date) {
  switch (item.rhythm) {
    case 'monthly':
      return 'каждый месяц около ${date.day}-го';
    case 'weekly':
      return 'каждую неделю';
    case 'fortnight':
      return 'раз в две недели';
    default:
      return item.rhythmLabel;
  }
}

/// Fields that actually differ. `known_at` is added by the caller.
Map<String, dynamic> eventChanges({
  required CalendarItem item,
  required DateTime date,
  required int amountMinor,
  required String category,
  required String counterparty,
  required String label,
  required String? certainty,
}) {
  final changed = <String, dynamic>{};
  if (!sameDate(date, item.date)) changed['expected_date'] = isoDate(date);
  if (amountMinor != item.amountMinor && amountMinor > 0) {
    changed['amount_minor'] = amountMinor;
  }
  if (!item.assumption) {
    if (category.isNotEmpty && category != item.category) {
      changed['category'] = category;
    }
    final name = counterparty.trim();
    if (name != (item.counterpartyName ?? '').trim()) {
      changed['counterparty_name'] = name;
    }
    if (certainty != null && certainty.isNotEmpty && certainty != item.certainty) {
      changed['certainty'] = certainty;
    }
  }
  if (label.trim().isNotEmpty && label.trim() != item.label.trim()) {
    changed['label'] = label.trim();
  }
  return changed;
}

Map<String, dynamic> assumptionChanges({
  required CalendarItem item,
  required DateTime date,
  required int amountMinor,
  required String label,
}) {
  final changed = eventChanges(
    item: item,
    date: date,
    amountMinor: amountMinor,
    category: item.category,
    counterparty: item.counterpartyName ?? '',
    label: label,
    certainty: item.certainty,
  );
  changed.remove('category');
  changed.remove('counterparty_name');
  changed.remove('certainty');
  return changed;
}
