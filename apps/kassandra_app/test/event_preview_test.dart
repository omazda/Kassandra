import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/features/events/event_preview.dart';
import 'package:kassandra/format/format.dart';

void main() {
  test('300 000 ₽ on 20 May moves the gap to 25 May and 92 thousand', () {
    final raw = jsonDecode(
      File('assets/fixtures/c003_2026-04-26.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final forecast = ForecastResult.fromJson(
      raw['forecast'] as Map<String, dynamic>,
    );
    final preview = previewEvent(
      points: forecast.points,
      openingMinor: forecast.openingBalanceMinor,
      thresholdMinor: forecast.risk.thresholdMinor,
      expectedDate: DateTime.utc(2026, 5, 20),
      signedMinor: 30000000,
    );
    expect(preview.gapDate, DateTime.utc(2026, 5, 25));
    expect(formatMoneyShort(preview.liquidityNeedMinor), '92 тыс.\u00A0₽');
    expect(preview.insideHorizon, isTrue);
  });
}
