import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/features/scenarios/scenario_logic.dart';

void main() {
  test('one switch keeps its own kind', () {
    final draft = ScenarioDraft()
      ..revenueDrop = true
      ..revenueDropPct = 20;
    final spec = draft.build('run-1');
    expect(spec, isNotNull);
    expect(spec!.kind, 'revenue_drop');
    expect(spec.parts, isEmpty);
    expect(spec.toJson()['revenue_drop_pct'], 20);
    expect(spec.toJson().containsKey('parts'), isFalse);
  });

  test('two switches become a combined scenario', () {
    final draft = ScenarioDraft()
      ..revenueDrop = true
      ..revenueDropPct = 20
      ..extraExpense = true
      ..extraAmountMinor = 30000000
      ..expenseDate = DateTime.utc(2026, 5, 8);
    final spec = draft.build('run-1');
    expect(spec!.kind, 'combined');
    expect(spec.parts.map((part) => part.kind), [
      'revenue_drop',
      'extra_expense',
    ]);
    final json = spec.toJson();
    expect(json['parts'], hasLength(2));
    expect(draft.validate(), isNull);
  });

  test('client delay without a counterparty does not build', () {
    final draft = ScenarioDraft()..clientDelay = true;
    expect(draft.build('run'), isNull);
    expect(draft.validate(), 'Выберите клиента');
  });

  test('gap shift wording', () {
    expect(
      gapShiftText(DateTime.utc(2026, 5, 21), DateTime.utc(2026, 5, 15)),
      'Разрыв на 6 дней раньше',
    );
    expect(
      gapShiftText(DateTime.utc(2026, 5, 15), DateTime.utc(2026, 5, 21)),
      'Разрыв на 6 дней позже',
    );
  });
}
