import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/chart/chart_scale.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('zero, minimum and maximum land on the linear pixels', () {
    const size = Size(1000, 340);
    final scale = ChartScale.fit(
      valuesMinor: const [0, -200000000, 800000000],
      size: size,
      left: 56,
      right: 12,
      top: 10,
      bottom: 50,
    );
    expect(scale.y(scale.domainMax), closeTo(scale.plot.top, 0.001));
    expect(scale.y(scale.domainMin), closeTo(scale.plot.bottom, 0.001));

    final yZero =
        scale.plot.bottom -
        (0 - scale.domainMin) /
            (scale.domainMax - scale.domainMin) *
            scale.plot.height;
    expect(scale.y(0), closeTo(yZero, 0.001));

    const dataMin = -2000000.0;
    const dataMax = 8000000.0;
    expect(scale.domainMin, lessThanOrEqualTo(dataMin));
    expect(scale.domainMax, greaterThanOrEqualTo(dataMax));
    expect(scale.y(dataMin), lessThan(scale.plot.bottom));
    expect(scale.y(dataMin), greaterThan(scale.plot.top));
    expect(scale.y(dataMax), lessThan(scale.y(dataMin)));

    for (final tick in scale.ticks) {
      expect(tick, inInclusiveRange(scale.domainMin, scale.domainMax));
      final steps = tick / scale.step;
      expect(steps, closeTo(steps.roundToDouble(), 0.001));
    }
  });
}
