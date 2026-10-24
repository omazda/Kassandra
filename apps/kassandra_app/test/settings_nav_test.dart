import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/main.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('desktop settings opens in two taps and highlights the item', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const ProviderScope(child: KassandraRoot()));
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 400));
    });
    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 100));
      if (find.textContaining('2\u00A0099\u00A0029').evaluate().isNotEmpty) {
        break;
      }
    }
    expect(find.textContaining('2\u00A0099\u00A0029'), findsWidgets);

    await tester.tap(find.byKey(const Key('nav-more')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Настройки'), findsWidgets);
    expect(find.widgetWithText(ListTile, 'Операции'), findsNothing);
    expect(find.text('Выбор меняет прогноз на всех экранах.'), findsOneWidget);

    final box = tester.widget<Container>(
      find
          .descendant(
            of: find.byKey(const Key('nav-more')),
            matching: find.byType(Container),
          )
          .first,
    );
    expect((box.decoration as BoxDecoration).color, const Color(0xFF1E4B4F));

    await tester.tap(find.text('Тёмная'));
    await tester.pump();
    expect(
      tester.widget<MaterialApp>(find.byType(MaterialApp)).themeMode,
      ThemeMode.dark,
    );
  });
}
