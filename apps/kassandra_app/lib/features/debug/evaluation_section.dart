import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/debug.dart';
import '../../api/errors.dart';
import '../../api/models.dart';
import '../../app/download.dart';
import '../../app/providers.dart';
import '../../chart/balance_chart.dart';
import '../../chart/series.dart';
import '../../design/tokens.dart';
import '../../design/widgets.dart';
import '../../format/format.dart';
import '../forecast/trace_card.dart';

/// The whole-dataset check per dataset id. Kept outside the page: it runs about a minute.
final evaluationStateProvider = StateProvider.family<EvaluationState?, String>(
  (ref, id) => null,
);

String _num(double? value, {int digits = 3}) =>
    value == null ? '—' : value.toStringAsFixed(digits).replaceAll('.', ',');

String _signed(double? value) => value == null
    ? '—'
    : '${value > 0
          ? '+'
          : value < 0
          ? minus
          : ''}${_num(value.abs())}';

String _pct(double? value) => value == null ? '—' : formatPercent(value);

/// MSE comes in kopecks squared; on screen it is rubles squared, in scientific notation.
String _mse(double? kopecks2) {
  if (kopecks2 == null) return '—';
  final rubles2 = kopecks2 / 10000;
  final text = rubles2.toStringAsExponential(2).replaceAll('.', ',');
  final parts = text.split('e');
  const superscript = {
    '-': '⁻',
    '0': '⁰',
    '1': '¹',
    '2': '²',
    '3': '³',
    '4': '⁴',
  };
  const more = {'5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'};
  final power = parts.last
      .replaceFirst('+', '')
      .split('')
      .map((c) => superscript[c] ?? more[c] ?? c)
      .join();
  return '${parts.first}·10$power ₽²';
}

/// «Прогноз и метрики»: runs the check, shows the live step log, then the metrics.
class EvaluationSection extends ConsumerStatefulWidget {
  const EvaluationSection({super.key, required this.datasetId});
  final String datasetId;

  @override
  ConsumerState<EvaluationSection> createState() => _EvaluationSectionState();
}

