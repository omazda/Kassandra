import 'ai.dart';
import 'debug.dart';
import 'models.dart';
import 'v11.dart';
import 'views.dart';
import 'webhooks.dart';

abstract class KassandraApi {
  Future<List<Account>> accounts();
  Future<ForecastResult> createForecast(
    ForecastRequest request, {
    required String idempotencyKey,
  });
  Future<GapExplanation> forecastExplanation(String runId);
  Future<List<DailyPoint>> daily(String accountId);
  Future<List<TopClient>> topClients(String accountId, DateTime asOf);
  Future<ScenarioResult> evaluateScenario(
    ScenarioSpec spec, {
    required String idempotencyKey,
  });
  Future<GapExplanation> scenarioExplanation(String scenarioId);
  Future<PlannedEvent> createEvent(PlannedEvent event, {String? baseRunId});
  Future<List<PlannedEvent>> events(String accountId, DateTime asOf);
  Future<PlannedEvent> rescheduleEvent(
    String eventId,
    DateTime expectedDate,
    DateTime knownAt,
  );
  Future<void> markPayment(
    String eventId,
    String operationId,
    int amountMinor,
    DateTime knownAt,
  );
  Future<ImportReport> importEvents(
    List<int> bytes,
    String filename, {
    String? baseRunId,
  });
  Future<TransactionPage> transactions(
    String accountId,
    TransactionQuery query,
  );
  Future<void> updateCategory(
    String accountId,
    String operationId,
    String category,
    DateTime knownAt,
  );
  Future<PlanFact> planFact(String runId);
  Future<ImportReport> importTransactions(List<int> bytes, String filename);
  Future<String> experimentsSummary();
  Future<Dictionaries> dictionaries();
  Future<RefreshResult> refreshForecast(String runId);
  Future<List<AlertItem>> alerts(String accountId, String runId);
  Future<EventParse> parseEvent(String text, DateTime today);
  Future<Map<String, dynamic>> taxCalendar(
    String accountId,
    DateTime asOf,
    String? runId,
  );
  Future<RegisterResult> applyTaxCalendar(String accountId, String? baseRunId);
  Future<List<RecurringItem>> recurring(String accountId, DateTime asOf);
  Future<CategorySummary> categorySummary(
    String accountId,
    TransactionQuery query,
  );
  Future<CalendarView> paymentCalendar(String runId, CalendarQuery query);
  Future<void> patchEvent(String eventId, Map<String, dynamic> fields);
  Future<void> deleteEvent(String eventId, DateTime knownAt);
  Future<void> editAssumption(
    String runId,
    String assumptionId,
    Map<String, dynamic> body,
  );
  Future<Map<String, dynamic>> sources(String accountId);
  Future<RegisterResult> createContract(
    String accountId,
    Map<String, dynamic> body,
  );
  Future<RegisterResult> createInvoice(
    String accountId,
    Map<String, dynamic> body,
  );
  Future<RegisterResult> importFile(
    String path,
    List<int> bytes,
    String filename,
  );

  /// JSON body for bank, marketplace and OFD feeds. Those routes reject multipart.
  Future<RegisterResult> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> planFactWeek(String runId);
  Future<Map<String, dynamic>> reconcile(
    String accountId, {
    bool dryRun = true,
  });
  Future<List<CounterpartyRisk>> counterpartyRisks(
    String accountId,
    DateTime asOf,
  );

  /// Debug page: hackathon-format datasets under their own dbgN- account ids.
  Future<List<DebugDataset>> debugDatasets();
  Future<DebugDataset> debugDataset(String id);
  Future<DebugDataset> uploadDebugDataset(
    List<int> bytes,
    String filename, {
    void Function(int sent, int total)? onProgress,
    bool history = true,
  });
  Future<void> deleteDebugDataset(String id);
  Future<DebugCheck> debugCheck(String accountId);

  /// The whole-dataset check runs on the server in the background; the page polls its state.
  Future<EvaluationState> startEvaluation(String datasetId);
  Future<EvaluationState> evaluation(String datasetId);
  Future<EvaluationCompany> evaluationCompany(
    String datasetId,
    String accountId,
  );
  Future<String> evaluationReport(String datasetId);

  /// How the forecast was built: the steps from the statement to the gap.
  Future<List<TraceStep>> forecastTrace(String runId);

  /// LLM: review of a forecast (cached on the server per run), OCR, chat agent.
  Future<AiReview?> aiCachedReview(String runId);
  Future<AiReview> aiReview(String runId, {bool refresh = false});
  Future<AiModelReview> aiModelReview(String datasetId);
  Future<AiOcrResult> aiOcr(
    List<int> bytes,
    String filename,
    String? accountId,
  );
  Future<AiChatReply> aiChat(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  );

  /// The chat in the background: start returns a job id, progress shows stage and timer.
  Future<String> aiChatStart(
    List<Map<String, String>> messages,
    Map<String, dynamic> context,
  );
  Future<AiChatProgress> aiChatProgress(String jobId);

  /// Webhooks: automatic collection from outside systems.
  Future<List<WebhookKind>> webhookKinds();
  Future<List<WebhookInfo>> webhooks(String accountId);
  Future<WebhookInfo> createWebhook(
    String accountId,
    String kind,
    String? label,
  );
  Future<String> rotateWebhook(String id);
  Future<void> deleteWebhook(String id);
  Future<WebhookCheck> checkWebhook(String id);

  /// Checks the format of a body the user's system would send; nothing is written.
  Future<String> validateWebhook(String id, String body, String contentType);
  Future<List<WebhookDelivery>> webhookDeliveries(String accountId);
}
