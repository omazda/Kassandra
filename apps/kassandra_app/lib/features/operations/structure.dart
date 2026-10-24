import 'package:flutter/material.dart';

import '../../api/views.dart';
import '../../design/tokens.dart';

const sliceColors = <Color>[
  Color(0xFF1E6E70),
  Color(0xFFE39B2D),
  Color(0xFF6CC3BD),
  Color(0xFF0F2B2E),
  Color(0xFFC9A86A),
];

const otherSliceColor = Color(0xFFA9B8B6);

class DonutSlice {
  const DonutSlice({
    required this.label,
    required this.detail,
    required this.amountMinor,
    required this.share,
    required this.categories,
    required this.color,
    required this.other,
  });

  final String label;
  final String detail;
  final int amountMinor;
  final double share;
  final List<String> categories;
  final Color color;
  final bool other;
}

List<DonutSlice> buildSlices({
  required List<AmountRow> rows,
  required String direction,
  required bool grouped,
  List<AmountRow> categoryRows = const [],
}) {
  final mine = rows.where((row) => row.direction == direction).toList()
    ..sort((a, b) => b.amountMinor.compareTo(a.amountMinor));
  if (mine.isEmpty) return const [];
  final total = mine.fold<int>(0, (sum, row) => sum + row.amountMinor);
  if (total <= 0) return const [];
  final top = mine.take(5).toList();
  final rest = mine.skip(5).toList();
  final slices = <DonutSlice>[
    for (var i = 0; i < top.length; i++)
      DonutSlice(
        label: top[i].label,
        detail: '',
        amountMinor: top[i].amountMinor,
        share: top[i].amountMinor / total,
        categories: _categoriesOf(top[i], direction, grouped, categoryRows),
        color: sliceColors[i % sliceColors.length],
        other: false,
      ),
  ];
  if (rest.isNotEmpty) {
    final amount = rest.fold<int>(0, (sum, row) => sum + row.amountMinor);
    final names = rest.map((row) => row.label).where((name) => name.isNotEmpty);
    slices.add(
      DonutSlice(
        label: 'Остальное',
        detail: names.join(', '),
        amountMinor: amount,
        share: amount / total,
        categories: [
          for (final row in rest)
            ..._categoriesOf(row, direction, grouped, categoryRows),
        ],
        color: otherSliceColor,
        other: true,
      ),
    );
  }
  return slices;
}

List<String> _categoriesOf(
  AmountRow row,
  String direction,
  bool grouped,
  List<AmountRow> categoryRows,
) {
  if (!grouped) {
    return row.category.isEmpty ? const [] : [row.category];
  }
  final codes = <String>[
    for (final item in categoryRows)
      if (item.direction == direction && item.group == row.group && item.category.isNotEmpty)
        item.category,
  ];
  return codes.toSet().toList();
}

class DonutChart extends StatelessWidget {
  const DonutChart({
    super.key,
    required this.slices,
    required this.center,
    required this.caption,
    this.size = 168,
    this.showCenter = true,
  });

  final List<DonutSlice> slices;
  final String center;
  final String caption;
  final double size;
  final bool showCenter;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: Size.square(size),
            painter: _DonutPainter(slices),
          ),
          if (showCenter)
          Padding(
            padding: const EdgeInsets.all(36),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  center,
                  textAlign: TextAlign.center,
                  style: golos(15, FontWeight.w700, const Color(0xFF0F2B2E), height: 1.15),
                ),
                const SizedBox(height: 2),
                Text(
                  caption,
                  textAlign: TextAlign.center,
                  style: golos(12, FontWeight.w400, const Color(0xFF56676A)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _DonutPainter extends CustomPainter {
  _DonutPainter(this.slices);
  final List<DonutSlice> slices;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 18.0;
    final rect = Rect.fromLTWH(
      stroke / 2,
      stroke / 2,
      size.width - stroke,
      size.height - stroke,
    );
    final radius = rect.width / 2;
    final gap = radius <= 0 ? 0.0 : (2 / radius);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.butt;
    if (slices.isEmpty) {
      paint.color = otherSliceColor;
      canvas.drawArc(rect, 0, 6.28318, false, paint);
      return;
    }
    var start = -1.5708;
    final usable = 6.28318 - gap * slices.length;
    for (final slice in slices) {
      final sweep = (usable * slice.share).clamp(0.0, 6.28318);
      paint.color = slice.color;
      canvas.drawArc(rect, start, sweep, false, paint);
      start += sweep + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _DonutPainter oldDelegate) =>
      oldDelegate.slices != slices;
}

String periodCaption(String preset) {
  switch (preset) {
    case 'month':
      return 'за месяц';
    case 'year':
      return 'за год';
    case 'custom':
      return 'за период';
    default:
      return 'за 3 месяца';
  }
}

String structureTitle(String direction) =>
    direction == 'in' ? 'Структура поступлений' : 'Структура списаний';

String otherDetail(DonutSlice slice) {
  if (!slice.other || slice.detail.isEmpty) return slice.label;
  return 'Остальное: ${slice.detail}';
}

String shownPhrase(int shown, int total) => 'Показано $shown из $total';

int compareOperations(TransactionSort sort, TransactionLike a, TransactionLike b) {
  switch (sort) {
    case TransactionSort.dateAsc:
      return a.date.compareTo(b.date);
    case TransactionSort.amountDesc:
      return b.amountMinor.compareTo(a.amountMinor);
    case TransactionSort.amountAsc:
      return a.amountMinor.compareTo(b.amountMinor);
    case TransactionSort.dateDesc:
      return b.date.compareTo(a.date);
  }
}

enum TransactionSort { dateDesc, dateAsc, amountDesc, amountAsc }

TransactionSort sortFromCode(String code) {
  switch (code) {
    case 'date_asc':
      return TransactionSort.dateAsc;
    case 'amount_desc':
      return TransactionSort.amountDesc;
    case 'amount_asc':
      return TransactionSort.amountAsc;
    default:
      return TransactionSort.dateDesc;
  }
}

class TransactionLike {
  const TransactionLike({required this.date, required this.amountMinor});
  final DateTime date;
  final int amountMinor;
}
