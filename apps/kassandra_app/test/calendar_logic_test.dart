import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/errors.dart';
import 'package:kassandra/api/models.dart';
import 'package:kassandra/api/v11.dart';
import 'package:kassandra/api/views.dart';
import 'package:kassandra/features/events/calendar_logic.dart';
import 'package:kassandra/features/operations/structure.dart';

void main() {
  test('moving an outflow later keeps the 18 May gap', () {
    final points = [
      _point(DateTime.utc(2026, 4, 30), 88431182),
      _point(DateTime.utc(2026, 5, 12), 100),
      _point(DateTime.utc(2026, 5, 18), -21846277),
    ];
    final preview = previewMove(
      points: points,
      oldDate: DateTime.utc(2026, 4, 30),
      newDate: DateTime.utc(2026, 5, 12),
      direction: 'out',
      amountMinor: 80091827,
    );
    expect(preview, isNotNull);
    expect(preview!.beforeMinor, 88431182);
    expect(preview.afterMinor, 88431182 + 80091827);
    expect(preview.oldGap, DateTime.utc(2026, 5, 18));
    expect(preview.newGap, DateTime.utc(2026, 5, 18));
    expect(preview.text, contains('останется'));
    expect(preview.text, contains('1\u00A0685\u00A0230'));
  });

  test('moving an outflow past the gap shifts it', () {
    final points = [
      _point(DateTime.utc(2026, 4, 30), 88431182),
      _point(DateTime.utc(2026, 5, 18), -21846277),
      _point(DateTime.utc(2026, 5, 25), -50552632),
    ];
    final preview = previewMove(
      points: points,
      oldDate: DateTime.utc(2026, 4, 30),
      newDate: DateTime.utc(2026, 5, 20),
      direction: 'out',
      amountMinor: 80091827,
    );
    expect(preview!.oldGap, DateTime.utc(2026, 5, 18));
    expect(preview.newGap, DateTime.utc(2026, 5, 25));
    expect(preview.text, contains('сдвинется'));
  });

  test('an inflow moved later lowers the balance', () {
    final delta = balanceDelta(
      day: DateTime.utc(2026, 4, 28),
      oldDate: DateTime.utc(2026, 4, 28),
      newDate: DateTime.utc(2026, 5, 2),
      direction: 'in',
      amountMinor: 13500000,
    );
    expect(delta, -13500000);
  });

  test('demo calendar filters keep ten payments and the obligatory count', () {
    final file = jsonDecode(
      File('assets/fixtures/c003_v1_1_calendar.json')
          .readAsStringSync(),
    ) as Map<String, dynamic>;
    final full = CalendarView.fromJson(
      file['demo_sources_calendar'] as Map<String, dynamic>,
    );
    expect(full.items, hasLength(10));
    expect(full.totals.inflowMinor, 83000000);
    expect(full.totals.outflowMinor, 194282027);
    expect(full.facets.obligatory, 4);
    final obligatory = applyCalendarFilters(
      full,
      const CalendarQuery(obligatory: true),
    );
    expect(obligatory.items, hasLength(4));
    expect(obligatory.items.every((item) => item.obligatory), isTrue);
    expect(obligatory.facets.obligatory, 4);
    final cancelled = full.items.first;
    final withCancelled = CalendarView(
      runId: full.runId,
      accountId: full.accountId,
      asOf: full.asOf,
      dateTo: full.dateTo,
      items: [
        CalendarItem(
          id: cancelled.id,
          date: cancelled.date,
          dueDate: cancelled.dueDate,
          direction: cancelled.direction,
          amountMinor: cancelled.amountMinor,
          label: cancelled.label,
          title: cancelled.title,
          componentId: cancelled.componentId,
          category: cancelled.category,
          categoryLabel: cancelled.categoryLabel,
          group: cancelled.group,
          source: cancelled.source,
          sourceLabel: cancelled.sourceLabel,
          sourceShort: cancelled.sourceShort,
          kind: cancelled.kind,
          status: 'cancelled',
          certainty: cancelled.certainty,
          certaintyLabel: cancelled.certaintyLabel,
          counterpartyName: cancelled.counterpartyName,
          counterpartyInn: cancelled.counterpartyInn,
          obligatory: cancelled.obligatory,
          overdue: cancelled.overdue,
          actions: const [],
        ),
        ...full.items.skip(1),
      ],
      totals: full.totals,
      facets: full.facets,
    );
    final hidden = applyCalendarFilters(withCancelled, const CalendarQuery());
    expect(hidden.items.any((item) => item.cancelled), isFalse);
    final shown = applyCalendarFilters(
      withCancelled,
      const CalendarQuery(includeCancelled: true),
    );
    expect(shown.items.first.cancelled, isTrue);
  });

  test('structure keeps five largest categories and the rest', () {
    final rows = [
      for (var i = 0; i < 8; i++)
        AmountRow(
          direction: 'out',
          label: 'Категория $i',
          amountMinor: (8 - i) * 1000,
          operations: 1,
          share: 0,
          category: 'c$i',
        ),
    ];
    final slices = buildSlices(rows: rows, direction: 'out', grouped: false);
    expect(slices, hasLength(6));
    expect(slices.last.other, isTrue);
    expect(slices.last.color, otherSliceColor);
    expect(slices.last.categories, ['c5', 'c6', 'c7']);
    expect(slices.first.color.toARGB32(), 0xFF1E6E70);
  });

  test('cancelled rows stay visible but leave the totals', () {
    final file = jsonDecode(
      File('assets/fixtures/c003_v1_1_calendar.json')
          .readAsStringSync(),
    ) as Map<String, dynamic>;
    final full = CalendarView.fromJson(
      file['demo_sources_calendar'] as Map<String, dynamic>,
    );
    final first = full.items.first;
    final withCancelled = CalendarView(
      runId: full.runId,
      accountId: full.accountId,
      asOf: full.asOf,
      dateTo: full.dateTo,
      items: [
        CalendarItem(
          id: first.id,
          date: first.date,
          dueDate: first.dueDate,
          direction: first.direction,
          amountMinor: first.amountMinor,
          label: first.label,
          title: first.title,
          componentId: first.componentId,
          category: first.category,
          categoryLabel: first.categoryLabel,
          group: first.group,
          source: first.source,
          sourceLabel: first.sourceLabel,
          sourceShort: first.sourceShort,
          kind: first.kind,
          status: 'cancelled',
          certainty: first.certainty,
          certaintyLabel: first.certaintyLabel,
          counterpartyName: first.counterpartyName,
          counterpartyInn: first.counterpartyInn,
          obligatory: first.obligatory,
          overdue: first.overdue,
          actions: const [],
        ),
        ...full.items.skip(1),
      ],
      totals: full.totals,
      facets: full.facets,
    );
    final shown = applyCalendarFilters(
      withCancelled,
      const CalendarQuery(includeCancelled: true),
    );
    expect(shown.items, hasLength(10));
    expect(shown.totals.count, 9);
    expect(activePayments(shown.items, 'in'), 2);
    expect(
      shown.totals.inflowMinor,
      full.totals.inflowMinor - first.amountMinor,
    );
  });

  test('risk badge is only critical or warning', () {
    expect(showsCounterpartyRisk('critical'), isTrue);
    expect(showsCounterpartyRisk('warning'), isTrue);
    expect(showsCounterpartyRisk('unknown'), isFalse);
    expect(showsCounterpartyRisk('high'), isFalse);
  });

  test('subtitle follows the sources that are actually present', () {
    expect(
      calendarSourcesLine(const ['По истории']),
      'платежи по истории счёта',
    );
    expect(
      calendarSourcesLine(const ['Банк', 'Счёт', 'Договор']),
      'данные: Банк, Счёт, Договор',
    );
  });

  test('deleting a displaced payment explains the unchanged gap', () {
    const changes = RefreshChanges(
      gapAppeared: false,
      gapResolved: false,
      gapShiftDays: 0,
      needDeltaMinor: 3,
      minimumDeltaMinor: -3,
    );
    expect(
      refreshNotice(
        changes,
        removedMinor: 9650000,
        categoryLabel: 'Поставщики и закупки',
        registered: true,
      ),
      contains('Поставщики и закупки'),
    );
    expect(
      refreshNotice(
        changes,
        removedMinor: 4,
        categoryLabel: 'Налоги',
        registered: true,
      ),
      isNot(contains('Платёж убран')),
    );
  });

  test('409 names the registry event to open', () {
    final error = mapHttpError(409, {
      'detail': 'Ожидание уже исправлено: измените событие override:regular:1',
    });
    expect(error, isA<ConflictException>());
    expect(error.message, contains('Ожидание уже исправлено'));
    expect((error as ConflictException).eventId, 'override:regular:1');
    expect(
      mapHttpError(409, {'detail': 'version'}).message,
      'Данные обновились, прогноз пересчитан',
    );
  });

  test('calendar rows do not repeat the counterparty, category or source', () {
    final file = jsonDecode(
      File('assets/fixtures/c003_v1_1_calendar.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final items = CalendarView.fromJson(
      file['demo_sources_calendar'] as Map<String, dynamic>,
    ).items;
    CalendarItem titled(String start) =>
        items.firstWhere((item) => item.title.startsWith(start));

    final invoice = titled('Счёт №1506');
    expect(invoice.shortTitle, 'Счёт №1506');
    expect(invoice.metaLine, 'ООО «Семена-676» · Поставщики и закупки');
    expect(
      titled('Зарплатный проект: зарплата').metaLine,
      isNot(contains('Зарплата')),
    );
    expect(
      titled('ПО и учётные системы').metaLine,
      'ООО «Учетные системы-520»',
    );
    expect(titled('Поставка зерна').metaLine, isNot(contains('редоплата')));
    expect(titled('Кредит').metaLine, contains('Обслуживание долга'));
    // Every demo payment comes from a document or history: certainty only restates it.
    expect(items.any((item) => item.showsCertainty), isFalse);
  });

  test('gap actions say the action once', () {
    GapAction action(String code, String text) =>
        GapAction(code: code, text: text);
    final ask = action(
      'speed_up_client',
      'Запросить предоплату у крупных клиентов: в календаре нет поступлений после даты разрыва.',
    );
    expect(ask.title, 'Запросить предоплату у крупных клиентов');
    expect(ask.detail, 'В календаре нет поступлений после даты разрыва.');
    final credit = action(
      'credit_line',
      'Оформить овердрафт или кредитную линию не менее 393 тыс. ₽.',
    );
    expect(credit.title, 'Оформить овердрафт или кредитную линию');
    expect(credit.detail, 'Не менее 393 тыс. ₽.');
    final reserve = action(
      'reserve',
      'Сформировать резерв не менее 393 тыс. ₽, чтобы такие периоды проходить без кредита.',
    );
    expect(reserve.title, 'Сформировать резерв');
    expect(reserve.detail, startsWith('Не менее 393 тыс. ₽'));
    final covered = action(
      'reserve',
      'Порог включает резерв 500 тыс. ₽; дефицит 393 тыс. ₽ меньше резерва.',
    );
    expect(covered.title, 'Использовать резерв');
  });
}

ForecastPoint _point(DateTime date, int closing) => ForecastPoint(
  date: date,
  eventInMinor: 0,
  eventOutMinor: 0,
  residualInMinor: 0,
  residualOutMinor: 0,
  residualRevenueMinor: 0,
  closingBalanceMinor: closing,
);