class _EvaluationSectionState extends ConsumerState<EvaluationSection> {
  Timer? _timer;
  String? _error;
  bool _starting = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(_refresh);
  }

  @override
  void didUpdateWidget(EvaluationSection old) {
    super.didUpdateWidget(old);
    if (old.datasetId != widget.datasetId) {
      _timer?.cancel();
      _timer = null;
      _error = null;
      Future.microtask(_refresh);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    final id = widget.datasetId;
    try {
      final state = await ref.read(apiProvider).evaluation(id);
      if (!mounted) return;
      ref.read(evaluationStateProvider(id).notifier).state = state;
      if (state.running) {
        _timer ??= Timer.periodic(
          const Duration(milliseconds: 1500),
          (_) => _refresh(),
        );
      } else {
        _timer?.cancel();
        _timer = null;
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _start() async {
    setState(() {
      _starting = true;
      _error = null;
    });
    try {
      final state = await ref
          .read(apiProvider)
          .startEvaluation(widget.datasetId);
      if (!mounted) return;
      ref.read(evaluationStateProvider(widget.datasetId).notifier).state =
          state;
      _timer?.cancel();
      _timer = Timer.periodic(
        const Duration(milliseconds: 1500),
        (_) => _refresh(),
      );
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  Future<void> _report() async {
    try {
      final text = await ref
          .read(apiProvider)
          .evaluationReport(widget.datasetId);
      final saved = saveTextFile(
        'kassandra-${widget.datasetId}-report.md',
        text,
        type: 'text/markdown',
      );
      if (!saved && mounted) {
        await showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Отчёт'),
            content: SizedBox(
              width: 720,
              child: SingleChildScrollView(child: SelectableText(text)),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Закрыть'),
              ),
            ],
          ),
        );
      }
    } on ApiException catch (error) {
      ref.read(noticeProvider.notifier).state = error.message;
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final state = ref.watch(evaluationStateProvider(widget.datasetId));
    final running = state?.running ?? false;
    final result = state?.result;
    final progress = state == null || state.total == 0
        ? null
        : state.done / state.total;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SurfaceCard(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 12,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                alignment: WrapAlignment.spaceBetween,
                children: [
                  Text(
                    'Прогноз и метрики',
                    style: golos(18, FontWeight.w600, colors.text),
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      if (result != null && !running)
                        OutlinedButton.icon(
                          key: const Key('debug-report'),
                          onPressed: _report,
                          icon: const Icon(Icons.download, size: 18),
                          label: const Text('Скачать отчёт'),
                        ),
                      FilledButton.icon(
                        key: const Key('debug-evaluate'),
                        onPressed: running || _starting ? null : _start,
                        icon: const Icon(Icons.play_arrow, size: 18),
                        label: Text(
                          running
                              ? 'Считаем…'
                              : result == null
                              ? 'Рассчитать прогноз и метрики'
                              : 'Пересчитать',
                        ),
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                'Для каждой компании — прогноз на 30 дней от последнего дня выписки и честная '
                'проверка на истории: модель повторно запускается на 4 более ранних датах '
                '(шаг 14 дней), видит только данные до даты расчёта, а прогноз сравнивается '
                'с тем, что случилось потом. Отсюда MAE, MSE, матрица ошибок по разрывам, '
                'precision, recall, F1 и надёжность вероятности.',
                style: golos(13, FontWeight.w400, colors.textMuted),
              ),
              if (running) ...[
                const SizedBox(height: 14),
                LinearProgressIndicator(value: progress),
                const SizedBox(height: 6),
                Text(
                  '${state!.stage.isEmpty ? 'Готовим расчёт' : state.stage} · '
                  '${groupDigits('${state.done}')} из ${groupDigits('${state.total}')} прогнозов · '
                  '${state.elapsed.round()} с',
                  style: golos(13, FontWeight.w500, colors.textMuted),
                ),
              ],
              if (state?.status == 'error' && state?.error != null) ...[
                const SizedBox(height: 10),
                Text(
                  state!.error!,
                  style: golos(14, FontWeight.w600, colors.danger),
                ),
              ],
              if (_error != null) ...[
                const SizedBox(height: 10),
                Text(_error!, style: golos(14, FontWeight.w600, colors.danger)),
              ],
              if (state != null && state.log.isNotEmpty) ...[
                const SizedBox(height: 14),
                _LogConsole(lines: state.log, running: running),
              ],
            ],
          ),
        ),
        if (result != null) ...[
          const SizedBox(height: 16),
          _ResultHeadline(result: result, elapsed: state!.elapsed),
          const SizedBox(height: 16),
          _BalanceCard(metrics: result.balance),
          const SizedBox(height: 16),
          _GapCard(gaps: result.gaps, trajectory: result.trajectory),
          const SizedBox(height: 16),
          _ProbabilityCard(metrics: result.probability),
        ],
      ],
    );
  }
}

class _LogConsole extends StatefulWidget {
  const _LogConsole({required this.lines, required this.running});
  final List<String> lines;
  final bool running;

  @override
  State<_LogConsole> createState() => _LogConsoleState();
}

class _LogConsoleState extends State<_LogConsole> {
  final _scroll = ScrollController();

  @override
  void didUpdateWidget(_LogConsole old) {
    super.didUpdateWidget(old);
    if (widget.lines.length != old.lines.length && widget.running) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) {
          _scroll.jumpTo(_scroll.position.maxScrollExtent);
        }
      });
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      key: const Key('debug-log'),
      decoration: BoxDecoration(
        color: colors.ink,
        borderRadius: BorderRadius.circular(KRadius.tile),
      ),
      padding: const EdgeInsets.fromLTRB(16, 10, 8, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Журнал расчёта · ${widget.lines.length} строк',
                  style: golos(13, FontWeight.w600, colors.onInk),
                ),
              ),
              IconButton(
                tooltip: 'Скопировать журнал',
                onPressed: () => Clipboard.setData(
                  ClipboardData(text: widget.lines.join('\n')),
                ),
                icon: Icon(Icons.copy, size: 16, color: colors.inkMuted),
              ),
            ],
          ),
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 300),
            child: Scrollbar(
              controller: _scroll,
              child: ListView.builder(
                controller: _scroll,
                shrinkWrap: true,
                itemCount: widget.lines.length,
                itemBuilder: (context, i) {
                  final line = widget.lines[i];
                  final step = line.startsWith('Шаг');
                  return Padding(
                    padding: EdgeInsets.only(top: step ? 6 : 1, right: 8),
                    child: SelectableText(
                      line,
                      style: mono(
                        12,
                        step ? colors.onInk : colors.inkMuted,
                        weight: step ? FontWeight.w600 : FontWeight.w400,
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({
    required this.label,
    required this.value,
    this.hint,
    this.color,
  });

  final String label;
  final String value;
  final String? hint;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Container(
      width: isPhone(context) ? 160 : 196,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.surfaceMuted,
        borderRadius: BorderRadius.circular(KRadius.tile),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: golos(12, FontWeight.w600, colors.textMuted)),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: golos(19, FontWeight.w700, color ?? colors.text),
          ),
          if (hint != null) ...[
            const SizedBox(height: 2),
            Text(
              hint!,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: golos(12, FontWeight.w400, colors.textMuted),
            ),
          ],
        ],
      ),
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({
    required this.title,
    required this.subtitle,
    required this.children,
  });

  final String title;
  final String subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: golos(18, FontWeight.w600, colors.text)),
          const SizedBox(height: 4),
          Text(subtitle, style: golos(13, FontWeight.w400, colors.textMuted)),
          const SizedBox(height: 14),
          ...children,
        ],
      ),
    );
  }
}

