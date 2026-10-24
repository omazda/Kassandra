import 'dart:math' as math;
import 'dart:ui';

/// Linear scale in rubles. Domain pads the data; ticks are 1-2-5 values
/// that fall inside that domain.
class ChartScale {
  const ChartScale({
    required this.domainMin,
    required this.domainMax,
    required this.ticks,
    required this.plot,
    required this.step,
  });

  final double domainMin;
  final double domainMax;
  final List<double> ticks;
  final Rect plot;
  final double step;

  double y(double rubles) {
    final span = domainMax - domainMin;
    if (span == 0) return plot.center.dy;
    final t = (rubles - domainMin) / span;
    return plot.bottom - t * plot.height;
  }

  double x(int index, int count) {
    if (count <= 1) return plot.left;
    return plot.left + index * plot.width / (count - 1);
  }

  static ChartScale fit({
    required List<int> valuesMinor,
    required Size size,
    double left = 64,
    double right = 16,
    double top = 16,
    double bottom = 36,
  }) {
    final rubles = [for (final value in valuesMinor) value / 100.0];
    var lo = rubles.reduce(math.min);
    var hi = rubles.reduce(math.max);
    if ((hi - lo).abs() < 1) {
      lo -= 1;
      hi += 1;
    }
    final pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
    final step = _chooseStep(lo, hi);
    final ticks = _ticks(lo, hi, step);
    if (ticks.isNotEmpty) {
      if (ticks.first < lo) lo = ticks.first;
      if (ticks.last > hi) hi = ticks.last;
    }
    return ChartScale(
      domainMin: lo,
      domainMax: hi,
      ticks: ticks,
      step: step,
      plot: Rect.fromLTRB(
        left,
        top,
        math.max(left + 1, size.width - right),
        math.max(top + 1, size.height - bottom),
      ),
    );
  }

  /// 1-2-5 step whose snapped ticks cover the data.
  /// A step is scored by the ticks that actually fall in the range, so a
  /// span of about 10 million rubles stays on a 2 million step (−2…8)
  /// instead of collapsing to −5 / 0 / 5.
  static double _chooseStep(double lo, double hi) {
    final span = hi - lo;
    if (span <= 0) return 1;
    final base = math.pow(10, (math.log(span) / math.ln10).floor()).toDouble();
    double? chosen;
    var best = 1000.0;
    for (final magnitude in [base / 100, base / 10, base, base * 10]) {
      for (final factor in [1.0, 2.0, 5.0]) {
        final step = factor * magnitude;
        if (step <= 0) continue;
        final count = _ticks(lo, hi, step).length;
        if (count < 4 || count > 7) continue;
        final score = count <= 6 ? (count - 5).abs().toDouble() : 3.0;
        if (score < best) {
          best = score;
          chosen = step;
        }
      }
    }
    if (chosen != null) return chosen;
    final rough = span / 5;
    final exp = math.pow(10, (math.log(rough) / math.ln10).floor()).toDouble();
    final fraction = rough / exp;
    final nice = fraction <= 1
        ? 1.0
        : fraction <= 2
        ? 2.0
        : fraction <= 5
        ? 5.0
        : 10.0;
    return nice * exp;
  }

  static List<double> _ticks(double lo, double hi, double step) {
    var start = (lo / step).floor() * step;
    if (lo < 0 && start >= -step * 0.001) start -= step;
    final ticks = <double>[];
    for (var value = start; value <= hi + step * 0.001; value += step) {
      final snapped = _snap(value, step);
      if (snapped < lo - step * 1.01 || snapped > hi + step * 0.25) continue;
      if (ticks.isEmpty || (snapped - ticks.last).abs() > step * 0.4) {
        ticks.add(snapped);
      }
      if (ticks.length > 8) break;
    }
    if (lo < 0 && ticks.every((tick) => tick >= -step * 0.001)) {
      ticks.insert(0, _snap(start < 0 ? start : -step, step));
    }
    return ticks;
  }

  /// Keeps labels whose boxes do not overlap. Higher [priority] wins a collision.
  static List<int> dropOverlappingLabels({
    required List<int> indexes,
    required double Function(int index) center,
    required double Function(int index) width,
    required int Function(int index) priority,
    double gap = 8,
  }) {
    final ordered = [...indexes]
      ..sort((a, b) => center(a).compareTo(center(b)));
    final kept = <int>[];
    for (final index in ordered) {
      var blocked = false;
      for (final previous in List<int>.of(kept)) {
        final distance = (center(index) - center(previous)).abs();
        final needed = (width(index) + width(previous)) / 2 + gap;
        if (distance >= needed) continue;
        if (priority(index) > priority(previous)) {
          kept.remove(previous);
        } else {
          blocked = true;
        }
      }
      if (!blocked) kept.add(index);
    }
    kept.sort((a, b) => center(a).compareTo(center(b)));
    return kept;
  }

  static double _snap(double value, double step) {
    final units = (value / step).round();
    final snapped = units * step;
    if (snapped.abs() < step / 1000) return 0;
    return snapped;
  }
}
