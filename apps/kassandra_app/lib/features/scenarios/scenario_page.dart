import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/errors.dart';
import '../../api/models.dart';
import '../../api/v11.dart';
import '../../app/providers.dart';
import '../../chart/balance_chart.dart';
import '../../chart/series.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'scenario_logic.dart';

class ScenarioPage extends ConsumerStatefulWidget {
  const ScenarioPage({super.key});

  @override
  ConsumerState<ScenarioPage> createState() => _ScenarioPageState();
}

class _ScenarioPageState extends ConsumerState<ScenarioPage> {
  final _draft = ScenarioDraft();
  Timer? _timer;
  ScenarioResult? _result;
  GapExplanation? _explanation;
  String? _error;
  var _loading = false;
  var _pendingApplied = false;

  void _consumePending(ForecastBundle data) {
    if (_pendingApplied) return;
    final pending = ref.read(pendingScenarioProvider);
    if (pending == null) return;
    _pendingApplied = true;
    final kind = pending['kind']?.toString() ?? '';
    if (kind == 'client_delay' || kind == 'delay') {
      _draft.clientDelay = true;
      final days = pending['delay_days'];
      if (days is num && days > 0) _draft.delayDays = days.round();
      final who = pending['counterparty']?.toString();
      if (who != null && who.isNotEmpty) {
        TopClient? match;
        for (final client in data.clients) {
          if (client.inn == who || client.name == who || client.label == who) {
            match = client;
            break;
          }
        }
        _draft.counterparty = match?.label ?? who;
      }
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(pendingScenarioProvider.notifier).state = null;
      _schedule();
    });
  }

  void _applyRisk(CounterpartyRisk risk, List<TopClient> clients) {
    _draft.clientDelay = true;
    _draft.delayDays = risk.delayDays;
    TopClient? match;
    for (final client in clients) {
      if (client.inn == risk.inn || client.name == risk.name) {
        match = client;
        break;
      }
    }
    _draft.counterparty =
        match?.label ?? (risk.name.isEmpty ? risk.inn : risk.name);
    _schedule();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _schedule() {
    _timer?.cancel();
    _timer = Timer(const Duration(milliseconds: 400), _evaluate);
    setState(() {});
  }

  Future<void> _evaluate() async {
    final bundle = ref.read(forecastBundleProvider).valueOrNull;
    if (bundle == null) return;
    final asOf = bundle.forecast.asOf;
    _draft.expenseDate ??= asOf.add(const Duration(days: 12));
    _draft.fixedStart ??= asOf.add(const Duration(days: 1));
    if (_draft.counterparty == null && bundle.clients.isNotEmpty) {
      _draft.counterparty = bundle.clients.first.label;
    }
    final error = _draft.validate();
    if (error != null) {
      setState(() {
        _error = _draft.enabledCount == 0 ? null : error;
        _result = null;
      });
      return;
    }
    final spec = _draft.build(bundle.forecast.runId);
    if (spec == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = ref.read(apiProvider);
      final key = ref
          .read(idempotencyProvider)
          .get('scenario:${spec.toJson()}');
      final result = await api.evaluateScenario(spec, idempotencyKey: key);
      final explanation = await api.scenarioExplanation(result.scenarioId);
      if (!mounted) return;
      setState(() {
        _result = result;
        _explanation = explanation;
        _loading = false;
      });
    } on ConflictException {
      ref.read(noticeProvider.notifier).state =
          'Данные обновились, прогноз пересчитан';
      ref.read(idempotencyProvider).rotate('scenario:${spec.toJson()}');
      ref.invalidate(forecastBundleProvider);
      if (mounted) setState(() => _loading = false);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = error is ApiException
            ? error.message
            : 'Не удалось посчитать сценарий';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final bundle = ref.watch(forecastBundleProvider);
    final phone = isPhone(context);
    final colors = KassandraColors.of(context);
    return bundle.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => ErrorPane(
        error: error,
        onRetry: () => ref.invalidate(forecastBundleProvider),
      ),
      data: (data) {
        _consumePending(data);
        return ListView(
          key: const Key('page-scroll'),
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 120)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            Text(
              phone ? 'Что если' : 'Что если',
              style: phone
                  ? golos(17, FontWeight.w600, colors.text)
                  : unbounded(30, FontWeight.w600, colors.text),
            ),
            const SizedBox(height: 6),
            Text(
              'Включите неблагоприятные события — Kassandra покажет, как изменится остаток и когда наступит разрыв. Базовый прогноз и история не меняются.',
              style: golos(15, FontWeight.w400, colors.textMuted),
            ),
            const SizedBox(height: 20),
            if (phone)
              _Controls(
                draft: _draft,
                clients: data.clients,
                onChanged: _schedule,
                onApplyRisk: (risk) => _applyRisk(risk, data.clients),
              )
            else
              LayoutBuilder(
                builder: (context, constraints) {
                  final controls = _Controls(
                    draft: _draft,
                    clients: data.clients,
                    onChanged: _schedule,
                    onApplyRisk: (risk) => _applyRisk(risk, data.clients),
                  );
                  final result = _Result(
                    base: data,
                    result: _result,
                    explanation: _explanation,
                    loading: _loading,
                    error: _error,
                    note: _draft.expenseNote,
                    onSave: _result == null
                        ? null
                        : () {
                            ref
                                .read(savedScenariosProvider.notifier)
                                .add(_result!.scenarioId);
                            ref.read(noticeProvider.notifier).state =
                                'Сценарий сохранён на этом устройстве';
                          },
                    onCalendar: () => context.go(
                      '/calendar${sectionQuery(ref.read(sessionProvider))}',
                    ),
                    onReset: () {
                      setState(() {
                        _draft.clientDelay = false;
                        _draft.revenueDrop = false;
                        _draft.extraExpense = false;
                        _draft.fixedIncrease = false;
                        _result = null;
                        _error = null;
                      });
                    },
                  );
                  if (constraints.maxWidth < 760) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [controls, const SizedBox(height: 16), result],
                    );
                  }
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(width: 380, child: controls),
                      const SizedBox(width: 24),
                      Expanded(child: result),
                    ],
                  );
                },
              ),
            if (phone) ...[
              const SizedBox(height: 16),
              _Result(
                base: data,
                result: _result,
                explanation: _explanation,
                loading: _loading,
                error: _error,
                note: _draft.expenseNote,
                onSave: _result == null
                    ? null
                    : () {
                        ref
                            .read(savedScenariosProvider.notifier)
                            .add(_result!.scenarioId);
                        ref.read(noticeProvider.notifier).state =
                            'Сценарий сохранён на этом устройстве';
                      },
                onCalendar: () => context.go(
                  '/calendar${sectionQuery(ref.read(sessionProvider))}',
                ),
                onReset: () {
                  setState(() {
                    _draft.clientDelay = false;
                    _draft.revenueDrop = false;
                    _draft.extraExpense = false;
                    _draft.fixedIncrease = false;
                    _result = null;
                    _error = null;
                  });
                },
              ),
            ],
          ],
        );
      },
    );
  }
}

