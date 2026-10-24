import '../ai/ai_review_card.dart';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/errors.dart';
import '../../api/models.dart';
import '../../api/v11.dart';
import '../../app/config.dart';
import '../../app/providers.dart';
import '../../chart/balance_chart.dart';
import '../../chart/series.dart';
import '../../design/icons.dart';
import '../../design/logo.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import 'trace_card.dart';

enum GapBannerKind { shortNow, upcoming, watch, enough }

GapBannerKind classifyRisk(RiskResult risk) {
  if (risk.currentGapMinor > 0) return GapBannerKind.shortNow;
  if (risk.firstGapDate != null && risk.newRisk) return GapBannerKind.upcoming;
  // X11: the median stays above zero, but the calibrated probability reached the alarm threshold.
  if (risk.alarm == true && risk.probability30d != null) {
    return GapBannerKind.watch;
  }
  return GapBannerKind.enough;
}

class ForecastPage extends ConsumerStatefulWidget {
  const ForecastPage({super.key});

  @override
  ConsumerState<ForecastPage> createState() => _ForecastPageState();
}

class _ForecastPageState extends ConsumerState<ForecastPage> {
  HistoryWindow _window = HistoryWindow.days30;

  @override
  Widget build(BuildContext context) {
    final bundle = ref.watch(forecastBundleProvider);
    final phone = isPhone(context);
    return bundle.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => ErrorPane(
        error: error,
        onRetry: () => ref.invalidate(forecastBundleProvider),
      ),
      data: (data) {
        if (data.forecast.confidence.refuse) {
          return RefuseView(
            text: data.forecast.confidence.limitation ?? 'Прогноз не построен',
          );
        }
        return ListView(
          key: const Key('page-scroll'),
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 200)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            if (phone)
              _MobileHeader(data: data)
            else
              _DesktopHeader(data: data),
            const SizedBox(height: 16),
            if (phone)
              _MobileBanner(data: data)
            else
              GapBanner(forecast: data.forecast, explanation: data.explanation),
            if (data.forecast.risk.stress != null) ...[
              const SizedBox(height: 12),
              StressCard(stress: data.forecast.risk.stress!),
            ],
            if (data.forecast.overdueEvents.isNotEmpty) ...[
              const SizedBox(height: 12),
              OverdueCard(items: data.forecast.overdueEvents),
            ],
            const SizedBox(height: 16),
            if (!phone) ...[
              _KpiRow(forecast: data.forecast),
              const SizedBox(height: 16),
            ],
            // The LLM reviews the ML forecast; fixtures have no server to ask.
            if (!kUseFixtures) ...[
              AiReviewCard(forecast: data.forecast),
              const SizedBox(height: 16),
            ],
            _ChartCard(
              data: data,
              window: _window,
              compact: phone,
              onWindow: (value) => setState(() => _window = value),
            ),
            const SizedBox(height: 16),
            if (phone)
              _MobilePayments(events: data.forecast.obligatoryOut)
            else
              _DesktopColumns(data: data),
            const SizedBox(height: 12),
            TrustBlock(forecast: data.forecast),
            if (!kUseFixtures) ...[
              const SizedBox(height: 12),
              ForecastTraceCard(runId: data.forecast.runId),
            ],
            if (kUseFixtures) ...[
              const SizedBox(height: 8),
              Text(
                'Демо-данные: синтетическая выписка ${data.forecast.accountId}, модель ${data.forecast.model}, факт до ${formatDateDot(data.forecast.asOf)}.',
                style: golos(
                  13,
                  FontWeight.w400,
                  KassandraColors.of(context).textMuted,
                ),
              ),
            ],
          ],
        );
      },
    );
  }
}

class RefuseView extends StatelessWidget {
  const RefuseView({super.key, required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: SurfaceCard(
          borderColor: colors.warningOutline,
          color: colors.warningContainer,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Прогноз не построен',
                style: golos(22, FontWeight.w600, colors.text),
              ),
              const SizedBox(height: 8),
              Text(text, style: golos(15, FontWeight.w400, colors.text)),
            ],
          ),
        ),
      ),
    );
  }
}

