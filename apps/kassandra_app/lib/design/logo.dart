import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import 'tokens.dart';

/// Wordmark: the mark's center sits on the vertical center of the capital K.
class KassandraLogo extends StatelessWidget {
  const KassandraLogo({
    super.key,
    this.fontSize = 22,
    this.onDark = true,
    this.color,
  });

  final double fontSize;
  final bool onDark;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final metrics = LogoMetrics.of(fontSize);
    final word =
        color ??
        (onDark
            ? KassandraColors.of(context).onInk
            : KassandraColors.of(context).text);
    final style = unbounded(fontSize, FontWeight.w600, word);
    final painter = TextPainter(
      text: TextSpan(text: 'Kassandra', style: style),
      textDirection: TextDirection.ltr,
      textScaler: MediaQuery.textScalerOf(context),
    )..layout();
    final baseline = painter.computeDistanceToActualBaseline(
      TextBaseline.alphabetic,
    );
    // Top of the mark, measured from the top of the word.
    final markTop = baseline - metrics.lift - metrics.markHeight;
    // The sidebar is 200 px inside its padding. Unbounded at 22 px is wider
    // than that, so the lockup scales down instead of painting an overflow.
    return FittedBox(
      fit: BoxFit.scaleDown,
      alignment: Alignment.centerLeft,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.only(top: math.max(0, markTop)),
            child: SvgPicture.asset(
              onDark
                  ? 'assets/logo/mark-on-dark.svg'
                  : 'assets/logo/mark-on-light.svg',
              width: metrics.markWidth,
              height: metrics.markHeight,
              semanticsLabel: 'Знак Горизонт',
            ),
          ),
          SizedBox(width: metrics.gap),
          Padding(
            padding: EdgeInsets.only(top: math.max(0, -markTop)),
            child: Text('Kassandra', style: style),
          ),
        ],
      ),
    );
  }
}

class KassandraMark extends StatelessWidget {
  const KassandraMark({super.key, this.size = 36});
  final double size;

  @override
  Widget build(BuildContext context) {
    return SvgPicture.asset(
      'assets/logo/app-icon.svg',
      width: size,
      height: size,
      semanticsLabel: 'Kassandra',
    );
  }
}
