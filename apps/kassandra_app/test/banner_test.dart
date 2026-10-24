import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/app/theme.dart';
import 'package:kassandra/design/tokens.dart';
import 'package:kassandra/features/forecast/forecast_page.dart';

RiskResult risk({
  int current = 0,
  DateTime? gap,
  bool fresh = true,
  double? probability = 0.77,
  String calibration = 'experimental_uncalibrated',
  int minimum = -39202853,
  int need = 39202853,
}) {
  return RiskResult(
    thresholdMinor: 0,
    currentGapMinor: current,
    newRisk: fresh,
    firstGapDate: gap,
    minimumBalanceMinor: minimum,
    liquidityNeedMinor: need,
    probability30d: probability,
    calibrationStatus: calibration,
    probabilitySource: '',
    trajectoryCount: 0,
    simulations: 1000,
    limitation: 'Оценка недоступна для этого среза',
  );
}

ForecastResult forecastWith(
  RiskResult value, {
  bool refuse = false,
  List<PlannedEvent> events = const [],
}) {
  final day = DateTime.utc(2026, 4, 27);
  return ForecastResult(
    runId: 'run',
    accountId: 'C003',
    asOf: DateTime.utc(2026, 4, 26),
    model: 'hgb',
    modelVersion: 'hgb',
    dataVersion: 1,
    eventVersion: 0,
    calendar: true,
    openingBalanceMinor: 209902886,
    reserveMinor: 0,
    creditLimitMinor: 0,
    points: [
      for (var i = 0; i < 30; i++)
        ForecastPoint(
          date: day.add(Duration(days: i)),
          eventInMinor: 0,
          eventOutMinor: 0,
          residualInMinor: 0,
          residualOutMinor: 0,
          residualRevenueMinor: 0,
          closingBalanceMinor: i == 29 ? value.minimumBalanceMinor : 100,
        ),
    ],
    events: events,
    risk: value,
    qualityFlags: const ['Флаг качества'],
    confidence: Confidence(
      refuse: refuse,
      label: 'Высокое доверие',
      limitation: 'История короткая',
    ),
    latestOperationDate: DateTime.utc(2026, 4, 26),
    history: const [],
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Widget host(Widget child) {
    return ProviderScope(
      child: MaterialApp(
        theme: buildKassandraTheme(KassandraColors.light, Brightness.light),
        home: Scaffold(body: SingleChildScrollView(child: child)),
      ),
    );
  }

  testWidgets('three banner states', (tester) async {
    await tester.pumpWidget(
      host(
        GapBanner(
          forecast: forecastWith(risk(gap: DateTime.utc(2026, 5, 21))),
          explanation: const GapExplanation(
            hasGap: true,
            gapDate: null,
            shortfallMinor: 1,
            when: '',
            shortfall: '393 тыс. ₽',
            why: ['Зарплата'],
            actions: [],
            text: '',
          ),
        ),
      ),
    );
    expect(find.textContaining('не хватит денег'), findsOneWidget);

    await tester.pumpWidget(
      host(
        GapBanner(
          forecast: forecastWith(
            risk(
              current: 5000000,
              gap: DateTime.utc(2026, 4, 26),
              fresh: false,
            ),
          ),
          explanation: GapExplanation.empty,
        ),
      ),
    );
    expect(find.textContaining('уже не хватает'), findsOneWidget);

    await tester.pumpWidget(
      host(
        GapBanner(
          forecast: forecastWith(
            risk(
              gap: null,
              fresh: false,
              minimum: 1000,
              need: 0,
              probability: null,
              calibration: 'unavailable',
            ),
          ),
          explanation: GapExplanation.empty,
        ),
      ),
    );
    expect(find.textContaining('Денег хватает'), findsWidgets);
  });

  testWidgets('null probability hides the ring', (tester) async {
    await tester.pumpWidget(
      host(
        ProbabilityBlock(
          risk: risk(probability: null, calibration: 'unavailable'),
        ),
      ),
    );
    expect(find.text('Вероятность недоступна'), findsOneWidget);
    expect(find.textContaining('Оценка недоступна'), findsOneWidget);
  });

  testWidgets('refuse replaces the forecast', (tester) async {
    await tester.pumpWidget(
      host(const RefuseView(text: 'История слишком короткая')),
    );
    expect(find.text('Прогноз не построен'), findsOneWidget);
    expect(find.text('История слишком короткая'), findsOneWidget);
  });

  testWidgets('empty payments offer to add one', (tester) async {
    await tester.pumpWidget(
      host(
        PaymentsBlock(
          forecast: forecastWith(risk(gap: DateTime.utc(2026, 5, 21))),
        ),
      ),
    );
    expect(find.text('Обязательных платежей не найдено'), findsOneWidget);
    expect(find.text('Добавить событие'), findsOneWidget);
  });
}
