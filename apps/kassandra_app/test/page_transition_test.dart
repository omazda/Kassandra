import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/features/events/calendar_page.dart';
import 'package:kassandra/main.dart';

/// Tabs fade into each other: half-way through the switch the new page is partly
/// transparent, and it is fully opaque once the 260 ms transition is over.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  double opacityOf(WidgetTester tester, Finder page) {
    var value = 1.0;
    for (final fade in tester.widgetList<FadeTransition>(
      find.ancestor(of: page, matching: find.byType(FadeTransition)),
    )) {
      value *= fade.opacity.value;
    }
    return value;
  }

  testWidgets('switching tabs fades the new page in', (tester) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const ProviderScope(child: KassandraRoot()));
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 400)),
    );
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }

    await tester.tap(find.byKey(const Key('nav-calendar')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 130));
    final page = find.byType(CalendarPage);
    expect(page, findsOneWidget);
    final midway = opacityOf(tester, page);
    expect(midway, greaterThan(0));
    expect(midway, lessThan(1));

    await tester.pump(const Duration(milliseconds: 200));
    expect(opacityOf(tester, page), 1);
  });
}
