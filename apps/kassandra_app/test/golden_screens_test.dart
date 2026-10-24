import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/main.dart';

/// Flutter renders of the P0 screens at the design widths.
/// Browser PNGs in design/screens/ stay the visual reference; Skia and the
/// browser do not rasterize Unbounded the same way, so the committed goldens
/// are the Flutter frames.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('forecast, gap, event and scenarios at 1440 and 390', (
    tester,
  ) async {
    for (final width in const [1440.0, 390.0]) {
      final height = width == 390 ? 844.0 : 900.0;
      await _boot(tester, Size(width, height));
      await expectLater(
        find.byType(KassandraRoot),
        matchesGoldenFile('goldens/forecast_$width.png'),
      );

      await _open(tester, find.byKey(const Key('open-gap')));
      expect(find.text('Кассовый разрыв'), findsWidgets);
      await expectLater(
        find.byType(KassandraRoot),
        matchesGoldenFile('goldens/gap_$width.png'),
      );

      await tester.tap(
        find.byKey(
          width < 600 ? const Key('gap-back') : const Key('nav-forecast'),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      final addKey = width < 600
          ? const Key('add-event')
          : const Key('add-event-inline');
      await _open(tester, find.byKey(addKey));
      expect(find.text('Новое событие'), findsWidgets);
      await expectLater(
        find.byType(KassandraRoot),
        matchesGoldenFile('goldens/event_$width.png'),
      );

      if (width < 600) {
        await tester.tap(find.byTooltip('Закрыть'));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 300));
      }
      await tester.tap(find.byKey(const Key('nav-scenarios')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      await _reveal(tester, find.byKey(const Key('scenario-result')));
      expect(find.byKey(const Key('scenario-result')), findsWidgets);
      await expectLater(
        find.byType(KassandraRoot),
        matchesGoldenFile('goldens/scenarios_$width.png'),
      );
    }
  });
}

Future<void> _boot(WidgetTester tester, Size size) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    ProviderScope(key: UniqueKey(), child: const KassandraRoot()),
  );
  await tester.runAsync(() async {
    await Future<void>.delayed(const Duration(milliseconds: 400));
  });
  final balance = find.textContaining('2\u00A0099\u00A0029');
  for (var i = 0; i < 40; i++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (balance.evaluate().isNotEmpty) return;
  }
  final texts = find
      .byType(Text)
      .evaluate()
      .map((element) => (element.widget as Text).data)
      .whereType<String>()
      .take(8)
      .join(' | ');
  fail('Нет остатка. size=${tester.view.physicalSize} texts=$texts');
}

Future<void> _reveal(WidgetTester tester, Finder target) async {
  final scrollable = find.descendant(
    of: find.byKey(const Key('page-scroll')),
    matching: find.byType(Scrollable),
  );
  for (var i = 0; i < 12 && target.evaluate().isEmpty; i++) {
    if (scrollable.evaluate().isEmpty) break;
    await tester.drag(
      scrollable.first,
      const Offset(0, -420),
      warnIfMissed: false,
    );
    await tester.pump(const Duration(milliseconds: 50));
  }
}

Future<void> _open(WidgetTester tester, Finder target) async {
  final scrollable = find.descendant(
    of: find.byKey(const Key('page-scroll')),
    matching: find.byType(Scrollable),
  );
  for (var i = 0; i < 10 && target.evaluate().isEmpty; i++) {
    if (scrollable.evaluate().isEmpty) break;
    await tester.drag(
      scrollable.first,
      const Offset(0, -420),
      warnIfMissed: false,
    );
    await tester.pump(const Duration(milliseconds: 50));
  }
  expect(target, findsWidgets);
  await tester.ensureVisible(target);
  await tester.pump();
  await tester.tap(target);
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 300));
}
