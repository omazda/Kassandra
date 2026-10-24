import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api/models.dart';
import '../design/tokens.dart';
import '../format/format.dart';
import 'chart_scale.dart';
import 'series.dart';

/// Horizontal origin of a gap caption. Near the right edge the caption sits
/// to the left of the marker so it is not clipped.
double gapLabelX({
  required double anchor,
  required double labelWidth,
  required double canvasWidth,
  double margin = 4,
  double gap = 8,
}) {
  final centered = anchor - labelWidth / 2;
  if (centered + labelWidth <= canvasWidth - margin) {
    return math.max(margin, centered);
  }
  return math.max(margin, anchor - gap - labelWidth);
}

class BalanceChart extends StatefulWidget {
  const BalanceChart({
    super.key,
    required this.series,
    required this.thresholdMinor,
    required this.semanticsLabel,
    this.gapDate,
    this.minimumDate,
    this.minimumMinor,
    this.scenarioPoints = const [],
    this.scenarioGapDate,
    this.compact = false,
    this.height = 340,
  });

  final List<SeriesPoint> series;
  final int thresholdMinor;
  final String semanticsLabel;
  final DateTime? gapDate;
  final DateTime? minimumDate;
  final int? minimumMinor;
  final List<ForecastPoint> scenarioPoints;
  final DateTime? scenarioGapDate;
  final bool compact;
  final double height;

  @override
  State<BalanceChart> createState() => _BalanceChartState();
}