class _ResultHeadline extends StatelessWidget {
  const _ResultHeadline({required this.result, required this.elapsed});
  final EvaluationResult result;
  final double elapsed;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final models = result.models.entries
        .map((e) => '${e.key} ${e.value}')
        .join(', ');
    return _Card(
      title: 'Прогноз на последнюю дату',
      subtitle:
          'Модели: $models · расчёт ${elapsed.round()} с'
          '${result.computedAt == null ? '' : ' · ${formatDateDot(result.computedAt!.toLocal())}'}',
      children: [
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            _MetricTile(
              label: 'Компаний с прогнозом',
              value: groupDigits('${result.companies}'),
            ),
            _MetricTile(
              label: 'Разрыв в ближайшие 30 дней',
              value: groupDigits('${result.withGap}'),
              color: result.withGap > 0 ? colors.danger : colors.positive,
              hint: 'компаний',
            ),
            _MetricTile(
              label: 'Суммарная нехватка',
              value: formatMoneyShort(result.needMinor, ceil: true),
            ),
            if (result.closed > 0)
              _MetricTile(
                label: 'Закрытые счета',
                value: groupDigits('${result.closed}'),
                hint: 'остаток 0, операции кончились раньше',
              ),
            if (result.categorizationAccuracy != null)
              _MetricTile(
                label: 'Категоризация',
                value:
                    'accuracy ${(result.categorizationAccuracy! * 100).toStringAsFixed(1).replaceAll('.', ',')}%',
                hint:
                    'macro-F1 ${_num(result.categorizationF1)} · ${result.categorizationLabelled} размеченных',
              ),
          ],
        ),
      ],
    );
  }
}

