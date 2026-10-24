import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/main.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('fixture demo: gap, combined scenario, event preview', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    await tester.pumpWidget(const ProviderScope(child: KassandraRoot()));
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 300));
    });
    await _until(tester, find.textContaining('2\u00A0099\u00A0029'));
    expect(find.textContaining('21 мая'), findsWidgets);
    expect(find.textContaining('393'), findsWidgets);
    expect(find.textContaining('экспериментальная оценка'), findsWidgets);

    await tester.tap(find.byKey(const Key('open-gap')));
    await _until(tester, find.text('Кассовый разрыв'));
    expect(find.textContaining('393'), findsWidgets);
    await _scroll(tester, find.byKey(const Key('worst-scenario')));
    await tester.tap(find.byKey(const Key('worst-scenario')));
    await _until(tester, find.byKey(const Key('scenario-revenue')));
    await _scroll(tester, find.byKey(const Key('scenario-revenue')));
    await tester.tap(find.byKey(const Key('scenario-revenue')));
    await tester.pump();
    await _scroll(tester, find.byKey(const Key('scenario-extra')));
    await tester.tap(find.byKey(const Key('scenario-extra')));
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump();
    await _scroll(tester, find.textContaining('15 мая'));
    expect(find.textContaining('1,34 млн'), findsWidgets);

    await tester.tap(find.byKey(const Key('nav-forecast')));
    await _until(tester, find.byKey(const Key('add-event')));
    await tester.tap(find.byKey(const Key('add-event')));
    await _until(tester, find.byKey(const Key('event-date')));
    await tester.enterText(find.byKey(const Key('event-date')), '20.05.2026');
    await tester.pump();
    await _scroll(tester, find.textContaining('25 мая'));
    expect(find.textContaining('25 мая'), findsWidgets);
    expect(find.textContaining('92 тыс'), findsWidgets);
  });
}

Future<void> _scroll(WidgetTester tester, Finder finder) async {
  final scrollable = find
      .descendant(
        of: find.byKey(const Key('page-scroll')),
        matching: find.byType(Scrollable),
      )
      .at(0);
  for (var i = 0; i < 12 && finder.evaluate().isEmpty; i++) {
    await tester.drag(scrollable, const Offset(0, -450), warnIfMissed: false);
    await tester.pump(const Duration(milliseconds: 50));
  }
  expect(finder, findsWidgets);
}

Future<void> _until(WidgetTester tester, Finder finder) async {
  for (var i = 0; i < 40; i++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) return;
  }
  fail('Не дождались $finder');
}
