import '../../api/models.dart';

class BalancePreview {
  const BalancePreview({
    required this.gapDate,
    required this.liquidityNeedMinor,
    required this.minimumMinor,
    required this.insideHorizon,
  });

  final DateTime? gapDate;
  final int liquidityNeedMinor;
  final int minimumMinor;
  final bool insideHorizon;
}

/// Linear preview: closing[d] shifts by [signedMinor] for every d ≥ expected.
/// Inflow is positive, outflow is negative. Threshold comparison matches the engine (`<`).
BalancePreview previewEvent({
  required List<ForecastPoint> points,
  required int openingMinor,
  required int thresholdMinor,
  required DateTime expectedDate,
  required int signedMinor,
}) {
  if (points.isEmpty) {
    return const BalancePreview(
      gapDate: null,
      liquidityNeedMinor: 0,
      minimumMinor: 0,
      insideHorizon: false,
    );
  }
  final inside = !expectedDate.isAfter(points.last.date);
  DateTime? gap;
  var minimum = openingMinor;
  for (final point in points) {
    var closing = point.closingBalanceMinor;
    if (!point.date.isBefore(expectedDate)) closing += signedMinor;
    if (closing < minimum) minimum = closing;
    if (gap == null && closing < thresholdMinor) gap = point.date;
  }
  final floor = minimum < openingMinor ? minimum : openingMinor;
  final need = thresholdMinor - floor;
  return BalancePreview(
    gapDate: gap,
    liquidityNeedMinor: need > 0 ? need : 0,
    minimumMinor: minimum,
    insideHorizon: inside,
  );
}

RiskResult deriveRisk({
  required int openingMinor,
  required List<ForecastPoint> points,
  int reserveMinor = 0,
  int creditLimitMinor = 0,
  String limitation =
      'Локальный пересчёт фикстуры. На живом API цифры приходят с сервера.',
}) {
  final threshold = reserveMinor - creditLimitMinor;
  DateTime? first;
  var minimum = points.isEmpty
      ? openingMinor
      : points.first.closingBalanceMinor;
  for (final point in points) {
    if (point.closingBalanceMinor < minimum) {
      minimum = point.closingBalanceMinor;
    }
    if (first == null && point.closingBalanceMinor < threshold) {
      first = point.date;
    }
  }
  final floor = minimum < openingMinor ? minimum : openingMinor;
  final rawNeed = threshold - floor;
  return RiskResult(
    thresholdMinor: threshold,
    currentGapMinor: openingMinor < threshold ? threshold - openingMinor : 0,
    newRisk: openingMinor >= threshold && first != null,
    firstGapDate: first,
    minimumBalanceMinor: minimum,
    liquidityNeedMinor: rawNeed > 0 ? rawNeed : 0,
    probability30d: null,
    calibrationStatus: 'unavailable',
    probabilitySource: 'Нет пригодного банка ошибок',
    trajectoryCount: 0,
    simulations: 0,
    limitation: limitation,
  );
}
