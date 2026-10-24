import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/models.dart';
import '../../app/providers.dart';
import '../../chart/chart_scale.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';

final planFactProvider = FutureProvider<PlanFact>((ref) async {
  final bundle = await ref.watch(forecastBundleProvider.future);
  return ref.watch(apiProvider).planFact(bundle.forecast.runId);
});

/// Weekly plan-fact for the overdue block; cached instead of re-requested on every build.
final planFactWeekProvider = FutureProvider<Map<String, dynamic>>((ref) async {
  final bundle = await ref.watch(forecastBundleProvider.future);
  return ref.watch(apiProvider).planFactWeek(bundle.forecast.runId);
});

class PlanFactPage extends ConsumerWidget {
  const PlanFactPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final page = ref.watch(planFactProvider);
    final phone = isPhone(context);
    final colors = KassandraColors.of(context);
    return page.when(
      skipLoadingOnReload: true,
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, _) => ErrorPane(
        error: error,
        onRetry: () => ref.invalidate(planFactProvider),
      ),
      data: (data) {
        return ListView(
          padding: phone
              ? const EdgeInsets.fromLTRB(20, 14, 20, 120)
              : const EdgeInsets.fromLTRB(40, 32, 40, 48),
          children: [
            Text(
              'План и факт',
              style: phone
                  ? golos(22, FontWeight.w600, colors.text)
                  : unbounded(30, FontWeight.w600, colors.text),
            ),
            const SizedBox(height: 8),
            Text(
              'Сравнение прогнозного остатка с фактом выписки. Серым отмечены дни без данных.',
              style: golos(15, FontWeight.w400, colors.textMuted),
            ),
            const SizedBox(height: 16),
            const _WeekOverdue(),
            const SizedBox(height: 12),
            if (data.rows.isEmpty)
              const EmptyHint(text: 'Нет строк плана и факта')
            else ...[
              SizedBox(height: 260, child: _PlanChart(rows: data.rows)),
              const SizedBox(height: 16),
              // Column names once in the header instead of «План …» / «Факт …» on every row.
              _FactHeader(phone: phone),
              for (final row in data.rows)
                Container(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  decoration: BoxDecoration(
                    border: Border(bottom: BorderSide(color: colors.divider)),
                  ),
                  child: _FactLines(row: row, phone: phone),
                ),
            ],
            if (data.matchingReview.isNotEmpty) ...[
              const SizedBox(height: 16),
              Text(
                'На проверке сопоставления: ${data.matchingReview.length}',
                style: golos(14, FontWeight.w500, colors.textMuted),
              ),
            ],
          ],
        );
      },
    );
  }
}

String _signedShort(int minor) {
  if (minor > 0) return '+${formatMoneyShort(minor)}';
  return formatMoneyShort(minor);
}

Color _deviationColor(KassandraColors colors, int delta) {
  if (delta.abs() < 100000) return colors.textMuted;
  return delta < 0 ? colors.danger : colors.positive;
}

class _FactHeader extends StatelessWidget {
  const _FactHeader({required this.phone});
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final style = golos(
      12,
      FontWeight.w600,
      KassandraColors.of(context).textMuted,
    );
    return Container(
      padding: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(color: KassandraColors.of(context).divider),
        ),
      ),
      child: _FactRow(
        phone: phone,
        date: Text('Дата', style: style),
        plan: Text('План', style: style, textAlign: TextAlign.right),
        fact: Text('Факт', style: style, textAlign: TextAlign.right),
        deviation: Text(
          phone ? 'Разница' : 'Факт − план',
          style: style,
          textAlign: TextAlign.right,
        ),
      ),
    );
  }
}

class _FactLines extends StatelessWidget {
  const _FactLines({required this.row, required this.phone});
  final PlanFactRow row;
  final bool phone;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return _FactRow(
      phone: phone,
      date: Text(formatDateShort(row.date), style: mono(13, colors.textMuted)),
      plan: Text(
        formatMoneyShort(row.plannedMinor),
        textAlign: TextAlign.right,
        style: golos(14, FontWeight.w400, colors.text),
      ),
      fact: Text(
        row.actualMinor == null
            ? 'нет данных'
            : formatMoneyShort(row.actualMinor!),
        textAlign: TextAlign.right,
        style: golos(
          14,
          FontWeight.w600,
          row.actualMinor == null ? colors.textMuted : colors.text,
        ),
      ),
      deviation: Text(
        row.deltaMinor == null ? '' : _signedShort(row.deltaMinor!),
        textAlign: TextAlign.right,
        style: golos(
          14,
          FontWeight.w600,
          row.deltaMinor == null
              ? colors.textMuted
              : _deviationColor(colors, row.deltaMinor!),
        ),
      ),
    );
  }
}

class _FactRow extends StatelessWidget {
  const _FactRow({
    required this.phone,
    required this.date,
    required this.plan,
    required this.fact,
    required this.deviation,
  });

  final bool phone;
  final Widget date;
  final Widget plan;
  final Widget fact;
  final Widget deviation;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(width: phone ? 64 : 96, child: date),
        Expanded(child: plan),
        Expanded(child: fact),
        Expanded(child: deviation),
      ],
    );
  }
}

