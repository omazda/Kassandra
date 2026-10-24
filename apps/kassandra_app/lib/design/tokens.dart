import 'package:flutter/material.dart';

class KRadius {
  static const panel = 28.0;
  static const card = 24.0;
  static const cardSmall = 20.0;
  static const tile = 16.0;
  static const button = 14.0;
  static const input = 14.0;
  static const control = 12.0;
  static const chip = 999.0;
}

class KSpace {
  static const s4 = 4.0;
  static const s8 = 8.0;
  static const s10 = 10.0;
  static const s12 = 12.0;
  static const s14 = 14.0;
  static const s16 = 16.0;
  static const s20 = 20.0;
  static const s24 = 24.0;
  static const s28 = 28.0;
  static const s32 = 32.0;
  static const s40 = 40.0;
  static const s48 = 48.0;
}

class KassandraColors extends ThemeExtension<KassandraColors> {
  const KassandraColors({
    required this.ink,
    required this.onInk,
    required this.inkSurface,
    required this.inkSelected,
    required this.inkMuted,
    required this.navMuted,
    required this.warning,
    required this.warningContainer,
    required this.warningOutline,
    required this.onWarningContainer,
    required this.danger,
    required this.positive,
    required this.positiveContainer,
    required this.onPositiveContainer,
    required this.surfaceMuted,
    required this.divider,
    required this.primary,
    required this.primaryContainer,
    required this.background,
    required this.surface,
    required this.text,
    required this.textMuted,
  });

  final Color ink;
  final Color onInk;
  final Color inkSurface;
  final Color inkSelected;
  final Color inkMuted;
  final Color navMuted;
  final Color warning;
  final Color warningContainer;
  final Color warningOutline;
  final Color onWarningContainer;
  final Color danger;
  final Color positive;
  final Color positiveContainer;
  final Color onPositiveContainer;
  final Color surfaceMuted;
  final Color divider;
  final Color primary;
  final Color primaryContainer;
  final Color background;
  final Color surface;
  final Color text;
  final Color textMuted;

  static const light = KassandraColors(
    ink: Color(0xFF0F2B2E),
    onInk: Color(0xFFF2F5F4),
    inkSurface: Color(0xFF17393D),
    inkSelected: Color(0xFF1E4B4F),
    inkMuted: Color(0xFFA9C3C0),
    navMuted: Color(0xFFC6D8D6),
    warning: Color(0xFFE39B2D),
    warningContainer: Color(0xFFFBEFD9),
    warningOutline: Color(0xFFF0D3A0),
    onWarningContainer: Color(0xFF8A5A12),
    danger: Color(0xFFB23E35),
    positive: Color(0xFF2F8F62),
    positiveContainer: Color(0xFFE3EFE9),
    onPositiveContainer: Color(0xFF3F5B54),
    surfaceMuted: Color(0xFFEEF1F0),
    divider: Color(0xFFE4EBE9),
    primary: Color(0xFF1E6E70),
    primaryContainer: Color(0xFFE6F0EF),
    background: Color(0xFFF2F5F4),
    surface: Color(0xFFFFFFFF),
    text: Color(0xFF0F2B2E),
    textMuted: Color(0xFF56676A),
  );

  static const dark = KassandraColors(
    ink: Color(0xFF0F2B2E),
    onInk: Color(0xFFE4EEEC),
    inkSurface: Color(0xFF17393D),
    inkSelected: Color(0xFF1E4B4F),
    inkMuted: Color(0xFF93AAA8),
    navMuted: Color(0xFF93AAA8),
    warning: Color(0xFFF0B04A),
    warningContainer: Color(0xFF3A2F18),
    warningOutline: Color(0xFF6A5324),
    onWarningContainer: Color(0xFFF0B04A),
    danger: Color(0xFFEE7B6C),
    positive: Color(0xFF5CC48F),
    positiveContainer: Color(0xFF173A33),
    onPositiveContainer: Color(0xFF5CC48F),
    surfaceMuted: Color(0xFF0E2224),
    divider: Color(0xFF23403F),
    primary: Color(0xFF6CC3BD),
    primaryContainer: Color(0xFF173538),
    background: Color(0xFF0A1A1C),
    surface: Color(0xFF13292C),
    text: Color(0xFFE4EEEC),
    textMuted: Color(0xFF93AAA8),
  );

  static KassandraColors of(BuildContext context) =>
      Theme.of(context).extension<KassandraColors>() ?? light;

