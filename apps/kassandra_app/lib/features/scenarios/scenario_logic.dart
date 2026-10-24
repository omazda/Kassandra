import '../../api/models.dart';
import '../../format/format.dart';

class ScenarioDraft {
  bool clientDelay = false;
  bool revenueDrop = false;
  bool extraExpense = false;
  bool fixedIncrease = false;
  int delayDays = 14;
  int revenueDropPct = 20;
  int extraAmountMinor = 30000000;
  DateTime? expenseDate;
  String expenseNote = '';
  String? counterparty;
  int fixedCostPct = 10;
  DateTime? fixedStart;

  int get enabledCount =>
      (clientDelay ? 1 : 0) +
      (revenueDrop ? 1 : 0) +
      (extraExpense ? 1 : 0) +
      (fixedIncrease ? 1 : 0);

  String? validate() {
    if (enabledCount == 0) return 'Включите хотя бы одно событие';
    if (clientDelay && (counterparty == null || counterparty!.trim().isEmpty)) {
      return 'Выберите клиента';
    }
    if (extraExpense && extraAmountMinor <= 0) return 'Укажите сумму расхода';
    if (extraExpense && expenseDate == null) return 'Укажите дату расхода';
    if (fixedIncrease && fixedStart == null) {
      return 'Укажите дату роста расходов';
    }
    return null;
  }

  /// One switch → its kind. Two or more → `combined` with parts.
  ScenarioSpec? build(String baseRunId) {
    if (validate() != null) return null;
    final parts = <ScenarioPart>[];
    if (clientDelay) {
      parts.add(
        ScenarioPart(
          kind: 'client_delay',
          counterparty: counterparty!.trim(),
          delayDays: delayDays,
        ),
      );
    }
    if (revenueDrop) {
      parts.add(
        ScenarioPart(kind: 'revenue_drop', revenueDropPct: revenueDropPct),
      );
    }
    if (extraExpense) {
      parts.add(
        ScenarioPart(
          kind: 'extra_expense',
          amountMinor: extraAmountMinor,
          expenseDate: expenseDate,
        ),
      );
    }
    if (fixedIncrease) {
      parts.add(
        ScenarioPart(
          kind: 'fixed_cost_increase',
          fixedCostPct: fixedCostPct,
          startDate: fixedStart,
        ),
      );
    }
    if (parts.length == 1) {
      return ScenarioSpec(
        baseRunId: baseRunId,
        kind: parts.first.kind,
        part: parts.first,
      );
    }
    return ScenarioSpec(baseRunId: baseRunId, kind: 'combined', parts: parts);
  }
}

String gapShiftText(DateTime? base, DateTime? next) {
  if (base == null && next == null) return 'Разрыва нет';
  if (base == null && next != null) return 'Появился разрыв';
  if (base != null && next == null) return 'Разрыв ушёл';
  final days = next!.difference(base!).inDays;
  if (days == 0) return 'Дата разрыва не изменилась';
  if (days < 0) return 'Разрыв на ${daysWord(days.abs())} раньше';
  return 'Разрыв на ${daysWord(days)} позже';
}
