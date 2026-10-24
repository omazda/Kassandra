import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/app/providers.dart';
import 'package:kassandra/main.dart';

/// «Сменить счёт» with the stand's 150 companies: the list used to be a plain sheet
/// that showed the first seven and could not scroll.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('the account picker scrolls, searches and switches', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          accountsProvider.overrideWith((ref) async {
            final real = await ref.watch(apiProvider).accounts();
            return [
              ...real,
              for (var i = 2; i <= 150; i++)
                Account(
                  id: 'C${i.toString().padLeft(3, '0')}',
                  name: 'ООО «Компания-$i»',
                  demo: true,
                  dataVersion: 1,
                  eventVersion: 0,
                  lastDate: DateTime.utc(2026, 9, 30),
                ),
              Account.fromJson(const {
                'id': 'dbg1-C001',
                'name': 'Отладочная',
                'owner': 'debug',
                'demo': 1,
                'data_version': 1,
                'event_version': 0,
              }),
            ];
          }),
        ],
        child: const KassandraRoot(),
      ),
    );
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 400)),
    );
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }

    await tester.tap(find.text('Сменить счёт').first);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('account-search')), findsOneWidget);
    expect(find.text('150 компаний'), findsOneWidget);
    // Debug uploads stay on the debug page.
    expect(find.textContaining('Отладочная'), findsNothing);

    final last = find.byKey(const Key('account-C150'));
    expect(last, findsNothing);
    await tester.scrollUntilVisible(
      last,
      400,
      // The search field has its own Scrollable; the list is the last one.
      scrollable: find
          .descendant(
            of: find.byType(Dialog),
            matching: find.byType(Scrollable),
          )
          .last,
    );
    expect(last, findsOneWidget);

    await tester.enterText(find.byKey(const Key('account-search')), 'c042');
    await tester.pumpAndSettle();
    expect(find.text('Найдено: 1'), findsOneWidget);
    await tester.tap(find.byKey(const Key('account-C042')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    final container = ProviderScope.containerOf(
      tester.element(find.byType(KassandraRoot)),
    );
    expect(container.read(sessionProvider).accountId, 'C042');
    expect(find.byType(Dialog), findsNothing);
  });
}
