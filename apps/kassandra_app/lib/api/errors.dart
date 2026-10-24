/// Human text for FastAPI `detail`. Never returns a raw JSON blob.
String humanizeDetail(Object? detail) {
  if (detail == null) return '';
  if (detail is String) return detail.trim();
  if (detail is List) {
    final lines = <String>[];
    for (final item in detail) {
      if (item is Map) {
        final message = item['msg'] ?? item['message'];
        if (message != null) lines.add(message.toString());
      } else if (item != null) {
        lines.add(item.toString());
      }
    }
    return lines.join('\n');
  }
  if (detail is Map && detail['msg'] != null) return detail['msg'].toString();
  return 'Запрос не выполнен. Проверьте поля и повторите.';
}

Map<String, String> fieldErrors(Object? detail) {
  final fields = <String, String>{};
  if (detail is! List) return fields;
  for (final item in detail) {
    if (item is! Map) continue;
    final message = item['msg']?.toString();
    final loc = item['loc'];
    if (message == null || loc is! List || loc.isEmpty) continue;
    fields[loc.last.toString()] = message;
  }
  return fields;
}

sealed class ApiException implements Exception {
  ApiException(this.message, {this.fields = const {}});

  final String message;
  final Map<String, String> fields;

  @override
  String toString() => message;
}

class UnauthorizedException extends ApiException {
  UnauthorizedException([super.message = 'Нужно войти']);
}

class ForbiddenException extends ApiException {
  ForbiddenException([super.message = 'Нет доступа к этому счёту']);
}

class ConflictException extends ApiException {
  ConflictException([
    super.message = 'Данные обновились, прогноз пересчитан',
    this.eventId,
  ]);

  /// Set when a history payment was already replaced. The id is the registry
  /// event the client should open next.
  final String? eventId;
}

class ValidationException extends ApiException {
  ValidationException(super.message, {super.fields});
}

class ApiFailure extends ApiException {
  ApiFailure(super.message);
}

ApiException mapHttpError(int? status, Object? body) {
  final detail = body is Map<String, dynamic> ? body['detail'] : body;
  final text = humanizeDetail(detail);
  switch (status) {
    case 401:
      return UnauthorizedException(text.isEmpty ? 'Нужно войти' : text);
    case 403:
      return ForbiddenException(
        text.isEmpty ? 'Нет доступа к этому счёту' : text,
      );
    case 409:
      if (text.startsWith('Ожидание уже исправлено')) {
        const marker = 'событие ';
        final index = text.lastIndexOf(marker);
        final id = index >= 0 ? text.substring(index + marker.length).trim() : '';
        return ConflictException(text, id.isEmpty ? null : id);
      }
      return ConflictException('Данные обновились, прогноз пересчитан');
    case 422:
      return ValidationException(
        text.isEmpty ? 'Проверьте поля формы' : text,
        fields: fieldErrors(detail),
      );
    default:
      return ApiFailure(text.isEmpty ? 'Не удалось выполнить запрос' : text);
  }
}