class _BalanceCard extends StatelessWidget {
  const _BalanceCard({required this.metrics});
  final BalanceMetrics metrics;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final m = metrics;
    final skillColor = (m.skillMae ?? 0) > 0 ? colors.positive : colors.text;
    final minimumBetter = m.minimumMaeMinor < m.minimumConstantMaeMinor;
    return _Card(
      title: 'Точность прогноза остатка',
      subtitle:
          '${groupDigits('${m.windows}')} окон по 30 дней · ${groupDigits('${m.points}')} дневных точек · '
          '${m.companies} компаний. Ориентир — «остаток не изменится».',
      children: [
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            _MetricTile(
              label: 'MAE остатка',
              value: formatMoneyShort(m.maeMinor),
              hint: 'не изменится: ${formatMoneyShort(m.constantMaeMinor)}',
            ),
            _MetricTile(
              label: 'RMSE',
              value: formatMoneyShort(m.rmseMinor),
              hint: 'не изменится: ${formatMoneyShort(m.constantRmseMinor)}',
            ),
            _MetricTile(
              label: 'MSE',
              value: _mse(m.mse),
              hint: 'не изменится: ${_mse(m.constantMse)}',
            ),
            _MetricTile(
              label: 'WAPE',
              value: _pct(m.wape),
              hint: 'ошибка к сумме остатков',
            ),
            _MetricTile(
              label: 'Skill по MAE',
              value: _signed(m.skillMae),
              color: skillColor,
              hint: 'по MSE ${_signed(m.skillMse)}',
            ),
            _MetricTile(
              label: 'MAE минимума месяца',
              value: formatMoneyShort(m.minimumMaeMinor),
              color: minimumBetter ? colors.positive : null,
              hint:
                  'не изменится: ${formatMoneyShort(m.minimumConstantMaeMinor)}',
            ),
            _MetricTile(
              label: 'Модель точнее',
              value: '${m.companiesBetter} из ${m.companies}',
              hint: 'компаний по MAE',
            ),
          ],
        ),
        if (m.byHorizon.length > 1) ...[
          const SizedBox(height: 18),
          Text(
            'Ошибка по горизонту, MAE',
            style: golos(14, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 6),
          _HorizonChart(points: m.byHorizon),
        ],
      ],
    );
  }
}

class _HorizonChart extends StatelessWidget {
  const _HorizonChart({required this.points});
  final List<HorizonError> points;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    Widget legend(Color color, String text, {bool dashed = false}) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 18,
          height: 3,
          color: dashed ? color.withValues(alpha: 0.6) : color,
        ),
        const SizedBox(width: 6),
        Text(text, style: golos(12, FontWeight.w500, colors.textMuted)),
      ],
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Semantics(
          label: 'MAE модели и ориентира по дням горизонта',
          child: SizedBox(
            key: const Key('debug-horizon-chart'),
            height: 200,
            child: CustomPaint(
              size: Size.infinite,
              painter: _HorizonPainter(
                points: points,
                model: colors.primary,
                constant: colors.textMuted,
                grid: colors.divider,
                label: colors.textMuted,
              ),
            ),
          ),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 16,
          children: [
            legend(colors.primary, 'модель'),
            legend(colors.textMuted, '«остаток не изменится»', dashed: true),
          ],
        ),
      ],
    );
  }
}

class _HorizonPainter extends CustomPainter {
  _HorizonPainter({
    required this.points,
    required this.model,
    required this.constant,
    required this.grid,
    required this.label,
  });

  final List<HorizonError> points;
  final Color model;
  final Color constant;
  final Color grid;
  final Color label;

  @override
  void paint(Canvas canvas, Size size) {
    const left = 64.0, bottom = 22.0, top = 8.0, right = 8.0;
    final plot = Rect.fromLTRB(
      left,
      top,
      size.width - right,
      size.height - bottom,
    );
    final top_ = points
        .map((p) => math.max(p.maeMinor, p.constantMaeMinor))
        .reduce(math.max)
        .toDouble();
    final high = top_ <= 0 ? 1.0 : top_ * 1.1;
    double x(int i) => plot.left + i * plot.width / (points.length - 1);
    double y(num minor) => plot.bottom - minor / high * plot.height;
    final gridPaint = Paint()
      ..color = grid
      ..strokeWidth = 1;
    for (var k = 0; k <= 4; k++) {
      final value = high * k / 4;
      final yy = y(value);
      canvas.drawLine(Offset(plot.left, yy), Offset(plot.right, yy), gridPaint);
      _text(canvas, formatMoneyShort(value.round()), Offset(4, yy - 7));
    }
    for (final i in [0, 6, 13, 20, points.length - 1]) {
      if (i >= points.length) continue;
      _text(canvas, 'д. ${points[i].day}', Offset(x(i) - 12, plot.bottom + 4));
    }
    void line(int Function(HorizonError) value, Color color, double alpha) {
      final path = Path()..moveTo(x(0), y(value(points.first)));
      for (var i = 1; i < points.length; i++) {
        path.lineTo(x(i), y(value(points[i])));
      }
      canvas.drawPath(
        path,
        Paint()
          ..color = color.withValues(alpha: alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5,
      );
    }

    line((p) => p.constantMaeMinor, constant, 0.6);
    line((p) => p.maeMinor, model, 1);
  }

  void _text(Canvas canvas, String text, Offset at) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontFamily: 'JetBrains Mono',
          fontSize: 10,
          color: label,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, at);
  }

  @override
  bool shouldRepaint(_HorizonPainter old) => old.points != points;
}

