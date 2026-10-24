import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/app/theme.dart';
import 'package:kassandra/design/logo.dart';
import 'package:kassandra/design/tokens.dart';

/// Lockups at 22 and 48 px. design/logo/lockup-dark.png and lockup-light.png
/// are the visual reference; these frames check the same baseline rule.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final loader = FontLoader('Unbounded');
    for (final file in const [
      'Unbounded-Medium.ttf',
      'Unbounded-SemiBold.ttf',
      'Unbounded-Bold.ttf',
    ]) {
      loader.addFont(rootBundle.load('assets/fonts/$file'));
    }
    await loader.load();
  });

  for (final size in const [22.0, 48.0]) {
    for (final dark in const [true, false]) {
      final name = '${size.toInt()}_${dark ? 'dark' : 'light'}';
      testWidgets('logo $name sits on the capital K', (tester) async {
        final background = dark
            ? const Color(0xFF0F2B2E)
            : const Color(0xFFF2F5F4);
        await tester.pumpWidget(
          MaterialApp(
            theme: buildKassandraTheme(
              dark ? KassandraColors.dark : KassandraColors.light,
              dark ? Brightness.dark : Brightness.light,
            ),
            home: ColoredBox(
              color: background,
              child: Center(
                child: RepaintBoundary(
                  key: const Key('lockup'),
                  child: KassandraLogo(fontSize: size, onDark: dark),
                ),
              ),
            ),
          ),
        );
        await tester.pump();

        final word = find.text('Kassandra');
        final mark = find.byType(SvgPicture);
        final textWidget = tester.widget<Text>(word);
        final painter = TextPainter(
          text: TextSpan(text: 'Kassandra', style: textWidget.style),
          textDirection: TextDirection.ltr,
        )..layout();
        final baseline = painter.computeDistanceToActualBaseline(
          TextBaseline.alphabetic,
        );
        final metrics = LogoMetrics.of(size);
        final textTop = tester.getTopLeft(word).dy;
        final markTop = tester.getTopLeft(mark).dy;
        expect(
          markTop,
          closeTo(textTop + baseline - metrics.lift - metrics.markHeight, 0.6),
        );
        expect(
          tester.getTopLeft(word).dx - tester.getTopRight(mark).dx,
          closeTo(metrics.gap, 0.6),
        );

        await expectLater(
          find.byKey(const Key('lockup')),
          matchesGoldenFile('goldens/logo_$name.png'),
        );
      });
    }
  }
}