class _Controls extends StatelessWidget {
  const _Controls({
    required this.draft,
    required this.clients,
    required this.onChanged,
    required this.onApplyRisk,
  });
  final ScenarioDraft draft;
  final List<TopClient> clients;
  final VoidCallback onChanged;
  final ValueChanged<CounterpartyRisk> onApplyRisk;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Column(
      children: [
        _ToggleCard(
          title: 'Крупный клиент заплатит позже',
          subtitle: 'Задержка оплаты на 2–4 недели. Клиент выбирается из крупнейших за 90 дней.',
          value: draft.clientDelay,
          onChanged: (value) {
            draft.clientDelay = value;
            onChanged();
          },
          child: draft.clientDelay
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    DropdownButton<String>(
                      isExpanded: true,
                      value:
                          draft.counterparty == null ||
                              draft.counterparty!.isEmpty
                          ? null
                          : draft.counterparty,
                      hint: const Text('Клиент'),
                      items: [
                        for (final client in clients)
                          DropdownMenuItem(
                            value: client.label,
                            child: Text(client.label),
                          ),
                        if (draft.counterparty != null &&
                            draft.counterparty!.isNotEmpty &&
                            clients.every(
                              (client) => client.label != draft.counterparty,
                            ))
                          DropdownMenuItem(
                            value: draft.counterparty,
                            child: Text(draft.counterparty!),
                          ),
                      ],
                      onChanged: (value) {
                        draft.counterparty = value;
                        onChanged();
                      },
                    ),
                    const SizedBox(height: 8),
                    _ClientRiskHint(onApply: onApplyRisk),
                    const SizedBox(height: 8),
                    WindowSwitch(
                      labels: const ['14 дней', '21 день', '28 дней'],
                      index:
                          const [14, 21, 28].indexOf(14).clamp(0, 2) == 0 &&
                              draft.delayDays == 14
                          ? 0
                          : draft.delayDays == 21
                          ? 1
                          : 2,
                      onChanged: (index) {
                        draft.delayDays = const [14, 21, 28][index];
                        onChanged();
                      },
                    ),
                  ],
                )
              : null,
        ),
        _ToggleCard(
          title: 'Выручка снизится',
          active: draft.revenueDrop,
          value: draft.revenueDrop,
          switchKey: const Key('scenario-revenue'),
          onChanged: (value) {
            draft.revenueDrop = value;
            onChanged();
          },
          child: draft.revenueDrop
              ? Column(
                  children: [
                    Row(
                      children: [
                        Text(
                          'Насколько',
                          style: golos(14, FontWeight.w400, colors.textMuted),
                        ),
                        const Spacer(),
                        Text(
                          signedPercent(-draft.revenueDropPct),
                          style: mono(14, colors.text, weight: FontWeight.w500),
                        ),
                      ],
                    ),
                    Slider(
                      min: 10,
                      max: 30,
                      divisions: 4,
                      value: draft.revenueDropPct.toDouble(),
                      label: '${draft.revenueDropPct}%',
                      onChanged: (value) {
                        draft.revenueDropPct = value.round();
                        onChanged();
                      },
                    ),
                  ],
                )
              : null,
        ),
        _ToggleCard(
          title: 'Внеплановый расход',
          active: draft.extraExpense,
          value: draft.extraExpense,
          switchKey: const Key('scenario-extra'),
          onChanged: (value) {
            draft.extraExpense = value;
            onChanged();
          },
          child: draft.extraExpense
              ? Column(
                  children: [
                    TextFormField(
                      initialValue: formatMoneyExact(draft.extraAmountMinor),
                      decoration: const InputDecoration(labelText: 'Сумма'),
                      onChanged: (value) {
                        final minor = parseRublesToMinor(value);
                        if (minor != null && minor > 0) {
                          draft.extraAmountMinor = minor;
                        }
                        onChanged();
                      },
                    ),
                    const SizedBox(height: 8),
                    TextFormField(
                      initialValue: draft.expenseDate == null
                          ? ''
                          : formatDateShort(draft.expenseDate!),
                      decoration: const InputDecoration(
                        labelText: 'Дата',
                        hintText: '08.05.2026',
                      ),
                      onChanged: (value) {
                        final parsed =
                            parseLooseDate(value) ??
                            _withYear(value, draft.expenseDate?.year ?? 2026);
                        if (parsed != null) draft.expenseDate = parsed;
                        onChanged();
                      },
                    ),
                    const SizedBox(height: 8),
                    TextFormField(
                      initialValue: draft.expenseNote,
                      decoration: const InputDecoration(
                        labelText: 'Что это',
                        hintText: 'Не передаётся в API',
                      ),
                      onChanged: (value) => draft.expenseNote = value,
                    ),
                  ],
                )
              : null,
        ),
        _ToggleCard(
          title: 'Вырастут постоянные расходы',
          subtitle: 'Аренда, связь и похожие статьи — на заданный процент с выбранной даты.',
          value: draft.fixedIncrease,
          onChanged: (value) {
            draft.fixedIncrease = value;
            onChanged();
          },
          child: draft.fixedIncrease
              ? Slider(
                  min: 5,
                  max: 30,
                  divisions: 5,
                  value: draft.fixedCostPct.clamp(5, 30).toDouble(),
                  label: '${draft.fixedCostPct}%',
                  onChanged: (value) {
                    draft.fixedCostPct = value.round();
                    onChanged();
                  },
                )
              : null,
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton(
            onPressed: onChanged,
            child: const Text('Пересчитать'),
          ),
        ),
      ],
    );
  }

  DateTime? _withYear(String raw, int year) {
    final match = RegExp(r'^(\d{1,2})\s+([а-яё]+)$')
        .firstMatch(raw.trim().toLowerCase());
    if (match == null) return null;
    return parseLooseDate('${match.group(1)} ${match.group(2)} $year');
  }
}

