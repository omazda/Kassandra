import 'package:flutter_test/flutter_test.dart';
import 'package:kassandra/api/errors.dart';

void main() {
  test('string detail stays human', () {
    expect(humanizeDetail('Нет доступа к счёту'), 'Нет доступа к счёту');
  });

  test('validation list becomes field text, not JSON', () {
    final detail = [
      {
        'loc': ['body', 'event', 'amount_minor'],
        'msg': 'Сумма должна быть положительной',
      },
    ];
    expect(humanizeDetail(detail), 'Сумма должна быть положительной');
    expect(
      fieldErrors(detail)['amount_minor'],
      'Сумма должна быть положительной',
    );
    expect(humanizeDetail(detail).contains('{'), isFalse);
  });

  test('status codes map to the product messages', () {
    expect(
      mapHttpError(401, {'detail': 'missing'}),
      isA<UnauthorizedException>(),
    );
    expect(mapHttpError(403, {'detail': 'nope'}).message, 'nope');
    expect(
      mapHttpError(409, {'detail': 'version'}).message,
      'Данные обновились, прогноз пересчитан',
    );
    expect(
      mapHttpError(422, {'detail': 'Проверьте дату'}),
      isA<ValidationException>(),
    );
    final unknown = mapHttpError(500, {'weird': true});
    expect(unknown.message.contains('{'), isFalse);
  });
}