class _DesktopHeader extends ConsumerWidget {
  const _DesktopHeader({required this.data});
  final ForecastBundle data;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final forecast = data.forecast;
    final updated = forecast.latestOperationDate.isAfter(forecast.asOf)
        ? 'последняя операция в архиве ${formatDateDot(forecast.latestOperationDate)}'
        : 'выписка до ${formatDateDot(forecast.latestOperationDate)}';
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'ИИ-прогноз остатка',
                style: unbounded(30, FontWeight.w600, colors.text),
              ),
              const SizedBox(height: 6),
              Text(
                'Расчёт на ${formatDateFull(forecast.asOf)} · следующие 30 дней · ML-модель ${forecast.model}${kUseFixtures ? '' : ' + проверка $kAiName'} · $updated',
                style: golos(15, FontWeight.w400, colors.textMuted),
              ),
            ],
          ),
        ),
        const SizedBox(width: 16),
        Wrap(
          spacing: 10,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            _DateChip(date: forecast.asOf),
            OutlinedButton(
              onPressed: () => context.go(
                '/sources${sectionQuery(ref.read(sessionProvider))}',
              ),
              child: const Text('Загрузить выписку'),
            ),
            _AlertButton(runId: forecast.runId, warned: _hasGap(forecast)),
            FilledButton(
              onPressed: () => context.go(
                '/scenarios${sectionQuery(ref.read(sessionProvider))}',
              ),
              child: const Text('Что если'),
            ),
          ],
        ),
      ],
    );
  }
}

class _DateChip extends ConsumerWidget {
  const _DateChip({required this.date});
  final DateTime date;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return Material(
      color: colors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: colors.divider),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => _pickDate(context, ref),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 44),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Center(
              child: Text(formatDateDot(date), style: mono(14, colors.text)),
            ),
          ),
        ),
      ),
    );
  }
}

Future<void> _pickDate(BuildContext context, WidgetRef ref) async {
  final accounts = await ref.read(accountsProvider.future);
  final session = ref.read(sessionProvider);
  final account = accounts.cast<Account?>().firstWhere(
    (item) => item!.id == session.accountId,
    orElse: () => accounts.isEmpty ? null : accounts.first,
  );
  if (!context.mounted || session.asOf == null) return;
  final first = account?.firstDate ?? DateTime.utc(session.asOf!.year - 2);
  final last = account?.lastDate ?? session.asOf!;
  final picked = await showDatePicker(
    context: context,
    initialDate: session.asOf!,
    firstDate: first,
    lastDate: last.isBefore(first) ? first : last,
    locale: const Locale('ru'),
  );
  if (picked == null) return;
  ref.read(sessionProvider.notifier).setAsOf(picked);
  if (!context.mounted) return;
  context.go('/forecast${sectionQuery(ref.read(sessionProvider))}');
}

class _MobileHeader extends ConsumerWidget {
  const _MobileHeader({required this.data});
  final ForecastBundle data;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final accounts = ref.watch(accountsProvider).valueOrNull;
    final account = accounts?.cast<Account?>().firstWhere(
      (item) => item!.id == data.forecast.accountId,
      orElse: () => null,
    );
    final gap =
        data.forecast.risk.firstGapDate != null ||
        data.forecast.risk.currentGapMinor > 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 52,
          child: Row(
            children: [
              const KassandraMark(size: 36),
              const SizedBox(width: 10),
              Expanded(
                child: OutlinedButton(
                  onPressed: () async {
                    final list = await ref.read(accountsProvider.future);
                    if (!context.mounted || list.isEmpty) return;
                    ref.read(sessionProvider.notifier).applyAccount(list.first);
                  },
                  child: Text(account?.shortName ?? data.forecast.accountId),
                ),
              ),
              const SizedBox(width: 8),
              _AlertButton(runId: data.forecast.runId, warned: gap),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'Остаток на ${formatDateFull(data.forecast.asOf).replaceFirst(' ${data.forecast.asOf.year}', '')}',
          style: golos(14, FontWeight.w400, colors.textMuted),
        ),
        const SizedBox(height: 4),
        Text(
          formatMoneyExact(data.forecast.openingBalanceMinor),
          style: unbounded(34, FontWeight.w600, colors.text, tabular: true),
        ),
      ],
    );
  }
}