class _WeekOverdue extends ConsumerWidget {
  const _WeekOverdue();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = KassandraColors.of(context);
    final overdue = ref.watch(planFactWeekProvider).valueOrNull?['overdue'];
    return Builder(
      builder: (context) {
        if (overdue is! List || overdue.isEmpty) return const SizedBox.shrink();
        return SurfaceCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Не поступили вовремя',
                style: golos(18, FontWeight.w600, colors.text),
              ),
              for (final item in overdue.take(6))
                if (item is Map && item['warning'] != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      item['warning'].toString(),
                      style: golos(14, FontWeight.w400, colors.danger),
                    ),
                  ),
            ],
          ),
        );
      },
    );
  }
}

class _PlanChart extends StatelessWidget {
  const _PlanChart({required this.rows});
  final List<PlanFactRow> rows;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _LegendDot(color: colors.primary, label: 'план'),
              const SizedBox(width: 16),
              _LegendDot(color: colors.danger, label: 'факт'),
            ],
          ),
          const SizedBox(height: 8),
          Expanded(
            child: CustomPaint(
              painter: _PlanPainter(
                rows: rows,
                planned: colors.primary,
                actual: colors.danger,
                muted: colors.textMuted,
                grid: colors.divider,
              ),
              child: const SizedBox.expand(),
            ),
          ),
        ],
      ),
    );
  }
}

class _LegendDot extends StatelessWidget {
  const _LegendDot({required this.color, required this.label});
  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 14,
          height: 3,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        const SizedBox(width: 6),
        Text(label, style: golos(13, FontWeight.w500, colors.text)),
      ],
    );
  }
}

class _PlanPainter extends CustomPainter {
  _PlanPainter({
    required this.rows,
    required this.planned,
    required this.actual,
    required this.muted,
    required this.grid,
  });

  final List<PlanFactRow> rows;
  final Color planned;
  final Color actual;
  final Color muted;
  final Color grid;

  @override
  void paint(Canvas canvas, Size size) {
    if (rows.isEmpty) return;
    final values = <int>[
      for (final row in rows) ...[
        row.plannedMinor,
        if (row.actualMinor != null) row.actualMinor!,
      ],
    ];
    final scale = ChartScale.fit(
      valuesMinor: values,
      size: size,
      left: 58,
      right: 8,
      top: 8,
      bottom: 22,
    );
    final plot = scale.plot;
    double y(int minor) => scale.y(minor / 100);
    double x(int index) =>
        plot.left +
        (rows.length == 1 ? 0 : index * plot.width / (rows.length - 1));
    final axisStyle = TextStyle(
      fontFamily: 'JetBrains Mono',
      fontSize: 11,
      color: muted,
    );
    for (final tick in scale.ticks) {
      final dy = scale.y(tick);
      canvas.drawLine(
        Offset(plot.left, dy),
        Offset(plot.right, dy),
        Paint()..color = grid,
      );
      final caption = TextPainter(
        text: TextSpan(
          text: formatMoneyShort((tick * 100).round())
              .replaceAll(' ₽', '')
              .replaceAll('\u00A0₽', ''),
          style: axisStyle,
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      caption.paint(canvas, Offset(plot.left - caption.width - 6, dy - 6));
    }
    final first = TextPainter(
      text: TextSpan(text: formatDateShort(rows.first.date), style: axisStyle),
      textDirection: TextDirection.ltr,
    )..layout();
    final last = TextPainter(
      text: TextSpan(text: formatDateShort(rows.last.date), style: axisStyle),
      textDirection: TextDirection.ltr,
    )..layout();
    first.paint(canvas, Offset(plot.left, plot.bottom + 4));
    last.paint(canvas, Offset(plot.right - last.width, plot.bottom + 4));
    final plannedPath = Path();
    final actualPath = Path();
    var actualStarted = false;
    for (var i = 0; i < rows.length; i++) {
      final px = Offset(x(i), y(rows[i].plannedMinor));
      if (i == 0) {
        plannedPath.moveTo(px.dx, px.dy);
      } else {
        plannedPath.lineTo(px.dx, px.dy);
      }
      final fact = rows[i].actualMinor;
      if (fact == null) {
        actualStarted = false;
        continue;
      }
      final ax = Offset(x(i), y(fact));
      if (!actualStarted) {
        actualPath.moveTo(ax.dx, ax.dy);
        actualStarted = true;
      } else {
        actualPath.lineTo(ax.dx, ax.dy);
      }
      final delta = rows[i].deltaMinor;
      if (delta != null && delta.abs() >= 100000) {
        canvas.drawCircle(ax, 3.5, Paint()..color = actual);
      }
    }
    canvas.drawPath(
      plannedPath,
      Paint()
        ..color = planned
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5,
    );
    canvas.drawPath(
      actualPath,
      Paint()
        ..color = actual
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5,
    );
  }

  @override
  bool shouldRepaint(covariant _PlanPainter oldDelegate) =>
      oldDelegate.rows != rows;
}