class _GapCard extends StatelessWidget {
  const _GapCard({required this.gaps, required this.trajectory});
  final GapMetrics gaps;
  final GapMetrics trajectory;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final g = gaps;
    final matrix = _ConfusionMatrix(gaps: g);
    final tiles = Wrap(
      spacing: 12,
      runSpacing: 12,
      children: [
        _MetricTile(label: 'Accuracy', value: _num(g.accuracy)),
        _MetricTile(
          label: 'Precision',
          value: _num(g.precision),
          hint: 'доля верных тревог',
        ),
        _MetricTile(
          label: 'Recall',
          value: _num(g.recall),
          hint: 'доля найденных разрывов',
        ),
        _MetricTile(
          label: 'F1',
          value: _num(g.f1),
          color: colors.primary,
          hint: 'только траектория: ${_num(trajectory.f1)}',
        ),
        _MetricTile(label: 'Specificity', value: _num(g.specificity)),
        _MetricTile(
          label: 'Дата ±3 дня',
          value: _pct(g.dateWithin3Days),
          hint: g.dateMaeDays == null
              ? 'найденных разрывов'
              : 'средняя ошибка ${_num(g.dateMaeDays, digits: 1)} дн.',
        ),
        _MetricTile(
          label: 'Заблаговременность',
          value: g.leadDaysMedian == null ? '—' : daysWord(g.leadDaysMedian!),
          hint: 'медиана до разрыва',
        ),
      ],
    );
    return _Card(
      title: 'Кассовые разрывы: матрица ошибок',
      subtitle:
          'Окно — 30 дней от даты расчёта; ${groupDigits('${g.windows}')} окон, ещё '
          '${g.skippedNegativeStart} начинались с минуса и не считаются. Разрыв — остаток ниже нуля '
          'или день, который спасло пополнение владельца или кредит. Тревога — калиброванная '
          'вероятность от порога; без калибровки — прогноз ниже нуля.',
      children: [
        LayoutBuilder(
          builder: (context, constraints) => constraints.maxWidth < 760
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [matrix, const SizedBox(height: 16), tiles],
                )
              : Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    matrix,
                    const SizedBox(width: 20),
                    Expanded(child: tiles),
                  ],
                ),
        ),
        const SizedBox(height: 12),
        Text(
          'Правило «прогноз ниже нуля» на тех же окнах: TP ${trajectory.tp}, FP ${trajectory.fp}, '
          'FN ${trajectory.fn}, TN ${trajectory.tn} · precision ${_num(trajectory.precision)}, '
          'recall ${_num(trajectory.recall)}, F1 ${_num(trajectory.f1)}.',
          style: golos(13, FontWeight.w400, colors.textMuted),
        ),
      ],
    );
  }
}