class _MobileBanner extends ConsumerWidget {
  const _MobileBanner({required this.data});
  final ForecastBundle data;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final risk = data.forecast.risk;
    final kind = classifyRisk(risk);
    final days = risk.firstGapDate?.difference(data.forecast.asOf).inDays;
    final title = switch (kind) {
      GapBannerKind.shortNow =>
        'Уже не хватает ${data.explanation.shortfall.isEmpty ? formatMoneyShort(risk.currentGapMinor, ceil: true) : data.explanation.shortfall}',
      GapBannerKind.upcoming =>
        '${formatDateShort(risk.firstGapDate!)} не хватит ${data.explanation.shortfall.isEmpty ? formatMoneyShort(risk.liquidityNeedMinor, ceil: true) : data.explanation.shortfall}',
      GapBannerKind.watch =>
        'Минимум ${formatMoneyShort(risk.minimumBalanceMinor)} — запас тонкий',
      GapBannerKind.enough =>
        'Денег хватает до ${formatDateShort(data.forecast.horizonEnd)}',
    };
    final tone = kind == GapBannerKind.enough
        ? colors.positiveContainer
        : kind == GapBannerKind.shortNow
        ? colors.danger.withValues(alpha: 0.12)
        : colors.warningContainer;
    final border = kind == GapBannerKind.enough
        ? colors.positive
        : colors.warningOutline;
    final probability = risk.probability30d == null
        ? ''
        : ' · ${formatPercent(risk.probability30d!)}';
    final calibration = switch (risk.calibrationStatus) {
      'experimental_uncalibrated' => ' · экспериментальная оценка',
      'calibrated_on_jan_feb' ||
      'calibrated_on_selection' => ' · откалибровано',
      _ => '',
    };
    return Material(
      key: const Key('open-gap'),
      color: tone,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: BorderSide(color: border),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(22),
        onTap: () =>
            context.go('/gap${sectionQuery(ref.read(sessionProvider))}'),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                kind == GapBannerKind.enough
                    ? 'ЗАПАСА ХВАТАЕТ'
                    : kind == GapBannerKind.shortNow
                    ? 'УЖЕ НЕ ХВАТАЕТ'
                    : kind == GapBannerKind.watch
                    ? 'РИСК РАЗРЫВА ${formatPercent(risk.probability30d!)}'
                    : 'РАЗРЫВ ЧЕРЕЗ ${days == null ? '—' : daysWord(days).toUpperCase()}',
                style: golos(
                  12,
                  FontWeight.w600,
                  kind == GapBannerKind.enough
                      ? colors.positive
                      : colors.onWarningContainer,
                ).copyWith(letterSpacing: 0.8),
              ),
              const SizedBox(height: 8),
              Text(title, style: golos(20, FontWeight.w600, colors.text)),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${data.explanation.why.isEmpty ? 'Подробности' : data.explanation.why.first}$probability$calibration',
                      style: golos(14, FontWeight.w400, colors.textMuted),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Text(
                    'Что сделать',
                    style: golos(14, FontWeight.w600, colors.primary),
                  ),
                  KIcon(KIcon.chevron, size: 16, color: colors.primary),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class GapBanner extends ConsumerWidget {
  const GapBanner({
    super.key,
    required this.forecast,
    required this.explanation,
  });
  final ForecastResult forecast;
  final GapExplanation explanation;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final kind = classifyRisk(forecast.risk);
    final risk = forecast.risk;
    final background = switch (kind) {
      GapBannerKind.shortNow => colors.danger.withValues(alpha: 0.12),
      GapBannerKind.upcoming || GapBannerKind.watch => colors.warningContainer,
      GapBannerKind.enough => colors.positiveContainer,
    };
    final border = switch (kind) {
      GapBannerKind.shortNow => colors.danger,
      GapBannerKind.upcoming || GapBannerKind.watch => colors.warningOutline,
      GapBannerKind.enough => colors.positive,
    };
    final accent = kind == GapBannerKind.enough
        ? colors.positive
        : colors.onWarningContainer;
    final days = risk.firstGapDate == null
        ? 0
        : risk.firstGapDate!.difference(forecast.asOf).inDays;
    final eyebrow = switch (kind) {
      GapBannerKind.shortNow => 'Уже не хватает',
      GapBannerKind.upcoming => 'Кассовый разрыв через ${daysWord(days)}',
      GapBannerKind.watch =>
        'Риск кассового разрыва ${formatPercent(risk.probability30d!)}',
      GapBannerKind.enough => 'Денег хватает',
    };
    final title = switch (kind) {
      GapBannerKind.shortNow =>
        'На счёте уже не хватает денег на обязательные платежи',
      GapBannerKind.upcoming =>
        '${formatDateShort(risk.firstGapDate!)} на счёте не хватит денег на обязательные платежи',
      GapBannerKind.watch =>
        'Прогноз не уходит в минус, но запас тонкий: минимум ${formatMoneyExact(risk.minimumBalanceMinor)}. '
            'В похожих ситуациях денег не хватало',
      GapBannerKind.enough =>
        'Денег хватает до ${formatDateShort(forecast.horizonEnd)}, минимум ${formatMoneyExact(risk.minimumBalanceMinor)}',
    };
    final hero = kind == GapBannerKind.enough
        ? formatMoneyExact(risk.minimumBalanceMinor)
        : formatMoneyExact(risk.minimumBalanceMinor);
    final chips = explanation.why.take(3).toList();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 28),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(26),
        border: Border.all(color: border),
      ),
      child: Wrap(
        spacing: 28,
        runSpacing: 18,
        alignment: WrapAlignment.spaceBetween,
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 640),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Eyebrow(
                  eyebrow,
                  color: accent,
                  icon: kind == GapBannerKind.enough ? null : KIcon.alert,
                ),
                const SizedBox(height: 12),
                Text(
                  title,
                  style: golos(28, FontWeight.w600, colors.text, height: 1.25),
                ),
                if (kind != GapBannerKind.enough &&
                    kind != GapBannerKind.watch) ...[
                  const SizedBox(height: 12),
                  Wrap(
                    crossAxisAlignment: WrapCrossAlignment.end,
                    spacing: 14,
                    children: [
                      Text(
                        hero,
                        style: unbounded(
                          40,
                          FontWeight.w600,
                          colors.danger,
                          tabular: true,
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Text(
                          'понадобится к ${formatDateShort(forecast.horizonEnd)}, чтобы остаток не ушёл в минус',
                          style: golos(15, FontWeight.w400, colors.textMuted),
                        ),
                      ),
                    ],
                  ),
                ],
                if (chips.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 10,
                    runSpacing: 8,
                    children: [
                      for (final chip in chips)
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 8,
                          ),
                          decoration: BoxDecoration(
                            color: colors.surface,
                            borderRadius: BorderRadius.circular(KRadius.chip),
                            border: Border.all(color: border),
                          ),
                          child: Text(
                            chip,
                            style: golos(14, FontWeight.w400, colors.text),
                          ),
                        ),
                    ],
                  ),
                ],
              ],
            ),
          ),
          SizedBox(
            width: 300,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ProbabilityBlock(risk: risk),
                const SizedBox(height: 16),
                FilledButton(
                  key: const Key('open-gap'),
                  style: FilledButton.styleFrom(
                    backgroundColor: colors.ink,
                    foregroundColor: colors.onInk,
                    minimumSize: const Size.fromHeight(48),
                  ),
                  onPressed: () => context.go(
                    '/gap${sectionQuery(ref.read(sessionProvider))}',
                  ),
                  child: const Text('Посмотреть, что сделать'),
                ),
                const SizedBox(height: 10),
                OutlinedButton(
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                    side: BorderSide(color: border),
                  ),
                  onPressed: () => context.go(
                    '/scenarios${sectionQuery(ref.read(sessionProvider))}',
                  ),
                  child: const Text('Проверить худший сценарий'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class ProbabilityBlock extends StatelessWidget {
  const ProbabilityBlock({super.key, required this.risk, this.compact = false});
  final RiskResult risk;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final unavailable =
        risk.probability30d == null || risk.calibrationStatus == 'unavailable';
    if (unavailable) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Вероятность недоступна',
            style: golos(16, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 4),
          Text(
            risk.limitation,
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
        ],
      );
    }
    final caption = switch (risk.calibrationStatus) {
      'calibrated_on_jan_feb' || 'calibrated_on_selection' => 'откалибровано',
      _ => 'экспериментальная оценка',
    };
    final extra = risk.simulations > 0
        ? ' · ${groupDigits('${risk.simulations}')} траекторий'
        : '';
    return Row(
      children: [
        _Ring(
          value: risk.probability30d!,
          label: formatPercent(risk.probability30d!),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Вероятность разрыва',
                style: golos(16, FontWeight.w600, colors.text),
              ),
              const SizedBox(height: 4),
              Text(
                '$caption$extra',
                style: golos(13, FontWeight.w400, colors.textMuted),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Ring extends StatelessWidget {
  const _Ring({required this.value, required this.label});
  final double value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Semantics(
      label: 'Вероятность разрыва $label',
      child: SizedBox(
        width: 96,
        height: 96,
        child: CustomPaint(
          painter: _RingPainter(
            value: value,
            track: colors.warningOutline,
            color: colors.warning,
          ),
          child: Center(
            child: Text(
              label,
              style: unbounded(18, FontWeight.w600, colors.text, tracking: 0),
            ),
          ),
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({required this.value, required this.track, required this.color});
  final double value;
  final Color track;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 10
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect.deflate(8), 0, 6.28318, false, paint..color = track);
    canvas.drawArc(
      rect.deflate(8),
      -1.5708,
      6.28318 * value.clamp(0, 1),
      false,
      paint..color = color,
    );
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) =>
      oldDelegate.value != value;
}

class _KpiRow extends StatelessWidget {
  const _KpiRow({required this.forecast});
  final ForecastResult forecast;

  @override
  Widget build(BuildContext context) {
    final minPoint = forecast.points.reduce(
      (a, b) => a.closingBalanceMinor < b.closingBalanceMinor ? a : b,
    );
    final obligatory = forecast.obligatoryOut.fold<int>(
      0,
      (sum, event) => sum + event.remainingMinor,
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth > 860;
        final tiles = [
          _Kpi(
            'Остаток сегодня',
            formatMoneyExact(forecast.openingBalanceMinor),
            'на конец ${formatDateShort(forecast.asOf)}',
          ),
          _Kpi(
            'Минимум за 30 дней',
            formatMoneyExact(forecast.risk.minimumBalanceMinor),
            formatDateShort(minPoint.date),
            danger: forecast.risk.minimumBalanceMinor < 0,
          ),
          _Kpi(
            'Поступления',
            '+${formatMoneyShort(forecast.inflowMinor)}',
            'ожидается за 30 дней',
            positive: true,
          ),
          _Kpi(
            'Списания',
            formatMoneyShort(-forecast.outflowMinor),
            'из них обязательных ${formatMoneyExact(obligatory)}',
          ),
        ];
        if (!wide) {
          return Wrap(
            spacing: 16,
            runSpacing: 16,
            children: [
              for (final tile in tiles) SizedBox(width: 220, child: tile),
            ],
          );
        }
        return Row(
          children: [
            for (final tile in tiles) ...[
              Expanded(child: tile),
              if (tile != tiles.last) const SizedBox(width: 16),
            ],
          ],
        );
      },
    );
  }
}

class _Kpi extends StatelessWidget {
  const _Kpi(
    this.label,
    this.value,
    this.caption, {
    this.danger = false,
    this.positive = false,
  });
  final String label;
  final String value;
  final String caption;
  final bool danger;
  final bool positive;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final color = danger
        ? colors.danger
        : positive
        ? colors.positive
        : colors.text;
    return SurfaceCard(
      radius: KRadius.cardSmall,
      padding: const EdgeInsets.fromLTRB(22, 20, 22, 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: golos(14, FontWeight.w400, colors.textMuted)),
          const SizedBox(height: 8),
          Text(
            value,
            style: unbounded(
              24,
              FontWeight.w600,
              color,
              tabular: true,
              tracking: -0.01,
            ),
          ),
          const SizedBox(height: 8),
          Text(caption, style: golos(13, FontWeight.w400, colors.textMuted)),
        ],
      ),
    );
  }
}

class _ChartCard extends StatelessWidget {
  const _ChartCard({
    required this.data,
    required this.window,
    required this.compact,
    required this.onWindow,
  });

  final ForecastBundle data;
  final HistoryWindow window;
  final bool compact;
  final ValueChanged<HistoryWindow> onWindow;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final forecast = data.forecast;
    final series = buildBalanceSeries(
      asOf: forecast.asOf,
      history: forecast.history,
      daily: data.daily,
      points: forecast.points,
      openingMinor: forecast.openingBalanceMinor,
      window: window,
    );
    final minimum = forecast.points.reduce(
      (a, b) => a.closingBalanceMinor < b.closingBalanceMinor ? a : b,
    );
    final gap = forecast.risk.firstGapDate;
    final label = gap == null
        ? 'Остаток ${formatMoneyExact(forecast.openingBalanceMinor)}, минимум ${formatMoneyShort(forecast.risk.minimumBalanceMinor)} к ${formatDateShort(minimum.date)}'
        : 'Остаток падает до ${formatMoneyShort(forecast.risk.minimumBalanceMinor)} к ${formatDateShort(minimum.date)}, разрыв ${formatDateShort(gap)}';
    return SurfaceCard(
      padding: EdgeInsets.fromLTRB(
        compact ? 16 : 28,
        compact ? 16 : 24,
        compact ? 16 : 28,
        16,
      ),
      radius: compact ? 22 : KRadius.card,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  compact ? 'Остаток на 30 дней' : 'Остаток по дням',
                  style: golos(compact ? 15 : 20, FontWeight.w600, colors.text),
                ),
              ),
              if (compact)
                Text(
                  'до ${formatDateDot(forecast.horizonEnd).substring(0, 5)}',
                  style: mono(11, colors.textMuted),
                )
              else
                WindowSwitch(
                  labels: const ['30 дней истории', '90 дней', 'Год'],
                  index: window.index,
                  onChanged: (index) => onWindow(HistoryWindow.values[index]),
                ),
            ],
          ),
          const SizedBox(height: 8),
          BalanceChart(
            series: series,
            thresholdMinor: forecast.risk.thresholdMinor,
            gapDate: gap,
            minimumDate: minimum.date,
            minimumMinor: minimum.closingBalanceMinor,
            semanticsLabel: label,
            compact: compact,
            height: compact ? 140 : 340,
          ),
          if (!compact)
            Wrap(
              spacing: 20,
              runSpacing: 8,
              children: [
                _Legend(color: colors.text, text: 'Факт по выписке'),
                _Legend(color: colors.primary, text: 'Прогноз', dashed: true),
                _Legend(
                  color: colors.primary.withValues(alpha: 0.35),
                  text: '8 из 10 сценариев попадают в диапазон',
                  box: true,
                ),
                _Legend(
                  color: colors.danger,
                  text:
                      'Порог ${formatMoneyExact(forecast.risk.thresholdMinor)}',
                  dashed: true,
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend({
    required this.color,
    required this.text,
    this.dashed = false,
    this.box = false,
  });
  final Color color;
  final String text;
  final bool dashed;
  final bool box;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 22,
          height: box ? 12 : 3,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        const SizedBox(width: 8),
        Text(
          text,
          style: golos(
            13,
            FontWeight.w400,
            KassandraColors.of(context).textMuted,
          ),
        ),
      ],
    );
  }
}

