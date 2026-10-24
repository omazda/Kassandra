import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/format/format.dart';

void main() {
  test('exact rubles use a non-breaking space and a real minus', () {
    expect(formatMoneyExact(209902886), '2\u00A0099\u00A0029\u00A0₽');
    expect(formatMoneyExact(-39202853), '−392\u00A0029\u00A0₽');
    expect(formatMoneyExact(0), '0\u00A0₽');
  });

  test('short money matches the API rounding', () {
    expect(formatMoneyShort(39202853), '392 тыс.\u00A0₽');
    expect(formatMoneyShort(39202853, ceil: true), '393 тыс.\u00A0₽');
    expect(formatMoneyShort(134295043), '1,34 млн\u00A0₽');
    expect(formatMoneyShort(134295043, ceil: true), '1,35 млн\u00A0₽');
    expect(formatMoneyShort(374915292), '3,75 млн\u00A0₽');
    expect(formatMoneyShort(624021031), '6,24 млн\u00A0₽');
    expect(formatMoneyShort(95092190), '951 тыс.\u00A0₽');
    expect(formatMoneyShort(530000), '5,3 тыс.\u00A0₽');
    expect(formatMoneyShort(85000), '850\u00A0₽');
    expect(formatMoneyShort(-39202853), '−392 тыс.\u00A0₽');
  });

  test('dates', () {
    final may = DateTime.utc(2026, 5, 21);
    final april = DateTime.utc(2026, 4, 28);
    final full = DateTime.utc(2026, 4, 26);
    expect(formatDateShort(may), '21 мая');
    expect(formatDateShort(april), '28 апр');
    expect(formatDateFull(full), '26 апреля 2026');
    expect(formatDateDot(full), '26.04.2026');
    expect(parseLooseDate('20.05.2026'), DateTime.utc(2026, 5, 20));
    expect(parseLooseDate('20 мая 2026'), DateTime.utc(2026, 5, 20));
  });

  test('rubles typed by a person become kopecks', () {
    expect(parseRublesToMinor('300 000'), 30000000);
    expect(parseRublesToMinor('300000,50'), 30000050);
    expect(parseRublesToMinor(''), isNull);
    expect(parseRublesToMinor('12.345'), isNull);
  });
}