class _ClientRiskHint extends ConsumerWidget {
  const _ClientRiskHint({required this.onApply});
  final ValueChanged<CounterpartyRisk> onApply;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);
    final accountId = session.accountId;
    final asOf = session.asOf;
    if (accountId == null || asOf == null) return const SizedBox.shrink();
    final risks = ref.watch(counterpartyRisksProvider);
    return risks.maybeWhen(
      data: (list) {
        final flagged = [
          for (final risk in list)
            if (risk.risk == 'critical' || risk.risk == 'warning') risk,
        ];
        if (flagged.isEmpty) return const SizedBox.shrink();
        final colors = KassandraColors.of(context);
        return Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final risk in flagged.take(3))
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${risk.name.isEmpty ? 'Контрагент' : risk.name}: ${risk.statusLabel}',
                          style: golos(13, FontWeight.w500, colors.danger),
                        ),
                      ),
                      TextButton(
                        onPressed: () => onApply(risk),
                        child: const Text('Проверить'),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        );
      },
      orElse: () => const SizedBox.shrink(),
    );
  }
}

class _ToggleCard extends StatelessWidget {
  const _ToggleCard({
    required this.title,
    required this.value,
    required this.onChanged,
    this.subtitle,
    this.child,
    this.active = false,
    this.switchKey,
  });