class _DesktopColumns extends ConsumerWidget {
  const _DesktopColumns({required this.data});
  final ForecastBundle data;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final stacked = constraints.maxWidth < 760;
        final payments = PaymentsBlock(forecast: data.forecast);
        final actions = ActionsBlock(
          forecast: data.forecast,
          actions: data.explanation.actions,
        );
        if (stacked) {
          return Column(
            children: [payments, const SizedBox(height: 16), actions],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: payments),
            const SizedBox(width: 24),
            Expanded(child: actions),
          ],
        );
      },
    );
  }
}

class PaymentsBlock extends ConsumerWidget {
  const PaymentsBlock({super.key, required this.forecast, this.limit});
  final ForecastResult forecast;
  final int? limit;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final events = forecast.obligatoryOut;
    final shown = limit == null ? events : events.take(limit!).toList();
    final total = events.fold<int>(
      0,
      (sum, event) => sum + event.remainingMinor,
    );
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Обязательные платежи до ${formatDateShort(forecast.horizonEnd)}',
                  style: golos(20, FontWeight.w600, colors.text),
                ),
              ),
              if (events.isNotEmpty)
                Text(
                  formatMoneyExact(total),
                  style: golos(14, FontWeight.w400, colors.textMuted),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (events.isEmpty)
            EmptyHint(
              text: 'Обязательных платежей не найдено',
              action: 'Добавить событие',
              onAction: () => context.go(
                '/events/new${sectionQuery(ref.read(sessionProvider))}',
              ),
            )
          else
            for (final event in shown) _PaymentRow(event: event),
          if (events.isNotEmpty)
            TextButton(
              key: const Key('add-event-inline'),
              onPressed: () => context.go(
                '/events/new${sectionQuery(ref.read(sessionProvider))}',
              ),
              child: const Text('+ Добавить счёт, договор или налог'),
            ),
        ],
      ),
    );
  }
}

