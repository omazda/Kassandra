import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/views.dart';
import 'package:kassandra/features/events/calendar_edit.dart';

/// The side editor of the payment calendar: «Сохранить» and «Отмена» must leave the panel.
void main() {
  late CalendarItem item;

  setUpAll(() {
    final file = jsonDecode(
      File('assets/fixtures/c003_v1_1_calendar.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final view = CalendarView.fromJson(
      file['demo_sources_calendar'] as Map<String, dynamic>,
    );
    item = view.items.firstWhere((i) => i.hasAction('edit'));
  });

  Future<List<String>> open(WidgetTester tester, {required bool saved}) async {
    final calls = <String>[];
    tester.view.physicalSize = const Size(1440, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          // Wider than the real 360 px panel: the test font is much wider than Golos Text.
          body: SizedBox(
            width: 720,
            child: PaymentEditor(
              item: item,
              points: const [],
              knownAt: item.date.subtract(const Duration(days: 5)),
              categories: const [],
              onSave: (changed) async {
                calls.add('save ${changed.keys.join(',')}');
                return saved;
              },
              onDelete: () async => calls.add('delete'),
              onSkip: () async => calls.add('skip'),
              onClose: () => calls.add('close'),
            ),
          ),
        ),
      ),
    );
    return calls;
  }

  testWidgets('cancel and the cross close the side panel', (tester) async {
    final calls = await open(tester, saved: true);
    await tester.tap(find.byKey(const Key('edit-cancel')));
    await tester.tap(find.byKey(const Key('edit-close')));
    expect(calls, ['close', 'close']);
  });

  testWidgets('a saved change closes the panel', (tester) async {
    final calls = await open(tester, saved: true);
    await tester.enterText(find.byKey(const Key('edit-amount')), '123456');
    await tester.tap(find.byKey(const Key('edit-save')));
    await tester.pumpAndSettle();
    expect(calls, ['save amount_minor', 'close']);
  });

  testWidgets('a failed save keeps the panel open', (tester) async {
    final calls = await open(tester, saved: false);
    await tester.enterText(find.byKey(const Key('edit-amount')), '123456');
    await tester.tap(find.byKey(const Key('edit-save')));
    await tester.pumpAndSettle();
    expect(calls, ['save amount_minor']);
  });

  testWidgets('save without changes just closes', (tester) async {
    final calls = await open(tester, saved: true);
    await tester.tap(find.byKey(const Key('edit-save')));
    await tester.pumpAndSettle();
    expect(calls, ['close']);
  });
}