class _BalanceChartState extends State<BalanceChart>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  int? _hover;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 300),
    );
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (MediaQuery.disableAnimationsOf(context)) {
      _controller.value = 1;
    } else if (!_controller.isAnimating && _controller.value == 0) {
      _controller.forward();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = KassandraColors.of(context);
    return Semantics(
      label: widget.semanticsLabel,
      child: SizedBox(
        height: widget.height,
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, _) {
            return LayoutBuilder(
              builder: (context, constraints) {
                final size = Size(constraints.maxWidth, widget.height);
                final scale = ChartScale.fit(
                  valuesMinor: [
                    ...seriesValuesMinor(widget.series, widget.thresholdMinor),
                    for (final point in widget.scenarioPoints)
                      point.closingBalanceMinor,
                  ],
                  size: size,
                  left: widget.compact ? 8 : 78,
                  right: widget.compact ? 8 : 16,
                  top: 12,
                  bottom: widget.compact ? 22 : 32,
                );
                return MouseRegion(
                  onHover: (event) => _hit(event.localPosition, scale),
                  onExit: (_) => setState(() => _hover = null),
                  child: GestureDetector(
                    onTapDown: (details) => _hit(details.localPosition, scale),
                    child: CustomPaint(
                      size: size,
                      painter: _ChartPainter(
                        series: widget.series,
                        scale: scale,
                        colors: colors,
                        text: Theme.of(context).colorScheme.onSurface,
                        muted: Theme.of(context).colorScheme.onSurfaceVariant,
                        thresholdMinor: widget.thresholdMinor,
                        gapDate: widget.gapDate,
                        minimumDate: widget.minimumDate,
                        minimumMinor: widget.minimumMinor,
                        scenarioPoints: widget.scenarioPoints,
                        scenarioGapDate: widget.scenarioGapDate,
                        progress: _controller.value,
                        hover: _hover,
                        compact: widget.compact,
                      ),
                    ),
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }

  void _hit(Offset position, ChartScale scale) {
    if (widget.series.isEmpty) return;
    var best = 0;
    var bestDistance = double.infinity;
    for (var i = 0; i < widget.series.length; i++) {
      final dx = (scale.x(i, widget.series.length) - position.dx).abs();
      if (dx < bestDistance) {
        bestDistance = dx;
        best = i;
      }
    }
    setState(() => _hover = best);
  }
}

class _ChartPainter extends CustomPainter {
  _ChartPainter({
    required this.series,
    required this.scale,
    required this.colors,
    required this.text,
    required this.muted,
    required this.thresholdMinor,
    required this.gapDate,
    required this.minimumDate,
    required this.minimumMinor,
    required this.scenarioPoints,
    required this.scenarioGapDate,
    required this.progress,
    required this.hover,
    required this.compact,
  });

  final List<SeriesPoint> series;
  final ChartScale scale;
  final KassandraColors colors;
  final Color text;
  final Color muted;
  final int thresholdMinor;
  final DateTime? gapDate;
  final DateTime? minimumDate;
  final int? minimumMinor;
  final List<ForecastPoint> scenarioPoints;
  final DateTime? scenarioGapDate;
  final double progress;
  final int? hover;
  final bool compact;

  @override
  void paint(Canvas canvas, Size size) {
    if (series.isEmpty) return;
    final plot = scale.plot;
    final todayIndex = series.indexWhere((point) => point.isToday);
    final reveal = plot.left + plot.width * progress.clamp(0, 1);
    canvas.save();
    canvas.clipRect(
      Rect.fromLTRB(0, 0, math.max(reveal, plot.left), size.height),
    );

    if (todayIndex >= 0) {
      final x = scale.x(todayIndex, series.length);
      canvas.drawRect(
        Rect.fromLTRB(x, plot.top, plot.right, plot.bottom),
        Paint()..color = colors.primary.withValues(alpha: 0.04),
      );
    }
    final thresholdY = scale.y(thresholdMinor / 100);
    if (thresholdY < plot.bottom) {
      canvas.drawRect(
        Rect.fromLTRB(
          plot.left,
          thresholdY.clamp(plot.top, plot.bottom),
          plot.right,
          plot.bottom,
        ),
        Paint()..color = colors.danger.withValues(alpha: 0.05),
      );
    }

    final grid = Paint()
      ..color = colors.divider
      ..strokeWidth = 1;
    final labelStyle = TextStyle(
      fontFamily: 'JetBrains Mono',
      fontSize: compact ? 10 : 12,
      color: muted,
    );
    for (final tick in scale.ticks) {
      final y = scale.y(tick);
      canvas.drawLine(Offset(plot.left, y), Offset(plot.right, y), grid);
      if (!compact) {
        _text(
          canvas,
          _axis(tick),
          Offset(plot.left - 8, y - 6),
          labelStyle,
          align: TextAlign.right,
        );
      }
    }

    _band(canvas);
    _dashedLine(
      canvas,
      Offset(plot.left, thresholdY),
      Offset(plot.right, thresholdY),
      colors.danger,
      dash: 2,
      gap: 5,
      width: 1.5,
    );
    if (todayIndex >= 0) {
      final x = scale.x(todayIndex, series.length);
      canvas.drawLine(
        Offset(x, plot.top),
        Offset(x, plot.bottom),
        Paint()
          ..color = text.withValues(alpha: 0.4)
          ..strokeWidth = 1,
      );
    }

    _polyline(
      canvas,
      [
        for (var i = 0; i < series.length; i++)
          if (series[i].factMinor != null) i,
      ],
      (index) => series[index].factMinor!,
      text,
      2.5,
      dashed: false,
    );
    final forecastIndexes = [
      for (var i = 0; i < series.length; i++)
        if (series[i].forecastMinor != null) i,
    ];
    _polyline(
      canvas,
      forecastIndexes,
      (index) => series[index].forecastMinor!,
      colors.primary,
      2.5,
      dashed: true,
    );

    if (scenarioPoints.isNotEmpty) {
      final byDate = {
        for (final point in scenarioPoints)
          isoDate(point.date): point.closingBalanceMinor,
      };
      final indexes = <int>[];
      final values = <int>[];
      for (var i = 0; i < series.length; i++) {
        if (series[i].isToday && series[i].forecastMinor != null) {
          indexes.add(i);
          values.add(series[i].forecastMinor!);
        } else {
          final value = byDate[isoDate(series[i].date)];
          if (value != null) {
            indexes.add(i);
            values.add(value);
          }
        }
      }
      _polyline(
        canvas,
        indexes,
        (index) => values[indexes.indexOf(index)],
        colors.danger,
        3,
        dashed: false,
      );
    }

    if (todayIndex >= 0 && series[todayIndex].factMinor != null) {
      final center = Offset(
        scale.x(todayIndex, series.length),
        scale.y(series[todayIndex].factMinor! / 100),
      );
      canvas.drawCircle(center, 5, Paint()..color = text);
    }

    if (scenarioPoints.isEmpty && minimumDate != null && minimumMinor != null) {
      final index = series.indexWhere(
        (point) => sameDate(point.date, minimumDate!),
      );
      if (index >= 0) {
        final center = Offset(
          scale.x(index, series.length),
          scale.y(minimumMinor! / 100),
        );
        canvas.drawCircle(center, 4.5, Paint()..color = colors.danger);
        _text(
          canvas,
          formatMoneyShort(minimumMinor!),
          Offset(center.dx - 4, center.dy + 10),
          TextStyle(
            fontFamily: 'Golos Text',
            fontSize: 13,
            fontWeight: FontWeight.w600,
            color: colors.danger,
          ),
          align: TextAlign.right,
        );
      }
    }

    _marker(
      canvas,
      gapDate,
      scenarioPoints.isEmpty ? colors.warning : colors.primary,
      filled: scenarioPoints.isEmpty,
      canvasWidth: size.width,
    );
    if (scenarioGapDate != null) {
      _marker(
        canvas,
        scenarioGapDate,
        colors.warning,
        filled: true,
        canvasWidth: size.width,
      );
    }
    canvas.restore();
    _xLabels(canvas, size, labelStyle);
    _hover(canvas);
  }

  void _band(Canvas canvas) {
    final path = Path();
    var started = false;
    for (var i = 0; i < series.length; i++) {
      final low = series[i].p10Minor;
      final high = series[i].p90Minor;
      if (low == null || high == null) continue;
      final point = Offset(scale.x(i, series.length), scale.y(high / 100));
      if (!started) {
        path.moveTo(point.dx, point.dy);
        started = true;
      } else {
        path.lineTo(point.dx, point.dy);
      }
    }
    if (!started) return;
    for (var i = series.length - 1; i >= 0; i--) {
      final low = series[i].p10Minor;
      if (low == null || series[i].p90Minor == null) continue;
      path.lineTo(scale.x(i, series.length), scale.y(low / 100));
    }
    path.close();
    canvas.drawPath(
      path,
      Paint()..color = colors.primary.withValues(alpha: 0.13),
    );
  }

  void _polyline(
    Canvas canvas,
    List<int> indexes,
    int Function(int index) value,
    Color color,
    double width, {
    required bool dashed,
  }) {
    if (indexes.length < 2) return;
    final path = Path();
    for (var n = 0; n < indexes.length; n++) {
      final index = indexes[n];
      final offset = Offset(
        scale.x(index, series.length),
        scale.y(value(index) / 100),
      );
      if (n == 0) {
        path.moveTo(offset.dx, offset.dy);
      } else {
        path.lineTo(offset.dx, offset.dy);
      }
    }
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = width
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    if (!dashed) {
      canvas.drawPath(path, paint);
      return;
    }
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = math.min(distance + 8, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), paint);
        distance = next + 6;
      }
    }
  }

  void _marker(
    Canvas canvas,
    DateTime? date,
    Color color, {
    required bool filled,
    required double canvasWidth,
  }) {
    if (date == null) return;
    final index = series.indexWhere((point) => sameDate(point.date, date));
    if (index < 0) return;
    final center = Offset(
      scale.x(index, series.length),
      scale.y(thresholdMinor / 100),
    );
    if (filled) {
      canvas.drawCircle(
        center,
        compact ? 6 : 8,
        Paint()..color = colors.warning,
      );
      canvas.drawCircle(
        center,
        compact ? 6 : 8,
        Paint()
          ..color = colors.surface
          ..style = PaintingStyle.stroke
          ..strokeWidth = compact ? 2 : 3,
      );
    } else {
      canvas.drawCircle(center, 7, Paint()..color = colors.surface);
      canvas.drawCircle(
        center,
        7,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3,
      );
    }
    if (!compact) {
      const style = TextStyle(
        fontFamily: 'Golos Text',
        fontSize: 13,
        fontWeight: FontWeight.w600,
      );
      final label = '${formatDateShort(date)} · разрыв';
      final painter = TextPainter(
        text: TextSpan(
          text: label,
          style: style.copyWith(color: text),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final dx = gapLabelX(
        anchor: center.dx,
        labelWidth: painter.width,
        canvasWidth: canvasWidth,
      );
      painter.paint(canvas, Offset(dx, center.dy - 28));
    }
  }

  void _xLabels(Canvas canvas, Size size, TextStyle style) {
    if (series.isEmpty) return;
    final picked = <int>{0, series.length - 1};
    final today = series.indexWhere((point) => point.isToday);
    if (today >= 0) picked.add(today);
    final gap = gapDate == null
        ? -1
        : series.indexWhere((point) => sameDate(point.date, gapDate!));
    if (gap >= 0) picked.add(gap);
    final indexes = ChartScale.dropOverlappingLabels(
      indexes: picked.toList(),
      center: (index) => scale.x(index, series.length),
      width: (index) {
        final point = series[index];
        final label = point.isToday && compact
            ? 'сегодня'
            : formatDateShort(point.date);
        return label.length * (compact ? 7.2 : 8.4);
      },
      priority: (index) {
        final point = series[index];
        if (point.isToday) return 3;
        if (gapDate != null && sameDate(point.date, gapDate!)) return 2;
        if (index == series.length - 1 || index == 0) return 1;
        return 0;
      },
    );
    for (final index in indexes) {
      final point = series[index];
      final label = point.isToday && !compact
          ? '${formatDateShort(point.date)} · сегодня'
          : point.isToday && compact
          ? 'сегодня'
          : formatDateShort(point.date);
      final align = index == 0
          ? TextAlign.left
          : index == series.length - 1
          ? TextAlign.right
          : TextAlign.center;
      final emphasized =
          point.isToday || (gapDate != null && sameDate(point.date, gapDate!));
      _text(
        canvas,
        label,
        Offset(scale.x(index, series.length), scale.plot.bottom + 8),
        style.copyWith(
          color: emphasized ? text : muted,
          fontWeight: emphasized ? FontWeight.w600 : FontWeight.w400,
        ),
        align: align,
      );
    }
  }

  void _hover(Canvas canvas) {
    final index = hover;
    if (index == null || index < 0 || index >= series.length) return;
    final point = series[index];
    final x = scale.x(index, series.length);
    canvas.drawLine(
      Offset(x, scale.plot.top),
      Offset(x, scale.plot.bottom),
      Paint()
        ..color = text.withValues(alpha: 0.45)
        ..strokeWidth = 1,
    );
    final lines = <String>[formatDateFull(point.date)];
    if (point.factMinor != null && point.forecastMinor == null) {
      lines.add('Факт ${formatMoneyExact(point.factMinor!)}');
    } else if (point.forecastMinor != null) {
      lines.add('Прогноз ${formatMoneyExact(point.forecastMinor!)}');
    }
    if (point.p10Minor != null && point.p90Minor != null) {
      lines.add(
        '${formatMoneyShort(point.p10Minor!)} … ${formatMoneyShort(point.p90Minor!)}',
      );
    }
    final style = TextStyle(
      fontFamily: 'Golos Text',
      fontSize: 12,
      color: colors.onInk,
    );
    final painter = TextPainter(
      text: TextSpan(text: lines.join('\n'), style: style),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 220);
    final dx = (x + 12 + painter.width > scale.plot.right)
        ? x - painter.width - 16
        : x + 12;
    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(
        dx,
        scale.plot.top + 8,
        painter.width + 16,
        painter.height + 12,
      ),
      const Radius.circular(10),
    );
    canvas.drawRRect(rect, Paint()..color = colors.ink);
    painter.paint(canvas, Offset(dx + 8, scale.plot.top + 14));
  }

  String _axis(double rubles) {
    if (rubles.abs() < 0.5) return '0';
    return formatMoneyShort((rubles * 100).round())
        .replaceAll('$nbsp₽', '')
        .replaceAll(' ₽', '');
  }

  void _text(
    Canvas canvas,
    String value,
    Offset offset,
    TextStyle style, {
    TextAlign align = TextAlign.left,
  }) {
    final painter = TextPainter(
      text: TextSpan(text: value, style: style),
      textDirection: TextDirection.ltr,
      textAlign: align,
    )..layout();
    final dx = switch (align) {
      TextAlign.right => offset.dx - painter.width,
      TextAlign.center => offset.dx - painter.width / 2,
      _ => offset.dx,
    };
    painter.paint(canvas, Offset(dx, offset.dy));
  }

  void _dashedLine(
    Canvas canvas,
    Offset from,
    Offset to,
    Color color, {
    required double dash,
    required double gap,
    required double width,
  }) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = width;
    final total = (to - from).distance;
    final direction = (to - from) / total;
    var distance = 0.0;
    while (distance < total) {
      final start = from + direction * distance;
      final end = from + direction * math.min(distance + dash, total);
      canvas.drawLine(start, end, paint);
      distance += dash + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _ChartPainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.hover != hover ||
      oldDelegate.series != series;
}