class _PaymentRow extends StatelessWidget {
  const _PaymentRow({required this.event});
  final PlannedEvent event;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final signed = event.direction == 'out'
        ? -event.remainingMinor
        : event.remainingMinor;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: colors.divider)),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 64,
            child: Text(
              formatDateShort(event.expectedDate),
              style: mono(14, colors.textMuted),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  event.title,
                  style: golos(15, FontWeight.w500, colors.text),
                ),
                const SizedBox(height: 4),
                Consumer(
                  builder: (context, ref, _) {
                    final dictionaries = ref
                        .watch(dictionariesProvider)
                        .valueOrNull;
                    return SourceChip(
                      labelForSource(dictionaries, event.source),
                      emphasis: event.source == 'tax_calendar',
                    );
                  },
                ),
              ],
            ),
          ),
          Text(
            formatMoneyExact(signed),
            style: golos(15, FontWeight.w600, colors.text),
          ),
        ],
      ),
    );
  }
}

class _MobilePayments extends ConsumerWidget {
  const _MobilePayments({required this.events});
  final List<PlannedEvent> events;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final shown = events.take(3).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Ближайшие платежи',
                style: golos(16, FontWeight.w600, colors.text),
              ),
            ),
            TextButton(
              onPressed: () => context.go(
                '/calendar${sectionQuery(ref.read(sessionProvider))}',
              ),
              child: const Text('Все'),
            ),
          ],
        ),
        if (shown.isEmpty)
          EmptyHint(
            text: 'Обязательных платежей не найдено',
            action: 'Добавить событие',
            onAction: () => context.go(
              '/events/new${sectionQuery(ref.read(sessionProvider))}',
            ),
          )
        else
          SurfaceCard(
            radius: 18,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Column(
              children: [
                for (var i = 0; i < shown.length; i++)
                  Container(
                    constraints: const BoxConstraints(minHeight: 52),
                    decoration: BoxDecoration(
                      border: i == shown.length - 1
                          ? null
                          : Border(bottom: BorderSide(color: colors.divider)),
                    ),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 56,
                          child: Text(
                            formatDateShort(shown[i].expectedDate),
                            style: mono(12, colors.textMuted),
                          ),
                        ),
                        Expanded(
                          child: Text(
                            shown[i].title,
                            style: golos(15, FontWeight.w400, colors.text),
                          ),
                        ),
                        Text(
                          formatMoneyExact(-shown[i].remainingMinor),
                          style: golos(15, FontWeight.w600, colors.text),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

class ActionsBlock extends StatelessWidget {
  const ActionsBlock({
    super.key,
    required this.forecast,
    required this.actions,
  });
  final ForecastResult forecast;
  final List<GapAction> actions;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final when = forecast.risk.firstGapDate == null
        ? 'горизонта'
        : formatDateShort(forecast.risk.firstGapDate!);
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Что можно сделать до $when',
            style: golos(20, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 12),
          if (actions.isEmpty)
            Text(
              'Сервер не предложил действий',
              style: golos(14, FontWeight.w400, colors.textMuted),
            )
          else
            for (final action in actions) ...[
              Container(
                margin: const EdgeInsets.only(bottom: 12),
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: colors.background,
                  borderRadius: BorderRadius.circular(KRadius.tile),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 40,
                      height: 40,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: _actionColor(
                          colors,
                          action.code,
                        ).withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: KIcon(
                        _actionIcon(action.code),
                        size: 20,
                        color: _actionColor(colors, action.code),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            action.title,
                            style: golos(15, FontWeight.w600, colors.text),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            action.detail,
                            style: golos(14, FontWeight.w400, colors.textMuted),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
        ],
      ),
    );
  }
}

String _actionIcon(String code) => switch (code) {
  'speed_up_client' => KIcon.arrowUp,
  'postpone_supplier' => KIcon.calendar,
  'credit_line' => KIcon.bank,
  'reserve' => KIcon.vault,
  _ => KIcon.alert,
};

Color _actionColor(KassandraColors colors, String code) => switch (code) {
  'speed_up_client' => colors.positive,
  'postpone_supplier' => colors.primary,
  'credit_line' => colors.onWarningContainer,
  _ => colors.primary,
};

class StressCard extends StatelessWidget {
  const StressCard({super.key, required this.stress});
  final StressView stress;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final when = stress.firstGapDate == null
        ? 'без даты'
        : formatDateShort(stress.firstGapDate!);
    return SurfaceCard(
      key: const Key('stress-card'),
      borderColor: colors.warningOutline,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Если не придут обещанные оплаты (${formatMoneyShort(stress.excludedMinor, ceil: true)}): разрыв $when, не хватит ${formatMoneyShort(stress.liquidityNeedMinor, ceil: true)}',
            style: golos(15, FontWeight.w600, colors.text),
          ),
          if (stress.description.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              stress.description,
              style: golos(13, FontWeight.w400, colors.textMuted),
            ),
          ],
          for (final item in stress.excluded)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                '${item.label} · ${item.reason}',
                style: golos(14, FontWeight.w400, colors.text),
              ),
            ),
        ],
      ),
    );
  }
}

