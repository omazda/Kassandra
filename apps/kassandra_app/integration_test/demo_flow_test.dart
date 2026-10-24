import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:kassandra/main.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('fixture path reaches 15 May and the 25 May preview', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 900);
    tester.view.devicePixelRatio = 1;
    await tester.pumpWidget(const ProviderScope(child: KassandraRoot()));
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 300));
    });
    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 100));
      if (find.textContaining('2\u00A0099\u00A0029').evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.textContaining('21 мая'), findsWidgets);
    await tester.tap(find.byKey(const Key('open-gap')));
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    await tester.scrollUntilVisible(
      find.byKey(const Key('worst-scenario')),
      300,
    );
    await tester.tap(find.byKey(const Key('worst-scenario')));
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    await tester.scrollUntilVisible(
      find.byKey(const Key('scenario-revenue')),
      300,
    );
    await tester.tap(find.byKey(const Key('scenario-revenue')));
    await tester.pump();
    await tester.scrollUntilVisible(
      find.byKey(const Key('scenario-extra')),
      300,
    );
    await tester.tap(find.byKey(const Key('scenario-extra')));
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    expect(find.textContaining('15 мая'), findsWidgets);
    await tester.tap(find.byKey(const Key('nav-forecast')));
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    await tester.tap(find.byKey(const Key('add-event')));
    await tester.pumpAndSettle(const Duration(milliseconds: 100));
    await tester.enterText(find.byKey(const Key('event-date')), '20.05.2026');
    await tester.pump();
    expect(find.textContaining('25 мая'), findsWidgets);
  });
}
