import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/api/v11.dart';
import 'package:kassandra/chart/balance_chart.dart';
import 'package:kassandra/chart/chart_scale.dart';
import 'package:kassandra/features/events/calendar_page.dart';
import 'package:kassandra/format/format.dart';

void main() {
  test('manual event is confirmed and carries certainty', () {
    final event = PlannedEvent(
      id: 'manual-1',
      accountId: 'C003',
      componentId: 'manual',
      category: 'revenue',
      amountMinor: 30000000,
      direction: 'in',
      dueDate: DateTime.utc(2026, 5, 20),
      expectedDate: DateTime.utc(2026, 5, 20),
      knownAt: DateTime.utc(2026, 4, 26),
      source: 'manual',
      status: 'confirmed',
      certainty: 'promised',
      label: 'Клиент обещал',
    );
    final json = event.toJson();
    expect(json['status'], 'confirmed');
    expect(json['certainty'], 'promised');
    expect(json.containsKey('assumption'), isFalse);
  });

  test('refresh toast names a closed gap and a shift', () {
    expect(
      refreshMessage(
        const RefreshChanges(
          gapAppeared: false,
          gapResolved: true,
          gapShiftDays: null,
          needDeltaMinor: 0,
          minimumDeltaMinor: 0,
        ),
      ),
      'Разрыв закрыт',
    );
    expect(
      refreshMessage(
        const RefreshChanges(
          gapAppeared: false,
          gapResolved: false,
          gapShiftDays: -6,
          needDeltaMinor: 100,
          minimumDeltaMinor: -100,
        ),
      ),
      'Разрыв сдвинулся на 6 дней раньше',
    );
  });

  test('y axis has 4 to 6 ticks and one below zero', () {
    final scale = ChartScale.fit(
      valuesMinor: const [-40000000, 0, 800000000],
      size: const Size(1000, 340),
    );
    expect(scale.ticks.length, inInclusiveRange(4, 6));
    expect(scale.ticks.any((tick) => tick < 0), isTrue);
    expect(scale.ticks.any((tick) => tick == 0), isTrue);
    expect(scale.step, 2000000);
  });

  test('a cash range near −1…8 million does not collapse to −5/0/5', () {
    final scale = ChartScale.fit(
      valuesMinor: const [-39202853, 843539444],
      size: const Size(1100, 340),
    );
    expect(scale.step, 2000000);
    expect(scale.ticks.length, inInclusiveRange(4, 7));
    expect(scale.ticks, isNot(equals(const [-5000000.0, 0.0, 5000000.0])));
  });

  test('gap caption moves left of a marker at the right edge', () {
    expect(gapLabelX(anchor: 40, labelWidth: 80, canvasWidth: 400), 4);
    final shifted = gapLabelX(anchor: 390, labelWidth: 120, canvasWidth: 400);
    expect(shifted + 120, lessThanOrEqualTo(390));
    expect(shifted, lessThan(390));
  });

  test('tax line includes the date and short name', () {
    expect(
      taxEventLine({
        'date': '2026-04-28',
        'label': 'Единый налоговый платёж',
        'amount_minor': 66086300,
      }),
      '28 апр · ЕНП · 660\u00A0863\u00A0₽',
    );
  });

  test('calendar keeps forecast repeats when the registry is empty', () {
    final salary = PlannedEvent(
      id: 'regular:salary',
      accountId: 'C003',
      componentId: 'salary',
      category: 'salary',
      amountMinor: 37430117,
      direction: 'out',
      dueDate: DateTime.utc(2026, 5, 4),
      expectedDate: DateTime.utc(2026, 5, 4),
      knownAt: DateTime.utc(2026, 4, 26),
      source: 'regular',
      status: 'assumption',
      label: 'Зарплата',
    );
    final shown = calendarEvents(registry: const [], forecast: [salary]);
    expect(shown, hasLength(1));
    expect(shown.single.source, 'regular');
  });

  test('operation count declines', () {
    expect(operationsPhrase(1), '1 операция');
    expect(operationsPhrase(2), '2 операции');
    expect(operationsPhrase(5), '5 операций');
    expect(operationsPhrase(523), '523 операции');
    expect(operationsPhrase(11), '11 операций');
  });

  test('close date labels keep a single one', () {
    final kept = ChartScale.dropOverlappingLabels(
      indexes: const [0, 1, 2],
      center: (index) => [0.0, 40.0, 48.0][index],
      width: (_) => 36,
      priority: (index) => index == 1 ? 2 : 1,
    );
    expect(kept.contains(1), isTrue);
    expect(kept.contains(2), isFalse);
  });
}
