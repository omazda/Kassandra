/// Money and dates. Amounts stay in integer kopecks until this layer.
library;

const nbsp = '\u00A0';
const minus = '\u2212';

const _genitive = [
  '',
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
];

const _shortMonth = [
  '',
  'янв',
  'фев',
  'мар',
  'апр',
  'мая',
  'июн',
  'июл',
  'авг',
  'сен',
  'окт',
  'ноя',
  'дек',
];

String groupDigits(String digits) {
  final buffer = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buffer.write(nbsp);
    buffer.write(digits[i]);
  }
  return buffer.toString();
}

/// Nearest ruble, half up, away from zero. «−392 029 ₽».
String formatMoneyExact(int minor) {
  final negative = minor < 0;
  final rub = (minor.abs() + 50) ~/ 100;
  final body = groupDigits('$rub');
  return '${negative ? minus : ''}$body$nbsp₽';
}

/// Same rounding as the API helper `fmt_money`.
/// [ceil] rounds a deficit up so it is not understated («393 тыс. ₽»).
String formatMoneyShort(int minor, {bool ceil = false}) {
  final negative = minor < 0;
  final absMinor = minor.abs();
  final body = _shortPositive(absMinor, ceil);
  if (!negative || absMinor == 0) return body;
  return '$minus$body';
}

String _shortPositive(int minor, bool ceil) {
  // A billion and up: «14,52 млрд ₽», not «14515,28 млн ₽» (dataset totals on the debug page).
  if (minor >= 99950000000) {
    final hundredths = _div(minor, 1000000000, ceil);
    final whole = hundredths ~/ 100;
    final frac = hundredths % 100;
    final text = frac == 0
        ? groupDigits('$whole')
        : '${groupDigits('$whole')},${frac.toString().padLeft(2, '0')}';
    return '$text млрд$nbsp₽';
  }
  if (minor >= 99950000) {
    final hundredths = _div(minor, 1000000, ceil);
    final whole = hundredths ~/ 100;
    final frac = hundredths % 100;
    final text = frac == 0
        ? '$whole'
        : '$whole,${frac.toString().padLeft(2, '0')}';
    return '$text млн$nbsp₽';
  }
  if (minor >= 1000000) {
    final thousands = _div(minor, 100000, ceil);
    return '$thousands тыс.$nbsp₽';
  }
  if (minor >= 100000) {
    final tenths = _div(minor, 10000, ceil);
    final whole = tenths ~/ 10;
    final frac = tenths % 10;
    return '$whole,$frac тыс.$nbsp₽';
  }
  final rub = _div(minor, 100, ceil);
  return '${groupDigits('$rub')}$nbsp₽';
}

int _div(int numerator, int denominator, bool ceil) {
  if (ceil) return (numerator + denominator - 1) ~/ denominator;
  return (numerator + denominator ~/ 2) ~/ denominator;
}

/// Rubles typed by a person → kopecks. Null when the text is not a sum.
int? parseRublesToMinor(String raw) {
  var text = raw.trim();
  if (text.isEmpty) return null;
  text = text
      .replaceAll(nbsp, '')
      .replaceAll(' ', '')
      .replaceAll('₽', '')
      .replaceAll(minus, '-')
      .replaceAll('−', '-')
      .replaceAll(',', '.');
  if (text.startsWith('+')) text = text.substring(1);
  final negative = text.startsWith('-');
  if (negative) text = text.substring(1);
  if (!RegExp(r'^\d+(\.\d{1,2})?$').hasMatch(text)) return null;
  final parts = text.split('.');
  final rub = int.parse(parts[0]);
  final kop = parts.length == 1 ? 0 : int.parse(parts[1].padRight(2, '0'));
  final minor = rub * 100 + kop;
  return negative ? -minor : minor;
}

String formatDateFull(DateTime date) =>
    '${date.day} ${_genitive[date.month]} ${date.year}';

String formatDateShort(DateTime date) =>
    '${date.day} ${_shortMonth[date.month]}';

String formatDateDot(DateTime date) {
  final d = date.day.toString().padLeft(2, '0');
  final m = date.month.toString().padLeft(2, '0');
  return '$d.$m.${date.year}';
}

String formatDateDayMonth(DateTime date) => formatDateDot(date).substring(0, 5);