class _ConfusionMatrix extends StatelessWidget {
  const _ConfusionMatrix({required this.gaps});
  final GapMetrics gaps;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final head = golos(12, FontWeight.w600, colors.textMuted);
    Widget cell(String name, int value, bool right) => Container(
      width: 132,
      height: 84,
      margin: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: right ? colors.positiveContainer : colors.warningContainer,
        borderRadius: BorderRadius.circular(KRadius.control),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            groupDigits('$value'),
            style: unbounded(
              24,
              FontWeight.w600,
              right ? colors.onPositiveContainer : colors.onWarningContainer,
            ),
          ),
          Text(name, style: golos(12, FontWeight.w600, colors.textMuted)),
        ],
      ),
    );
    Widget label(String text, {double width = 132}) => SizedBox(
      width: width,
      child: Padding(
        padding: const EdgeInsets.all(4),
        child: Text(text, textAlign: TextAlign.center, style: head),
      ),
    );
    return Column(
      key: const Key('debug-confusion'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            label('', width: 96),
            label('Разрыв был'),
            label('Разрыва не было'),
          ],
        ),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            label('Тревога', width: 96),
            cell('TP', gaps.tp, true),
            cell('FP', gaps.fp, false),
          ],
        ),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            label('Нет тревоги', width: 96),
            cell('FN', gaps.fn, false),
            cell('TN', gaps.tn, true),
          ],
        ),
      ],
    );
  }
}

class _ProbabilityCard extends StatelessWidget {
  const _ProbabilityCard({required this.metrics});
  final ProbabilityMetrics metrics;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    final m = metrics;
    final good = (m.brierSkill ?? -1) > 0;
    return _Card(
      title: 'Вероятность разрыва',
      subtitle:
          '${groupDigits('${m.windows}')} окон с вероятностью, из них откалиброванных X11 — '
          '${groupDigits('${m.calibratedWindows}')}. Brier — средний квадрат ошибки вероятности; '
          'skill сравнивает его с постоянной частотой разрывов.',
      children: [
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            _MetricTile(
              label: 'Brier',
              value: _num(m.brier),
              hint: m.baseRate == null
                  ? null
                  : 'постоянная частота: ${_num(m.baseRate! * (1 - m.baseRate!))}',
            ),
            _MetricTile(
              label: 'Brier skill',
              value: _signed(m.brierSkill),
              color: good ? colors.positive : null,
            ),
            _MetricTile(
              label: 'ROC-AUC',
              value: _num(m.rocAuc),
              hint: 'ранжирование риска',
            ),
            _MetricTile(
              label: 'Частота разрывов',
              value: _pct(m.baseRate),
              hint: 'средняя вероятность ${_pct(m.meanProbability)}',
            ),
          ],
        ),
        if (m.calibration.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text(
            'Надёжность: обещанная вероятность против фактической частоты',
            style: golos(14, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 8),
          for (final bin in m.calibration) _CalibrationRow(bin: bin),
        ],
      ],
    );
  }
}

class _CalibrationRow extends StatelessWidget {
  const _CalibrationRow({required this.bin});
  final CalibrationBin bin;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    Widget bar(double value, Color color) => ClipRRect(
      borderRadius: BorderRadius.circular(3),
      child: LinearProgressIndicator(
        minHeight: 6,
        value: value.clamp(0, 1),
        color: color,
        backgroundColor: colors.divider,
      ),
    );
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        children: [
          SizedBox(
            width: 86,
            child: Text(
              bin.range,
              style: mono(12, colors.text, weight: FontWeight.w500),
            ),
          ),
          SizedBox(
            width: 80,
            child: Text(
              '${groupDigits('${bin.windows}')} окон',
              style: golos(12, FontWeight.w400, colors.textMuted),
            ),
          ),
          Expanded(
            child: Column(
              children: [
                bar(bin.predicted, colors.primary),
                const SizedBox(height: 3),
                bar(bin.observed, colors.warning),
              ],
            ),
          ),
          SizedBox(
            width: 150,
            child: Text(
              'обещано ${_pct(bin.predicted)} · было ${_pct(bin.observed)}',
              textAlign: TextAlign.right,
              style: golos(12, FontWeight.w500, colors.text),
            ),
          ),
        ],
      ),
    );
  }
}

/// One company of the check: the current forecast, each backtest window against the fact and
/// the step-by-step trace of how the forecast was built.
Future<void> showEvaluationCompany(
  BuildContext context, {
  required String datasetId,
  required String accountId,
  required String title,
  VoidCallback? onOpen,
}) {
  return showDialog<void>(
    context: context,
    builder: (context) => Dialog(
      insetPadding: isPhone(context)
          ? const EdgeInsets.all(8)
          : const EdgeInsets.symmetric(horizontal: 40, vertical: 24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1000),
        child: _CompanyDetail(
          datasetId: datasetId,
          accountId: accountId,
          title: title,
          onOpen: onOpen,
        ),
      ),
    ),
  );
}

