import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/chart/series.dart';
import 'package:kassandra/design/tokens.dart';

void main() {
  test('daily history never passes as_of', () {
    final raw = jsonDecode(
      File('assets/fixtures/c003_2026-04-26.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final daily = [
      for (final item in raw['daily_tail'] as List)
        DailyPoint.fromJson(item as Map<String, dynamic>),
    ];
    final asOf = DateTime.utc(2026, 4, 26);
    final filtered = filterDailyUntil(daily, asOf);
    expect(filtered.length, lessThan(daily.length));
    expect(filtered.last.date, asOf);
    expect(filtered.every((point) => !point.date.isAfter(asOf)), isTrue);
    expect(daily.any((point) => point.date.isAfter(asOf)), isTrue);
  });

  test('the 90-day series is cut on as_of', () {
    final raw = jsonDecode(
      File('assets/fixtures/c003_2026-04-26.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final forecast = ForecastResult.fromJson(
      raw['forecast'] as Map<String, dynamic>,
    );
    final daily = [
      for (final item in raw['daily_tail'] as List)
        DailyPoint.fromJson(item as Map<String, dynamic>),
    ];
    final series = buildBalanceSeries(
      asOf: forecast.asOf,
      history: forecast.history,
      daily: daily,
      points: forecast.points,
      openingMinor: forecast.openingBalanceMinor,
      window: HistoryWindow.days90,
    );
    final facts = series.where((point) => point.factMinor != null);
    expect(facts.every((point) => !point.date.isAfter(forecast.asOf)), isTrue);
    expect(series.last.date.isAfter(forecast.asOf), isTrue);
  });

  test('logo center sits on the capital K', () {
    final metrics = LogoMetrics.of(22);
    expect(metrics.markHeight, closeTo(22 * 0.57, 0.001));
    expect(metrics.markWidth, closeTo(22 * 0.57 * 48 / 25.5, 0.001));
    expect(metrics.gap, closeTo(22 * 0.24, 0.001));
    expect(metrics.centerAboveBaseline, closeTo(22 * 0.375, 0.001));
  });
}