DateTime? parseLooseDate(String raw) {
  final text = raw.trim().toLowerCase();
  final iso = RegExp(r'^(\d{4})-(\d{2})-(\d{2})$').firstMatch(text);
  if (iso != null) {
    return DateTime.utc(
      int.parse(iso.group(1)!),
      int.parse(iso.group(2)!),
      int.parse(iso.group(3)!),
    );
  }
  final dot = RegExp(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$').firstMatch(text);
  if (dot != null) {
    return DateTime.utc(
      int.parse(dot.group(3)!),
      int.parse(dot.group(2)!),
      int.parse(dot.group(1)!),
    );
  }
  final named = RegExp(r'^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$').firstMatch(text);
  if (named != null) {
    const names = {
      'января': 1,
      'февраля': 2,
      'марта': 3,
      'апреля': 4,
      'мая': 5,
      'июня': 6,
      'июля': 7,
      'августа': 8,
      'сентября': 9,
      'октября': 10,
      'ноября': 11,
      'декабря': 12,
    };
    final month = names[named.group(2)];
    if (month == null) return null;
    return DateTime.utc(
      int.parse(named.group(3)!),
      month,
      int.parse(named.group(1)!),
    );
  }
  return null;
}

String countNoun(int count, String one, String few, String many) {
  final n = count.abs();
  final n100 = n % 100;
  final n10 = n % 10;
  if (n100 >= 11 && n100 <= 14) return many;
  if (n10 == 1) return one;
  if (n10 >= 2 && n10 <= 4) return few;
  return many;
}

String operationsPhrase(int count) =>
    '$count ${countNoun(count, 'операция', 'операции', 'операций')}';

String eventsPhrase(int count) =>
    '$count ${countNoun(count, 'событие', 'события', 'событий')}';

String daysWord(int count) {
  final n = count.abs();
  final n100 = n % 100;
  final n10 = n % 10;
  final noun = (n100 >= 11 && n100 <= 14)
      ? 'дней'
      : n10 == 1
      ? 'день'
      : (n10 >= 2 && n10 <= 4)
      ? 'дня'
      : 'дней';
  return '$n $noun';
}

/// Whole rubles, truncated toward zero. Evening balances on the calendar.
String formatMoneyTrunc(int minor) {
  final negative = minor < 0;
  final rub = minor.abs() ~/ 100;
  return '${negative ? minus : ''}${groupDigits('$rub')}$nbsp₽';
}

/// Deficit rounded up to a ruble so the banner does not understate it.
String formatMoneyCeil(int minor) {
  final negative = minor < 0;
  final rub = (minor.abs() + 99) ~/ 100;
  return '${negative ? minus : ''}${groupDigits('$rub')}$nbsp₽';
}

/// Always two kopecks. Operations table: «−541 784,06 ₽».
String formatMoneyKopecks(int minor, {bool plus = false}) {
  final negative = minor < 0;
  final abs = minor.abs();
  final rub = abs ~/ 100;
  final kop = (abs % 100).toString().padLeft(2, '0');
  final sign = negative
      ? minus
      : (plus && minor > 0)
      ? '+'
      : '';
  return '$sign${groupDigits('$rub')},$kop$nbsp₽';
}

/// Whole rubles without kopecks, otherwise two digits. Inflows get «+».
String formatPayment(int signedMinor) {
  final negative = signedMinor < 0;
  final abs = signedMinor.abs();
  final sign = negative
      ? minus
      : signedMinor > 0
      ? '+'
      : '';
  if (abs % 100 == 0) {
    return '$sign${groupDigits('${abs ~/ 100}')}$nbsp₽';
  }
  final kop = (abs % 100).toString().padLeft(2, '0');
  return '$sign${groupDigits('${abs ~/ 100}')},$kop$nbsp₽';
}

/// One decimal percent with a comma: 0.6332 → «63,3%».
String formatShare(double share) {
  final tenths = (share * 1000).round();
  final whole = tenths ~/ 10;
  final frac = tenths.abs() % 10;
  return '$whole,$frac%';
}

String formatDayMonth(DateTime date) => '${date.day} ${_genitive[date.month]}';

String formatWeekdayDate(DateTime date) {
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  return '${names[date.weekday - 1]}, ${date.day} ${_genitive[date.month]}';
}

String paymentsPhrase(int count) =>
    '$count ${countNoun(count, 'платёж', 'платежа', 'платежей')}';

String rublesField(int minor) {
  final abs = minor.abs();
  final rub = abs ~/ 100;
  final kop = abs % 100;
  final sign = minor < 0 ? '-' : '';
  if (kop == 0) return '$sign$rub';
  return '$sign$rub,${kop.toString().padLeft(2, '0')}';
}

String formatPercent(double value) {
  final percent = (value * 100).round();
  final sign = percent < 0 ? minus : '';
  return '$sign${percent.abs()}%';
}

String signedPercent(num value) {
  final rounded = value.round();
  if (rounded < 0) return '$minus${rounded.abs()}%';
  if (rounded > 0) return '+$rounded%';
  return '0%';
}
