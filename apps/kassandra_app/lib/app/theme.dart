import 'package:flutter/material.dart';

import '../design/tokens.dart';

ThemeData buildKassandraTheme(KassandraColors colors, Brightness brightness) {
  final scheme =
      ColorScheme.fromSeed(
        seedColor: colors.primary,
        brightness: brightness,
      ).copyWith(
        primary: colors.primary,
        onPrimary: brightness == Brightness.dark
            ? colors.background
            : const Color(0xFFFFFFFF),
        secondary: colors.ink,
        onSecondary: colors.onInk,
        surface: colors.surface,
        onSurface: colors.text,
        onSurfaceVariant: colors.textMuted,
        error: colors.danger,
        onError: const Color(0xFFFFFFFF),
        outline: colors.divider,
        primaryContainer: colors.primaryContainer,
      );
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: colors.background,
    fontFamily: 'Golos Text',
    splashFactory: InkRipple.splashFactory,
    extensions: [colors],
  );
  final buttonText = golos(15, FontWeight.w600, scheme.onPrimary);
  return base.copyWith(
    textTheme: base.textTheme.apply(
      fontFamily: 'Golos Text',
      bodyColor: colors.text,
      displayColor: colors.text,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(44, 48),
        backgroundColor: colors.primary,
        foregroundColor: scheme.onPrimary,
        textStyle: buttonText.copyWith(color: scheme.onPrimary),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(KRadius.button),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(44, 48),
        foregroundColor: colors.text,
        textStyle: golos(15, FontWeight.w500, colors.text),
        side: BorderSide(color: colors.divider),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(KRadius.button),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        minimumSize: const Size(44, 44),
        foregroundColor: colors.primary,
        textStyle: golos(15, FontWeight.w600, colors.primary),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: colors.surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      hintStyle: golos(15, FontWeight.w400, colors.textMuted),
      labelStyle: golos(13, FontWeight.w400, colors.textMuted),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(KRadius.input),
        borderSide: BorderSide(color: colors.divider),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(KRadius.input),
        borderSide: BorderSide(color: colors.divider),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(KRadius.input),
        borderSide: BorderSide(color: colors.primary, width: 2),
      ),
    ),
    dividerColor: colors.divider,
    cardTheme: CardThemeData(
      color: colors.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(KRadius.card),
      ),
    ),
  );
}
