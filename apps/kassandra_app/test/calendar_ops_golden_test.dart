import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/fixture_api.dart';
import 'package:kassandra/app/providers.dart';
import 'package:kassandra/main.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final unbounded = FontLoader('Unbounded');
    for (final file in const [
      'Unbounded-Medium.ttf',
      'Unbounded-SemiBold.ttf',
      'Unbounded-Bold.ttf',
    ]) {
      unbounded.addFont(rootBundle.load('assets/fonts/$file'));
    }
    await unbounded.load();
    final golos = FontLoader('Golos Text');
    for (final file in const [
      'GolosText-Regular.ttf',
      'GolosText-Medium.ttf',
      'GolosText-SemiBold.ttf',
      'GolosText-Bold.ttf',
    ]) {
      golos.addFont(rootBundle.load('assets/fonts/$file'));
    }
    await golos.load();
    // Icons come from the SDK cache; without them goldens show empty boxes.
    final root = Platform.environment['FLUTTER_ROOT'];
    final icons = File(
      '$root/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
    );
    if (root != null && icons.existsSync()) {
      final bytes = icons.readAsBytesSync();
      await (FontLoader(
        'MaterialIcons',
      )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
    }
  });

  testWidgets('calendar and operations at 1440 and 390', (tester) async {
    await _boot(tester, const Size(1440, 1780));
    await _openCalendar(tester);
    expect(find.textContaining('около 13-го'), findsWidgets);
    expect(find.textContaining('реестре с риском'), findsOneWidget);
    expect(find.textContaining('данные:'), findsOneWidget);
    await expectLater(
      find.byType(KassandraRoot),
      matchesGoldenFile('goldens/calendar_1440.0.png'),
    );
    await _resize(tester, const Size(1440, 1240));
    await _openOperations(tester, phone: false);
    expect(find.textContaining('11,18'), findsNothing);
    expect(find.textContaining('63,3%'), findsWidgets);
    await expectLater(
      find.byType(KassandraRoot),
      matchesGoldenFile('goldens/operations_1440.0.png'),
    );

    await _boot(tester, const Size(390, 1688));
    await _openCalendar(tester);
    expect(find.text('30 дней'), findsOneWidget);
    expect(find.textContaining('Обязательные · 4'), findsOneWidget);
    expect(find.byKey(const Key('add-payment')), findsNothing);
    expect(find.byKey(const Key('add-event')), findsOneWidget);
    await expectLater(
      find.byType(KassandraRoot),
      matchesGoldenFile('goldens/calendar_390.0.png'),
    );
    await _openOperations(tester, phone: true);
    expect(find.textContaining('11,18'), findsWidgets);
    expect(find.textContaining('63,3%'), findsWidgets);
    await expectLater(
      find.byType(KassandraRoot),
      matchesGoldenFile('goldens/operations_390.0.png'),
    );
  });
}

Future<void> _openCalendar(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('nav-calendar')));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 300));
  await _until(tester, find.textContaining('1\u00A0942\u00A0820,27'));
  expect(find.textContaining('830\u00A0000'), findsWidgets);
  expect(find.textContaining('18 мая'), findsWidgets);
}

Future<void> _openOperations(WidgetTester tester, {required bool phone}) async {
  if (phone) {
    await tester.tap(find.byKey(const Key('nav-more')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.text('Операции'));
  } else {
    await tester.tap(find.byKey(const Key('nav-operations')));
  }
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 400));
  await _until(tester, find.textContaining('17,66'));
}

Future<void> _resize(WidgetTester tester, Size size) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  await tester.pump();
}

Future<void> _boot(WidgetTester tester, Size size) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    ProviderScope(
      key: UniqueKey(),
      overrides: [apiProvider.overrideWithValue(FixtureApi(sourcesDemo: true))],
      child: const KassandraRoot(),
    ),
  );
  await tester.runAsync(() async {
    await Future<void>.delayed(const Duration(milliseconds: 400));
  });
  final balance = find.textContaining('2\u00A0099\u00A0029');
  for (var i = 0; i < 40; i++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (balance.evaluate().isNotEmpty) return;
  }
  fail('Нет остатка. size=${tester.view.physicalSize}');
}

Future<void> _until(WidgetTester tester, Finder target) async {
  for (var i = 0; i < 30; i++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (target.evaluate().isNotEmpty) return;
  }
  final texts = find
      .byType(Text)
      .evaluate()
      .map((element) => (element.widget as Text).data)
      .whereType<String>()
      .take(24)
      .join(' | ');
  fail('Не дождались $target. texts=$texts');
}
