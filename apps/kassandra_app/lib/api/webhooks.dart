import 'models.dart';

/// Webhooks of an account: outside systems push data automatically (`/api/v1/.../webhooks`).
class WebhookKind {
  const WebhookKind({
    required this.kind,
    required this.label,
    required this.contentType,
    required this.example,
  });

  factory WebhookKind.fromJson(Map<String, dynamic> json) => WebhookKind(
    kind: json['kind'].toString(),
    label: (json['label'] ?? '').toString(),
    contentType: (json['content_type'] ?? 'application/json').toString(),
    example: (json['example'] ?? '').toString(),
  );

  final String kind;
  final String label;
  final String contentType;
  final String example;
}

class WebhookInfo {
  const WebhookInfo({
    required this.id,
    required this.kind,
    required this.kindLabel,
    required this.label,
    required this.createdAt,
    required this.active,
    required this.path,
    required this.deliveries,
    this.lastAt,
    this.lastStatus,
    this.lastSummary,
    this.lastError,
    this.secret,
  });

  factory WebhookInfo.fromJson(Map<String, dynamic> json) {
    final last = json['last_delivery'];
    final map = last is Map<String, dynamic> ? last : const <String, dynamic>{};
    return WebhookInfo(
      id: json['id'].toString(),
      kind: json['kind'].toString(),
      kindLabel: (json['kind_label'] ?? '').toString(),
      label: (json['label'] ?? '').toString(),
      createdAt: DateTime.tryParse((json['created_at'] ?? '').toString()),
      active: asBool(json['active']),
      path: (json['path'] ?? '').toString(),
      deliveries: asInt(json['deliveries']),
      lastAt: DateTime.tryParse((map['received_at'] ?? '').toString()),
      lastStatus: map['status']?.toString(),
      lastSummary: map['summary']?.toString(),
      lastError: map['error']?.toString(),
      secret: json['secret']?.toString(),
    );
  }

  final String id;
  final String kind;
  final String kindLabel;
  final String label;
  final DateTime? createdAt;
  final bool active;

  /// Path of the receiving end, `/api/v1/hooks/<id>`.
  final String path;
  final int deliveries;
  final DateTime? lastAt;
  final String? lastStatus;
  final String? lastSummary;
  final String? lastError;

  /// Only right after creation: the list never returns it.
  final String? secret;
}

class WebhookDelivery {
  const WebhookDelivery({
    required this.id,
    required this.webhookId,
    required this.kind,
    required this.receivedAt,
    required this.status,
    required this.test,
    required this.summary,
    this.error,
  });

  factory WebhookDelivery.fromJson(Map<String, dynamic> json) =>
      WebhookDelivery(
        id: asInt(json['id']),
        webhookId: json['webhook_id'].toString(),
        kind: (json['kind'] ?? '').toString(),
        receivedAt: DateTime.tryParse((json['received_at'] ?? '').toString()),
        status: (json['status'] ?? '').toString(),
        test: asBool(json['test']),
        summary: (json['summary'] ?? '').toString(),
        error: json['error']?.toString(),
      );

  final int id;
  final String webhookId;
  final String kind;
  final DateTime? receivedAt;

  /// ok, checked (test without writing) or error.
  final String status;
  final bool test;
  final String summary;
  final String? error;
}

/// The webhook's health from real deliveries: waiting, working, failing or disabled.
class WebhookCheck {
  const WebhookCheck({
    required this.status,
    required this.summary,
    required this.deliveries,
  });

  factory WebhookCheck.fromJson(Map<String, dynamic> json) => WebhookCheck(
    status: (json['status'] ?? 'waiting').toString(),
    summary: (json['summary'] ?? '').toString(),
    deliveries: asInt(json['deliveries']),
  );

  final String status;
  final String summary;
  final int deliveries;
}