class _CompanyDetail extends ConsumerStatefulWidget {
  const _CompanyDetail({
    required this.datasetId,
    required this.accountId,
    required this.title,
    this.onOpen,
  });

  final String datasetId;
  final String accountId;
  final String title;
  final VoidCallback? onOpen;

  @override
  ConsumerState<_CompanyDetail> createState() => _CompanyDetailState();
}

class _CompanyDetailState extends ConsumerState<_CompanyDetail> {
  late final Future<EvaluationCompany> _future = ref
      .read(apiProvider)
      .evaluationCompany(widget.datasetId, widget.accountId);
  int _window = 0;

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 12, 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  widget.title,
                  style: golos(20, FontWeight.w600, colors.text),
                ),
              ),
              if (widget.onOpen != null)
                TextButton(
                  onPressed: () {
                    Navigator.pop(context);
                    widget.onOpen!();
                  },
                  child: const Text('Открыть прогноз'),
                ),
              IconButton(
                tooltip: 'Закрыть',
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.close),
              ),
            ],
          ),
          Flexible(
            child: FutureBuilder<EvaluationCompany>(
              future: _future,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  final error = snapshot.error;
                  return EmptyHint(
                    text: error is ApiException
                        ? error.message
                        : 'Не удалось загрузить',
                  );
                }
                final data = snapshot.data;
                if (data == null) {
                  return const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: CircularProgressIndicator()),
                  );
                }
                return SingleChildScrollView(
                  padding: const EdgeInsets.only(right: 8),
                  child: _detail(context, data),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _detail(BuildContext context, EvaluationCompany data) {
    final colors = KassandraColors.of(context);
    final current = data.current;
    final windows = data.windows;
    final selected = windows.isEmpty
        ? null
        : windows[_window.clamp(0, windows.length - 1)];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Прогноз на 30 дней от ${formatDateDot(current.asOf)} · модель ${current.model}',
          style: golos(16, FontWeight.w600, colors.text),
        ),
        const SizedBox(height: 2),
        Text(
          current.closed
              ? 'Счёт закрыт ${formatDateDot(current.asOf)}: остаток 0, дальше операций нет — прогноз для справки'
              : current.gapDate == null
              ? 'Разрыва в ближайшие 30 дней нет · минимум ${formatMoneyShort(current.minimumMinor)}'
              : 'Разрыв ${formatDayMonth(current.gapDate!)} · не хватит '
                    '${formatMoneyShort(current.needMinor, ceil: true)}'
                    '${current.probability == null ? '' : ' · вероятность ${formatPercent(current.probability!)}'}',
          style: golos(
            14,
            FontWeight.w500,
            current.closed
                ? colors.textMuted
                : current.gapDate == null
                ? colors.positive
                : colors.danger,
          ),
        ),
        const SizedBox(height: 8),
        BalanceChart(
          key: const Key('debug-company-forecast'),
          series: _currentSeries(data),
          thresholdMinor: 0,
          gapDate: current.gapDate,
          height: 260,
          semanticsLabel: 'Остаток за 60 дней и прогноз на 30 дней',
        ),
        _legend(context, fact: 'факт', forecast: 'прогноз · полоса P10–P90'),
        if (selected != null) ...[
          const SizedBox(height: 20),
          Text(
            'Проверка на истории: прогноз против факта',
            style: golos(16, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              for (var i = 0; i < windows.length; i++)
                ChoiceChip(
                  label: Text('от ${formatDateDot(windows[i].origin)}'),
                  selected: i == _window,
                  onSelected: (_) => setState(() => _window = i),
                ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            _windowCaption(selected),
            style: golos(13, FontWeight.w500, colors.textMuted),
          ),
          const SizedBox(height: 6),
          BalanceChart(
            key: ValueKey('debug-window-${selected.origin}'),
            series: _windowSeries(selected),
            thresholdMinor: 0,
            height: 240,
            semanticsLabel:
                'Прогноз от ${formatDateDot(selected.origin)} и фактический остаток',
          ),
          _legend(
            context,
            fact: 'факт без новых пополнений',
            forecast: 'прогноз на ту дату',
          ),
        ],
        if (data.trace.isNotEmpty) ...[
          const SizedBox(height: 20),
          Text(
            'Как построен прогноз — пошагово',
            style: golos(16, FontWeight.w600, colors.text),
          ),
          const SizedBox(height: 6),
          TraceList(steps: data.trace),
        ],
      ],
    );
  }

  Widget _legend(
    BuildContext context, {
    required String fact,
    required String forecast,
  }) {
    final colors = KassandraColors.of(context);
    Widget item(Color color, String text) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 16, height: 3, color: color),
        const SizedBox(width: 6),
        Text(text, style: golos(12, FontWeight.w500, colors.textMuted)),
      ],
    );
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Wrap(
        spacing: 16,
        children: [
          item(colors.text, fact),
          item(colors.primary, forecast),
          item(colors.danger, 'ноль'),
        ],
      ),
    );
  }

  String _windowCaption(EvaluationWindow w) {
    final errors = [
      for (var i = 0; i < w.forecast.length; i++)
        if (w.actual[i] != null) (w.forecast[i] - w.actual[i]!).abs(),
    ];
    final constant = [
      for (var i = 0; i < w.forecast.length; i++)
        if (w.actual[i] != null) (w.startMinor - w.actual[i]!).abs(),
    ];
    final mae = errors.isEmpty
        ? null
        : errors.reduce((a, b) => a + b) ~/ errors.length;
    final maeConstant = constant.isEmpty
        ? null
        : constant.reduce((a, b) => a + b) ~/ constant.length;
    if (w.startMinor < 0) {
      return [
        if (mae != null) 'MAE ${formatMoneyShort(mae)}',
        if (maeConstant != null)
          'не изменится ${formatMoneyShort(maeConstant)}',
        'на дату расчёта остаток уже в минусе — окно не входит в метрики разрывов',
      ].join(' · ');
    }
    final gap = w.actualGapDay == null
        ? 'разрыва не было'
        : 'разрыв на ${w.actualGapDay}-й день';
    final alarm = w.predictedGapDay == null
        ? 'тревоги не было'
        : 'тревога на ${w.predictedGapDay}-й день';
    return [
      if (mae != null) 'MAE ${formatMoneyShort(mae)}',
      if (maeConstant != null) 'не изменится ${formatMoneyShort(maeConstant)}',
      'факт: $gap',
      'модель: $alarm',
      if (w.probability != null) 'вероятность ${formatPercent(w.probability!)}',
      if (w.financingMinor > 0)
        'пополнения за окно ${formatMoneyShort(w.financingMinor)}',
    ].join(' · ');
  }

  List<SeriesPoint> _currentSeries(EvaluationCompany data) {
    final history = data.history;
    final asOf = data.current.asOf;
    return [
      for (final point in history)
        SeriesPoint(
          date: point.date,
          factMinor: point.balanceMinor,
          forecastMinor: sameDate(point.date, asOf) ? point.balanceMinor : null,
          isToday: sameDate(point.date, asOf),
        ),
      for (final point in data.forecast)
        SeriesPoint(
          date: point.date,
          forecastMinor: point.balanceMinor,
          p10Minor: point.p10Minor,
          p90Minor: point.p90Minor,
        ),
    ];
  }

  List<SeriesPoint> _windowSeries(EvaluationWindow w) => [
    SeriesPoint(
      date: w.origin,
      factMinor: w.startMinor,
      forecastMinor: w.startMinor,
      isToday: true,
    ),
    for (var i = 0; i < w.forecast.length; i++)
      SeriesPoint(
        date: DateTime(w.origin.year, w.origin.month, w.origin.day + i + 1),
        factMinor: w.actual[i],
        forecastMinor: w.forecast[i],
        p10Minor: i < w.p10.length ? w.p10[i] : null,
        p90Minor: i < w.p90.length ? w.p90[i] : null,
      ),
  ];
}