class OverdueCard extends ConsumerWidget {
  const OverdueCard({super.key, required this.items});
  final List<OverdueItem> items;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Просрочено', style: golos(20, FontWeight.w600, colors.text)),
          for (final item in items)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      item.treatment == 'rolled_forward'
                          ? '${item.label}: перенесено на ${item.newDate == null ? 'новую дату' : formatDateShort(item.newDate!)}'
                          : '${item.label}: не учитывается, укажите новую дату',
                      style: golos(14, FontWeight.w400, colors.text),
                    ),
                  ),
                  if (item.treatment != 'rolled_forward')
                    TextButton(
                      onPressed: () => _moveOverdue(context, ref, item),
                      child: const Text('Перенести'),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

Future<void> _moveOverdue(
  BuildContext context,
  WidgetRef ref,
  OverdueItem item,
) async {
  final session = ref.read(sessionProvider);
  if (session.asOf == null) return;
  final picked = await showDatePicker(
    context: context,
    initialDate: session.asOf!.add(const Duration(days: 7)),
    firstDate: session.asOf!,
    lastDate: session.asOf!.add(const Duration(days: 370)),
  );
  if (picked == null) return;
  await ref
      .read(apiProvider)
      .rescheduleEvent(item.eventId, dateOnly(picked), session.asOf!);
  ref.read(sessionProvider.notifier).refresh();
}

bool _hasGap(ForecastResult forecast) =>
    forecast.risk.firstGapDate != null ||
    forecast.risk.currentGapMinor > 0 ||
    forecast.risk.alarm == true;

class _AlertButton extends ConsumerWidget {
  const _AlertButton({required this.runId, required this.warned});
  final String runId;
  final bool warned;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    return IconButton(
      key: const Key('alerts-button'),
      tooltip: warned ? 'Есть предупреждение о разрыве' : 'Уведомления',
      onPressed: () => showAlerts(context, ref, runId),
      icon: Stack(
        clipBehavior: Clip.none,
        children: [
          KIcon(KIcon.bell, color: colors.text),
          if (warned)
            Positioned(
              right: -2,
              top: -2,
              child: Container(
                width: 9,
                height: 9,
                decoration: BoxDecoration(
                  color: colors.warning,
                  shape: BoxShape.circle,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

Future<void> showAlerts(
  BuildContext context,
  WidgetRef ref,
  String runId,
) async {
  final session = ref.read(sessionProvider);
  if (session.accountId == null) return;
  List<AlertItem> alerts = const [];
  String? error;
  try {
    alerts = await ref.read(apiProvider).alerts(session.accountId!, runId);
  } on ApiException catch (caught) {
    error = caught.message;
  } catch (_) {
    error = 'Не удалось загрузить оповещения';
  }
  if (!context.mounted) return;
  final colors = KassandraColors.of(context);
  await showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (context) {
      return SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('Оповещения', style: golos(18, FontWeight.w600, colors.text)),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  error,
                  style: golos(15, FontWeight.w500, colors.danger),
                ),
              )
            else if (alerts.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text('Новых оповещений нет'),
              ),
            for (final alert in alerts)
              ListTile(
                title: Text(alert.title),
                subtitle: Text(alert.text),
                leading: Icon(
                  Icons.circle,
                  size: 12,
                  color: switch (alert.severity) {
                    'critical' => colors.danger,
                    'warning' => colors.warning,
                    _ => colors.primary,
                  },
                ),
                trailing: alert.action == null
                    ? null
                    : TextButton(
                        onPressed: () {
                          Navigator.pop(context);
                          final action = alert.action!;
                          if (action.type == 'explanation') {
                            context.go(
                              '/gap${sectionQuery(ref.read(sessionProvider))}',
                            );
                          } else if (action.type == 'stress') {
                            context.go(
                              '/forecast${sectionQuery(ref.read(sessionProvider))}',
                            );
                          } else if (action.type == 'scenario' &&
                              action.scenario != null) {
                            ref.read(pendingScenarioProvider.notifier).state =
                                action.scenario;
                            context.go(
                              '/scenarios${sectionQuery(ref.read(sessionProvider))}',
                            );
                          } else if (action.type == 'reschedule' &&
                              action.eventId != null) {
                            context.go(
                              '/calendar${sectionQuery(ref.read(sessionProvider))}',
                            );
                          }
                        },
                        child: Text(switch (alert.action!.type) {
                          'explanation' => 'Подробнее',
                          'scenario' => 'Проверить сценарий',
                          'stress' => 'Стресс',
                          'reschedule' => 'Перенести',
                          _ => 'Открыть',
                        }),
                      ),
              ),
          ],
        ),
      );
    },
  );
}

class TrustBlock extends StatelessWidget {
  const TrustBlock({super.key, required this.forecast});
  final ForecastResult forecast;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final confidence = forecast.confidence;
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: Material(
        color: colors.surface,
        borderRadius: BorderRadius.circular(KRadius.card),
        child: ExpansionTile(
          title: Text(
            'Насколько можно доверять прогнозу',
            style: golos(16, FontWeight.w600, colors.text),
          ),
          subtitle: Text(
            confidence.label ?? 'Оценка не передана',
            style: golos(13, FontWeight.w400, colors.textMuted),
          ),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          children: [
            if (forecast.netting != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Счета и договоры заменили ${formatMoneyExact(forecast.netting!.displacedMinor)} среднего потока, деньги не учтены дважды.',
                    style: golos(14, FontWeight.w400, colors.text),
                  ),
                ),
              ),
            if (confidence.limitation != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  confidence.limitation!,
                  style: golos(14, FontWeight.w400, colors.text),
                ),
              ),
            for (final warning in confidence.warnings)
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  warning,
                  style: golos(14, FontWeight.w400, colors.textMuted),
                ),
              ),
            for (final flag in forecast.qualityFlags)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    flag,
                    style: golos(14, FontWeight.w400, colors.textMuted),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