  @override
  KassandraColors copyWith({
    Color? ink,
    Color? onInk,
    Color? inkSurface,
    Color? inkSelected,
    Color? inkMuted,
    Color? navMuted,
    Color? warning,
    Color? warningContainer,
    Color? warningOutline,
    Color? onWarningContainer,
    Color? danger,
    Color? positive,
    Color? positiveContainer,
    Color? onPositiveContainer,
    Color? surfaceMuted,
    Color? divider,
    Color? primary,
    Color? primaryContainer,
    Color? background,
    Color? surface,
    Color? text,
    Color? textMuted,
  }) {
    return KassandraColors(
      ink: ink ?? this.ink,
      onInk: onInk ?? this.onInk,
      inkSurface: inkSurface ?? this.inkSurface,
      inkSelected: inkSelected ?? this.inkSelected,
      inkMuted: inkMuted ?? this.inkMuted,
      navMuted: navMuted ?? this.navMuted,
      warning: warning ?? this.warning,
      warningContainer: warningContainer ?? this.warningContainer,
      warningOutline: warningOutline ?? this.warningOutline,
      onWarningContainer: onWarningContainer ?? this.onWarningContainer,
      danger: danger ?? this.danger,
      positive: positive ?? this.positive,
      positiveContainer: positiveContainer ?? this.positiveContainer,
      onPositiveContainer: onPositiveContainer ?? this.onPositiveContainer,
      surfaceMuted: surfaceMuted ?? this.surfaceMuted,
      divider: divider ?? this.divider,
      primary: primary ?? this.primary,
      primaryContainer: primaryContainer ?? this.primaryContainer,
      background: background ?? this.background,
      surface: surface ?? this.surface,
      text: text ?? this.text,
      textMuted: textMuted ?? this.textMuted,
    );
  }

  @override
  KassandraColors lerp(ThemeExtension<KassandraColors>? other, double t) {
    if (other is! KassandraColors) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return KassandraColors(
      ink: mix(ink, other.ink),
      onInk: mix(onInk, other.onInk),
      inkSurface: mix(inkSurface, other.inkSurface),
      inkSelected: mix(inkSelected, other.inkSelected),
      inkMuted: mix(inkMuted, other.inkMuted),
      navMuted: mix(navMuted, other.navMuted),
      warning: mix(warning, other.warning),
      warningContainer: mix(warningContainer, other.warningContainer),
      warningOutline: mix(warningOutline, other.warningOutline),
      onWarningContainer: mix(onWarningContainer, other.onWarningContainer),
      danger: mix(danger, other.danger),
      positive: mix(positive, other.positive),
      positiveContainer: mix(positiveContainer, other.positiveContainer),
      onPositiveContainer: mix(onPositiveContainer, other.onPositiveContainer),
      surfaceMuted: mix(surfaceMuted, other.surfaceMuted),
      divider: mix(divider, other.divider),
      primary: mix(primary, other.primary),
      primaryContainer: mix(primaryContainer, other.primaryContainer),
      background: mix(background, other.background),
      surface: mix(surface, other.surface),
      text: mix(text, other.text),
      textMuted: mix(textMuted, other.textMuted),
    );
  }
}

class LogoMetrics {
  const LogoMetrics({
    required this.fontSize,
    required this.markHeight,
    required this.markWidth,
    required this.gap,
    required this.lift,
  });

  /// The mark is as tall as a lowercase letter (Unbounded x-height 0.57 em),
  /// keeps the 48:25.5 proportions of the SVG and is centered on the capital K.
  factory LogoMetrics.of(double fontSize) {
    final height = fontSize * 0.57;
    return LogoMetrics(
      fontSize: fontSize,
      markHeight: height,
      markWidth: height * 48 / 25.5,
      gap: fontSize * 0.24,
      lift: fontSize * 0.375 - height / 2,
    );
  }

  final double fontSize;
  final double markHeight;
  final double markWidth;
  final double gap;

  /// Bottom of the mark sits this far above the text baseline,
  /// so the mark center meets the center of the capital K (0.375 em).
  final double lift;

  double get centerAboveBaseline => lift + markHeight / 2;
}

bool isPhone(BuildContext context) => MediaQuery.sizeOf(context).width < 600;

bool isDesktop(BuildContext context) =>
    MediaQuery.sizeOf(context).width >= 1024;

TextStyle unbounded(
  double size,
  FontWeight weight,
  Color color, {
  bool tabular = false,
  double tracking = -0.02,
}) {
  return TextStyle(
    fontFamily: 'Unbounded',
    fontSize: size,
    fontWeight: weight,
    color: color,
    letterSpacing: size * tracking,
    height: 1.15,
    fontFeatures: tabular ? const [FontFeature.tabularFigures()] : const [],
  );
}

TextStyle golos(
  double size,
  FontWeight weight,
  Color color, {
  double height = 1.35,
}) {
  return TextStyle(
    fontFamily: 'Golos Text',
    fontSize: size,
    fontWeight: weight,
    color: color,
    height: height,
  );
}

TextStyle mono(
  double size,
  Color color, {
  FontWeight weight = FontWeight.w400,
}) {
  return TextStyle(
    fontFamily: 'JetBrains Mono',
    fontSize: size,
    fontWeight: weight,
    color: color,
  );
}
