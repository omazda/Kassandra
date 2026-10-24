import 'package:dio/dio.dart';

import 'ai.dart';
import 'debug.dart';
import 'errors.dart';
import 'kassandra_api.dart';
import 'models.dart';
import 'v11.dart';
import 'views.dart';
import 'webhooks.dart';

class HttpApi implements KassandraApi {
  HttpApi(this._dio);
  final Dio _dio;

  Future<T> _send<T>(
    Future<Response<dynamic>> future,
    T Function(dynamic data) parse,
  ) async {
    try {
      final response = await future;
      return parse(response.data);
    } on DioException catch (error) {
      throw mapHttpError(error.response?.statusCode, error.response?.data);
    }
  }

  Options _idem(String key) => Options(headers: {'Idempotency-Key': key});

  @override
  Future<List<Account>> accounts() {
    return _send(_dio.get<dynamic>('/api/v1/accounts'), (data) {
      final list = data as List;
      return [
        for (final item in list) Account.fromJson(item as Map<String, dynamic>),
      ];
    });
  }

  @override
  Future<ForecastResult> createForecast(
    ForecastRequest request, {
    required String idempotencyKey,
  }) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/forecasts',
        data: request.toJson(),
        options: _idem(idempotencyKey),
      ),
      (data) => ForecastResult.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<GapExplanation> forecastExplanation(String runId) {
    return _send(
      _dio.get<dynamic>('/api/v1/forecasts/$runId/explanation'),
      (data) => GapExplanation.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<List<DailyPoint>> daily(String accountId) {
    return _send(_dio.get<dynamic>('/api/v1/accounts/$accountId/daily'), (
      data,
    ) {
      return [
        for (final item in data as List)
          DailyPoint.fromJson(item as Map<String, dynamic>),
      ];
    });
  }

  @override
  Future<List<TopClient>> topClients(String accountId, DateTime asOf) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/top-clients',
        queryParameters: {'as_of': isoDate(asOf)},
      ),
      (data) => [
        for (final item in data as List)
          TopClient.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  @override
  Future<ScenarioResult> evaluateScenario(
    ScenarioSpec spec, {
    required String idempotencyKey,
  }) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/scenarios/evaluate',
        data: spec.toJson(),
        options: _idem(idempotencyKey),
      ),
      (data) => ScenarioResult.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<GapExplanation> scenarioExplanation(String scenarioId) {
    return _send(
      _dio.get<dynamic>('/api/v1/scenarios/$scenarioId/explanation'),
      (data) => GapExplanation.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<PlannedEvent> createEvent(PlannedEvent event, {String? baseRunId}) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/events',
        data: {'event': event.toJson(), 'base_run_id': ?baseRunId},
      ),
      (data) => PlannedEvent.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<List<PlannedEvent>> events(String accountId, DateTime asOf) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/events',
        queryParameters: {'as_of': isoDate(asOf)},
      ),
      (data) => [
        for (final item in data as List)
          PlannedEvent.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  @override
  Future<PlannedEvent> rescheduleEvent(
    String eventId,
    DateTime expectedDate,
    DateTime knownAt,
  ) {
    return _send(
      _dio.patch<dynamic>(
        '/api/v1/events/$eventId',
        data: {
          'expected_date': isoDate(expectedDate),
          'known_at': isoDate(knownAt),
        },
      ),
      (data) => PlannedEvent.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<void> markPayment(
    String eventId,
    String operationId,
    int amountMinor,
    DateTime knownAt,
  ) async {
    await _send(
      _dio.post<dynamic>(
        '/api/v1/events/$eventId/payments',
        data: {
          'operation_id': operationId,
          'amount_minor': amountMinor,
          'known_at': isoDate(knownAt),
        },
      ),
      (_) => null,
    );
  }

  @override
  Future<ImportReport> importEvents(
    List<int> bytes,
    String filename, {
    String? baseRunId,
  }) {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    return _send(
      _dio.post<dynamic>(
        '/api/v1/imports/events',
        data: form,
        queryParameters: {'base_run_id': ?baseRunId},
      ),
      ImportReport.fromResponse,
    );
  }

  @override
  Future<TransactionPage> transactions(
    String accountId,
    TransactionQuery query,
  ) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/transactions',
        queryParameters: query.toParams(),
        options: Options(listFormat: ListFormat.multi),
      ),
      (data) => TransactionPage.fromJson(_map(data)),
    );
  }

  @override
  Future<void> updateCategory(
    String accountId,
    String operationId,
    String category,
    DateTime knownAt,
  ) async {
    await _send(
      _dio.patch<dynamic>(
        '/api/v1/transactions/$accountId/$operationId/category',
        data: {'category': category, 'known_at': isoDate(knownAt)},
      ),
      (_) => null,
    );
  }

  @override
  Future<PlanFact> planFact(String runId) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/plan-fact',
        queryParameters: {'run_id': runId},
      ),
      (data) => PlanFact.fromJson(data as Map<String, dynamic>),
    );
  }

  @override
  Future<ImportReport> importTransactions(List<int> bytes, String filename) {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    return _send(
      _dio.post<dynamic>('/api/v1/imports/transactions', data: form),
      ImportReport.fromResponse,
    );
  }

  @override
  Future<String> experimentsSummary() {
    return _send(_dio.get<dynamic>('/api/v1/experiments'), (data) {
      if (data is Map && data['registry'] is List) {
        return 'В реестре экспериментов: ${(data['registry'] as List).length}';
      }
      return 'Сводка экспериментов недоступна';
    });
  }

  Map<String, dynamic> _map(dynamic data) =>
      data is Map<String, dynamic> ? data : <String, dynamic>{};

  @override
  Future<Dictionaries> dictionaries() {
    return _send(
      _dio.get<dynamic>('/api/v1/dictionaries'),
      (data) => Dictionaries.fromJson(_map(data)),
    );
  }

  @override
  Future<RefreshResult> refreshForecast(String runId) {
    return _send(
      _dio.post<dynamic>('/api/v1/forecasts/$runId/refresh'),
      (data) => RefreshResult.fromJson(_map(data)),
    );
  }

  @override
  Future<List<AlertItem>> alerts(String accountId, String runId) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/alerts',
        queryParameters: {'run_id': runId},
      ),
      (data) => [
        for (final item in ((_map(data)['alerts'] as List?) ?? const []))
          if (item is Map<String, dynamic>) AlertItem.fromJson(item),
      ],
    );
  }

  @override
  Future<EventParse> parseEvent(String text, DateTime today) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/events/parse',
        data: {'text': text, 'today': isoDate(today)},
      ),
      (data) => EventParse.fromJson(_map(data)),
    );
  }

  @override
  Future<Map<String, dynamic>> taxCalendar(
    String accountId,
    DateTime asOf,
    String? runId,
  ) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/tax-calendar',
        queryParameters: {'as_of': isoDate(asOf), 'run_id': ?runId},
      ),
      _map,
    );
  }

  @override
  Future<RegisterResult> applyTaxCalendar(String accountId, String? baseRunId) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/accounts/$accountId/tax-calendar/apply',
        data: {'base_run_id': ?baseRunId},
      ),
      (data) => RegisterResult.fromJson(_map(data)),
    );
  }

  @override
  Future<List<RecurringItem>> recurring(String accountId, DateTime asOf) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/recurring',
        queryParameters: {'as_of': isoDate(asOf)},
      ),
      (data) => [
        for (final item in ((_map(data)['components'] as List?) ?? const []))
          if (item is Map<String, dynamic>) RecurringItem.fromJson(item),
      ],
    );
  }

  @override
  Future<CategorySummary> categorySummary(
    String accountId,
    TransactionQuery query,
  ) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/categories/summary',
        queryParameters: query.summaryParams(),
      ),
      (data) => CategorySummary.fromJson(_map(data)),
    );
  }

  @override
  Future<CalendarView> paymentCalendar(String runId, CalendarQuery query) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/forecasts/$runId/calendar',
        queryParameters: query.toParams(),
        options: Options(listFormat: ListFormat.multi),
      ),
      (data) => CalendarView.fromJson(_map(data)),
    );
  }

  @override
  Future<void> patchEvent(String eventId, Map<String, dynamic> fields) async {
    await _send(
      _dio.patch<dynamic>('/api/v1/events/$eventId', data: fields),
      (_) => null,
    );
  }

  @override
  Future<void> deleteEvent(String eventId, DateTime knownAt) async {
    await _send(
      _dio.delete<dynamic>(
        '/api/v1/events/$eventId',
        queryParameters: {'known_at': isoDate(knownAt)},
      ),
      (_) => null,
    );
  }

  @override
  Future<void> editAssumption(
    String runId,
    String assumptionId,
    Map<String, dynamic> body,
  ) async {
    await _send(
      _dio.post<dynamic>(
        '/api/v1/forecasts/$runId/assumptions/$assumptionId',
        data: body,
      ),
      (_) => null,
    );
  }

  @override
  Future<Map<String, dynamic>> sources(String accountId) {
    return _send(
      _dio.get<dynamic>('/api/v1/accounts/$accountId/sources'),
      _map,
    );
  }

  @override
  Future<RegisterResult> createContract(
    String accountId,
    Map<String, dynamic> body,
  ) {
    return _send(
      _dio.post<dynamic>('/api/v1/accounts/$accountId/contracts', data: body),
      (data) => RegisterResult.fromJson(_map(data)),
    );
  }

  @override
  Future<RegisterResult> createInvoice(
    String accountId,
    Map<String, dynamic> body,
  ) {
    return _send(
      _dio.post<dynamic>('/api/v1/accounts/$accountId/invoices', data: body),
      (data) => RegisterResult.fromJson(_map(data)),
    );
  }

  @override
  Future<RegisterResult> importFile(
    String path,
    List<int> bytes,
    String filename,
  ) {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    return _send(
      _dio.post<dynamic>(path, data: form),
      (data) => RegisterResult.fromJson(_map(data)),
    );
  }

  @override
  Future<RegisterResult> postJson(String path, Map<String, dynamic> body) {
    return _send(
      _dio.post<dynamic>(path, data: body),
      (data) => RegisterResult.fromJson(_map(data)),
    );
  }

  @override
  Future<Map<String, dynamic>> planFactWeek(String runId) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/plan-fact',
        queryParameters: {'run_id': runId, 'granularity': 'week'},
      ),
      _map,
    );
  }

  @override
  Future<Map<String, dynamic>> reconcile(
    String accountId, {
    bool dryRun = true,
  }) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/accounts/$accountId/reconcile',
        queryParameters: {'dry_run': dryRun},
      ),
      _map,
    );
  }

  @override
  Future<List<CounterpartyRisk>> counterpartyRisks(
    String accountId,
    DateTime asOf,
  ) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/counterparty-risks',
        queryParameters: {'as_of': isoDate(asOf)},
      ),
      (data) => [
        for (final item
            in ((_map(data)['counterparties'] as List?) ?? const []))
          if (item is Map<String, dynamic>) CounterpartyRisk.fromJson(item),
      ],
    );
  }

  @override
  Future<List<DebugDataset>> debugDatasets() {
    return _send(
      _dio.get<dynamic>('/api/v1/debug/datasets'),
      (data) => [
        for (final item in data as List)
          DebugDataset.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  @override
  Future<DebugDataset> debugDataset(String id) {
    return _send(
      _dio.get<dynamic>('/api/v1/debug/datasets/$id'),
      (data) => DebugDataset.fromJson(_map(data)),
    );
  }

  @override
  Future<DebugDataset> uploadDebugDataset(
    List<int> bytes,
    String filename, {
    void Function(int sent, int total)? onProgress,
    bool history = true,
  }) {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    return _send(
      _dio.post<dynamic>(
        '/api/v1/debug/datasets',
        data: form,
        queryParameters: {'history': history},
        onSendProgress: onProgress,
        // The server parses and imports the whole set before it answers: about 30 s for 222k rows.
        options: Options(receiveTimeout: const Duration(minutes: 5)),
      ),
      (data) => DebugDataset.fromJson(_map(data)),
    );
  }

  @override
  Future<void> deleteDebugDataset(String id) {
    return _send(_dio.delete<dynamic>('/api/v1/debug/datasets/$id'), (_) {});
  }

  @override
  Future<DebugCheck> debugCheck(String accountId) {
    return _send(
      _dio.get<dynamic>('/api/v1/debug/accounts/$accountId/check'),
      (data) => DebugCheck.fromJson(_map(data)),
    );
  }

  @override
  Future<EvaluationState> startEvaluation(String datasetId) {
    return _send(
      _dio.post<dynamic>('/api/v1/debug/datasets/$datasetId/evaluate'),
      (data) => EvaluationState.fromJson(_map(data)),
    );
  }

  @override
  Future<EvaluationState> evaluation(String datasetId) {
    return _send(
      _dio.get<dynamic>('/api/v1/debug/datasets/$datasetId/evaluation'),
      (data) => EvaluationState.fromJson(_map(data)),
    );
  }

  @override
  Future<EvaluationCompany> evaluationCompany(
    String datasetId,
    String accountId,
  ) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/debug/datasets/$datasetId/evaluation/companies/$accountId',
      ),
      (data) => EvaluationCompany.fromJson(_map(data)),
    );
  }

  @override
  Future<String> evaluationReport(String datasetId) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/debug/datasets/$datasetId/evaluation/report.md',
        options: Options(responseType: ResponseType.plain),
      ),
      (data) => '$data',
    );
  }

  @override
  Future<List<TraceStep>> forecastTrace(String runId) {
    return _send(
      _dio.get<dynamic>('/api/v1/forecasts/$runId/trace'),
      (data) => [
        for (final item in data as List)
          TraceStep.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  // The LLM thinks before it answers: up to a minute for a review or a chat turn with tools.
  static final _slow = Options(receiveTimeout: const Duration(minutes: 4));

  @override
  Future<AiReview?> aiCachedReview(String runId) {
    return _send(
      _dio.get<dynamic>('/api/v1/ai/forecasts/$runId/review'),
      (data) => data is Map<String, dynamic> ? AiReview.fromJson(data) : null,
    );
  }

  @override
  Future<AiReview> aiReview(String runId, {bool refresh = false}) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/ai/forecasts/$runId/review',
        queryParameters: {'refresh': refresh},
        options: _slow,
      ),
      (data) => AiReview.fromJson(_map(data)),
    );
  }

  @override
  Future<AiModelReview> aiModelReview(String datasetId) {
    return _send(
      _dio.post<dynamic>('/api/v1/ai/debug/$datasetId/review', options: _slow),
      (data) => AiModelReview.fromJson(_map(data)),
    );
  }

  @override
  Future<AiOcrResult> aiOcr(
    List<int> bytes,
    String filename,
    String? accountId,
  ) {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
      'account_id': ?accountId,
    });
    return _send(
      _dio.post<dynamic>('/api/v1/ai/ocr', data: form, options: _slow),
      (data) => AiOcrResult.fromJson(_map(data)),
    );
  }

  @override
  Future<AiChatReply> aiChat(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  ) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/ai/chat',
        data: {'messages': messages, 'context': context},
        options: _slow,
      ),
      (data) => AiChatReply.fromJson(_map(data)),
    );
  }

  @override
  Future<String> aiChatStart(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  ) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/ai/chat/jobs',
        data: {'messages': messages, 'context': context},
      ),
      (data) => _map(data)['job_id'].toString(),
    );
  }

  @override
  Future<AiChatProgress> aiChatProgress(String jobId) {
    return _send(
      _dio.get<dynamic>('/api/v1/ai/chat/jobs/$jobId'),
      (data) => AiChatProgress.fromJson(_map(data)),
    );
  }

  @override
  Future<List<WebhookKind>> webhookKinds() {
    return _send(
      _dio.get<dynamic>('/api/v1/webhooks/kinds'),
      (data) => [
        for (final item in data as List)
          WebhookKind.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  @override
  Future<List<WebhookInfo>> webhooks(String accountId) {
    return _send(
      _dio.get<dynamic>('/api/v1/accounts/$accountId/webhooks'),
      (data) => [
        for (final item in data as List)
          WebhookInfo.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  @override
  Future<WebhookInfo> createWebhook(
    String accountId,
    String kind,
    String? label,
  ) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/accounts/$accountId/webhooks',
        data: {'kind': kind, 'label': ?label},
      ),
      (data) => WebhookInfo.fromJson(_map(data)),
    );
  }

  @override
  Future<String> rotateWebhook(String id) {
    return _send(
      _dio.post<dynamic>('/api/v1/webhooks/$id/rotate'),
      (data) => _map(data)['secret'].toString(),
    );
  }

  @override
  Future<void> deleteWebhook(String id) {
    return _send(_dio.delete<dynamic>('/api/v1/webhooks/$id'), (_) {});
  }

  @override
  Future<WebhookCheck> checkWebhook(String id) {
    return _send(
      _dio.post<dynamic>('/api/v1/webhooks/$id/check'),
      (data) => WebhookCheck.fromJson(_map(data)),
    );
  }

  @override
  Future<String> validateWebhook(String id, String body, String contentType) {
    return _send(
      _dio.post<dynamic>(
        '/api/v1/webhooks/$id/validate',
        data: body,
        options: Options(contentType: contentType),
      ),
      (data) => _map(data)['summary'].toString(),
    );
  }

  @override
  Future<List<WebhookDelivery>> webhookDeliveries(String accountId) {
    return _send(
      _dio.get<dynamic>(
        '/api/v1/accounts/$accountId/webhooks/deliveries',
        queryParameters: {'limit': 30},
      ),
      (data) => [
        for (final item in data as List)
          WebhookDelivery.fromJson(item as Map<String, dynamic>),
      ],
    );
  }
}
