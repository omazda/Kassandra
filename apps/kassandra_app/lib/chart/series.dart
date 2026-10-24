import '../api/models.dart';

enum HistoryWindow { days30, days90, year }

/// Drops every daily row after [asOf]. `/daily` includes the archive future.
List<DailyPoint> filterDailyUntil(List<DailyPoint> daily, DateTime asOf) {
  return [
    for (final point in daily)
      if (!point.date.isAfter(asOf)) point,
  ];
}

class SeriesPoint {
  const SeriesPoint({
    required this.date,
    this.factMinor,
    this.forecastMinor,
    this.p10Minor,
    this.p90Minor,
    this.isToday = false,
  });

  final DateTime date;
  final int? factMinor;
  final int? forecastMinor;
  final int? p10Minor;
  final int? p90Minor;
  final bool isToday;
}

List<SeriesPoint> buildBalanceSeries({
  required DateTime asOf,
  required List<HistoryPoint> history,
  required List<DailyPoint> daily,
  required List<ForecastPoint> points,
  required int openingMinor,
  required HistoryWindow window,
}) {
  final past = <HistoryPoint>[];
  if (window == HistoryWindow.days30) {
    final filtered = [
      for (final point in history)
        if (!point.date.isAfter(asOf)) point,
    ];
    past.addAll(
      filtered.length > 30 ? filtered.sublist(filtered.length - 30) : filtered,
    );
  } else {
    final filtered = filterDailyUntil(daily, asOf);
    final limit = window == HistoryWindow.days90 ? 90 : 365;
    final slice = filtered.length > limit
        ? filtered.sublist(filtered.length - limit)
        : filtered;
    past.addAll([
      for (final point in slice)
        HistoryPoint(date: point.date, balanceMinor: point.balanceMinor),
    ]);
  }

  final series = <SeriesPoint>[
    for (final point in past)
      SeriesPoint(
        date: point.date,
        factMinor: point.balanceMinor,
        forecastMinor: sameDate(point.date, asOf) ? openingMinor : null,
        isToday: sameDate(point.date, asOf),
      ),
  ];

  if (series.isEmpty || !series.last.isToday) {
    series.add(
      SeriesPoint(
        date: asOf,
        factMinor: openingMinor,
        forecastMinor: openingMinor,
        isToday: true,
      ),
    );
  }

  for (final point in points) {
    if (!point.date.isAfter(asOf)) continue;
    series.add(
      SeriesPoint(
        date: point.date,
        forecastMinor: point.closingBalanceMinor,
        p10Minor: point.p10Minor,
        p90Minor: point.p90Minor,
      ),
    );
  }
  return series;
}

List<int> seriesValuesMinor(List<SeriesPoint> series, int thresholdMinor) {
  final values = <int>[thresholdMinor];
  for (final point in series) {
    if (point.factMinor != null) values.add(point.factMinor!);
    if (point.forecastMinor != null) values.add(point.forecastMinor!);
    if (point.p10Minor != null) values.add(point.p10Minor!);
    if (point.p90Minor != null) values.add(point.p90Minor!);
  }
  return values;
}