  final String title;
  final String? subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;
  final Widget? child;
  final bool active;
  final Key? switchKey;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(KRadius.cardSmall),
        border: Border.all(
          color: value || active ? colors.primary : colors.divider,
          width: value ? 2 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: golos(16, FontWeight.w600, colors.text),
                ),
              ),
              Switch(key: switchKey, value: value, onChanged: onChanged),
            ],
          ),
          if (subtitle != null)
            Text(
              subtitle!,
              style: golos(14, FontWeight.w400, colors.textMuted),
            ),
          if (child != null) ...[const SizedBox(height: 12), child!],
        ],
      ),
    );
  }
}

class _Result extends StatelessWidget {
  const _Result({
    required this.base,
    required this.result,
    required this.explanation,
    required this.loading,
    required this.error,
    required this.note,
    required this.onSave,
    required this.onCalendar,
    required this.onReset,
  });

  final ForecastBundle base;
  final ScenarioResult? result;
  final GapExplanation? explanation;
  final bool loading;
  final String? error;
  final String note;
  final VoidCallback? onSave;
  final VoidCallback onCalendar;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final series = buildBalanceSeries(
      asOf: base.forecast.asOf,
      history: base.forecast.history,
      daily: base.daily,
      points: base.forecast.points,
      openingMinor: base.forecast.openingBalanceMinor,
      window: HistoryWindow.days30,
    );
    if (result == null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SurfaceCard(
            color: colors.ink,
            child: Text(
              error ?? 'Включите неблагоприятное событие слева — базовый прогноз ниже не изменится, пока вы ничего не выбрали.',
              key: const Key('scenario-result'),
              style: golos(16, FontWeight.w500, colors.onInk),
            ),
          ),
          const SizedBox(height: 16),
          SurfaceCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Базовый прогноз',
                  style: golos(18, FontWeight.w600, colors.text),
                ),
                const SizedBox(height: 8),
                BalanceChart(
                  series: series,
                  thresholdMinor: base.forecast.risk.thresholdMinor,
                  gapDate: base.forecast.risk.firstGapDate,
                  semanticsLabel: 'Базовый прогноз. Включите событие, чтобы сравнить сценарий.',
                  height: 280,
                ),
              ],
            ),
          ),
        ],
      );
    }
    final shift = gapShiftText(
      result!.baseFirstGapDate,
      result!.risk.firstGapDate,
    );
    final when = result!.risk.firstGapDate == null
        ? 'без разрыва'
        : formatDateShort(result!.risk.firstGapDate!);
    final need = formatMoneyShort(result!.risk.liquidityNeedMinor);
    final baseNeed = formatMoneyShort(base.forecast.risk.liquidityNeedMinor);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          key: const Key('scenario-result'),
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: colors.ink,
            borderRadius: BorderRadius.circular(KRadius.card),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                result!.risk.firstGapDate == null
                    ? shift
                    : 'При таком сценарии разрыв наступит $when — ${shift.toLowerCase()}',
                style: golos(22, FontWeight.w600, colors.onInk, height: 1.3),
              ),
              const SizedBox(height: 16),
              Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  _Stat(
                    'Начало разрыва',
                    when,
                    'было ${base.forecast.risk.firstGapDate == null ? '—' : formatDateShort(base.forecast.risk.firstGapDate!)}',
                  ),
                  _Stat(
                    'Не хватит к ${formatDateShort(base.forecast.horizonEnd)}',
                    need,
                    'было $baseNeed',
                    emphasize: true,
                  ),
                  _Stat(
                    'Разница на ${formatDateShort(base.forecast.horizonEnd)}',
                    formatMoneyShort(result!.deltaEndMinor),
                    'по сравнению с базовым',
                  ),
                ],
              ),
              if (loading)
                const Padding(
                  padding: EdgeInsets.only(top: 12),
                  child: LinearProgressIndicator(),
                ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        SurfaceCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Базовый прогноз и сценарий',
                style: golos(18, FontWeight.w600, colors.text),
              ),
              const SizedBox(height: 8),
              BalanceChart(
                series: series,
                thresholdMinor: base.forecast.risk.thresholdMinor,
                gapDate: base.forecast.risk.firstGapDate,
                scenarioPoints: result!.points,
                scenarioGapDate: result!.risk.firstGapDate,
                semanticsLabel:
                    'Базовый прогноз и сценарий. $shift, не хватит $need',
                height: 280,
              ),
            ],
          ),
        ),
        if (result!.assumptions.isNotEmpty) ...[
          const SizedBox(height: 8),
          for (final line in result!.assumptions)
            Text(line, style: golos(12, FontWeight.w400, colors.textMuted)),
        ],
        if (explanation != null && explanation!.text.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(
            explanation!.text,
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ],
        if (note.isNotEmpty)
          Text(
            'В интерфейсе: $note',
            style: golos(12, FontWeight.w400, colors.textMuted),
          ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 10,
          runSpacing: 8,
          children: [
            FilledButton(
              onPressed: onSave,
              child: const Text('Сохранить сценарий'),
            ),
            OutlinedButton(
              onPressed: onCalendar,
              child: const Text('Показать платежи в календаре'),
            ),
            TextButton(onPressed: onReset, child: const Text('Сбросить')),
          ],
        ),
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value, this.caption, {this.emphasize = false});
  final String label;
  final String value;
  final String caption;
  final bool emphasize;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      width: 200,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.inkSurface,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: golos(13, FontWeight.w400, colors.inkMuted)),
          const SizedBox(height: 6),
          Text(
            value,
            style: unbounded(
              22,
              FontWeight.w600,
              emphasize ? colors.danger : colors.onInk,
              tabular: true,
              tracking: 0,
            ),
          ),
          const SizedBox(height: 4),
          Text(caption, style: golos(13, FontWeight.w400, colors.inkMuted)),
        ],
      ),
    );
  }
}
