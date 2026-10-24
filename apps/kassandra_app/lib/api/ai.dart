import 'models.dart';

/// The LLM on top of the forecast: `/api/v1/ai/...` (review, model review, OCR, chat).
class AiCheck {
  const AiCheck({
    required this.title,
    required this.status,
    required this.detail,
  });

  factory AiCheck.fromJson(Map<String, dynamic> json) => AiCheck(
    title: (json['title'] ?? '').toString(),
    status: (json['status'] ?? 'warning').toString(),
    detail: (json['detail'] ?? '').toString(),
  );

  final String title;

  /// ok, warning or problem.
  final String status;
  final String detail;
}

class AiRecommendation {
  const AiRecommendation({
    required this.title,
    required this.detail,
    required this.priority,
    this.deadline,
    this.amountMinor,
  });

  factory AiRecommendation.fromJson(Map<String, dynamic> json) =>
      AiRecommendation(
        title: (json['title'] ?? '').toString(),
        detail: (json['detail'] ?? '').toString(),
        priority: (json['priority'] ?? 'medium').toString(),
        deadline: asDateOpt(json['deadline']),
        amountMinor: json['amount_minor'] == null
            ? null
            : asInt(json['amount_minor']),
      );

  final String title;
  final String detail;
  final String priority;
  final DateTime? deadline;
  final int? amountMinor;
}

class AiNote {
  const AiNote({required this.title, required this.detail});

  factory AiNote.fromJson(Map<String, dynamic> json) => AiNote(
    title: (json['title'] ?? '').toString(),
    detail: (json['detail'] ?? '').toString(),
  );

  final String title;
  final String detail;
}

List<T> _list<T>(Object? raw, T Function(Map<String, dynamic>) parse) => [
  for (final item in (raw as List? ?? const []))
    if (item is Map<String, dynamic>) parse(item),
];

List<String> _strings(Object? raw) => [
  for (final item in (raw as List? ?? const [])) item.toString(),
];

class AiReview {
  const AiReview({
    required this.runId,
    required this.model,
    required this.verdict,
    required this.headline,
    required this.trust,
    required this.checks,
    required this.recommendations,
    required this.improvements,
    required this.backtest,
    required this.reasoning,
    required this.elapsed,
    required this.cached,
  });

  factory AiReview.fromJson(Map<String, dynamic> json) => AiReview(
    runId: json['run_id'].toString(),
    model: (json['model'] ?? '').toString(),
    verdict: (json['verdict'] ?? 'caution').toString(),
    headline: (json['headline'] ?? '').toString(),
    trust: (json['trust'] ?? '').toString(),
    checks: _list(json['checks'], AiCheck.fromJson),
    recommendations: _list(json['recommendations'], AiRecommendation.fromJson),
    improvements: _list(json['improvements'], AiNote.fromJson),
    backtest: json['backtest'] is Map<String, dynamic>
        ? json['backtest'] as Map<String, dynamic>
        : const {},
    reasoning: (json['reasoning'] ?? '').toString(),
    elapsed: asDoubleOpt(json['elapsed']) ?? 0,
    cached: asBool(json['cached']),
  );

  final String runId;
  final String model;

  /// ok, caution or weak.
  final String verdict;
  final String headline;
  final String trust;
  final List<AiCheck> checks;
  final List<AiRecommendation> recommendations;
  final List<AiNote> improvements;

  /// The same model 30 days earlier against the fact, already formatted by the server.
  final Map<String, dynamic> backtest;
  final String reasoning;
  final double elapsed;
  final bool cached;
}

class AiModelImprovement {
  const AiModelImprovement({
    required this.title,
    required this.detail,
    required this.expectedEffect,
    required this.howToValidate,
    required this.priority,
  });

  factory AiModelImprovement.fromJson(Map<String, dynamic> json) =>
      AiModelImprovement(
        title: (json['title'] ?? '').toString(),
        detail: (json['detail'] ?? '').toString(),
        expectedEffect: (json['expected_effect'] ?? '').toString(),
        howToValidate: (json['how_to_validate'] ?? '').toString(),
        priority: (json['priority'] ?? 'medium').toString(),
      );

  final String title;
  final String detail;
  final String expectedEffect;
  final String howToValidate;
  final String priority;
}

class AiModelReview {
  const AiModelReview({
    required this.verdict,
    required this.headline,
    required this.strengths,
    required this.weaknesses,
    required this.patterns,
    required this.improvements,
    required this.risks,
    required this.reasoning,
  });

  factory AiModelReview.fromJson(Map<String, dynamic> json) => AiModelReview(
    verdict: (json['verdict'] ?? 'caution').toString(),
    headline: (json['headline'] ?? '').toString(),
    strengths: _strings(json['strengths']),
    weaknesses: _strings(json['weaknesses']),
    patterns: _strings(json['patterns']),
    improvements: _list(json['improvements'], AiModelImprovement.fromJson),
    risks: _strings(json['risks']),
    reasoning: (json['reasoning'] ?? '').toString(),
  );

  final String verdict;
  final String headline;
  final List<String> strengths;
  final List<String> weaknesses;
  final List<String> patterns;
  final List<AiModelImprovement> improvements;
  final List<String> risks;
  final String reasoning;
}

class AiDraft {
  const AiDraft({
    required this.direction,
    required this.amountMinor,
    required this.date,
    required this.label,
    required this.counterpartyName,
    required this.counterpartyInn,
    required this.source,
    required this.certainty,
    required this.category,
  });

  factory AiDraft.fromJson(Map<String, dynamic> json) => AiDraft(
    direction: json['direction']?.toString(),
    amountMinor: asInt(json['amount_minor']),
    date: asDateOpt(json['date']),
    label: (json['label'] ?? '').toString(),
    counterpartyName: json['counterparty_name']?.toString(),
    counterpartyInn: json['counterparty_inn']?.toString(),
    source: (json['source'] ?? 'manual').toString(),
    certainty: (json['certainty'] ?? 'documented').toString(),
    category: (json['category'] ?? 'operating_expense').toString(),
  );

  /// in, out or null when the document does not say who pays.
  final String? direction;
  final int amountMinor;
  final DateTime? date;
  final String label;
  final String? counterpartyName;
  final String? counterpartyInn;
  final String source;
  final String certainty;
  final String category;
}

class AiOcrResult {
  const AiOcrResult({
    required this.engine,
    required this.text,
    required this.docType,
    required this.number,
    required this.confidence,
    required this.notes,
    required this.drafts,
  });

  factory AiOcrResult.fromJson(Map<String, dynamic> json) {
    final fields = json['fields'] as Map<String, dynamic>? ?? const {};
    return AiOcrResult(
      engine: (json['engine'] ?? '').toString(),
      text: (json['text'] ?? '').toString(),
      docType: (fields['doc_type'] ?? 'other').toString(),
      number: fields['number']?.toString(),
      confidence: asDoubleOpt(fields['confidence']) ?? 0,
      notes: fields['notes']?.toString(),
      drafts: _list(json['drafts'], AiDraft.fromJson),
    );
  }

  final String engine;
  final String text;
  final String docType;
  final String? number;
  final double confidence;
  final String? notes;
  final List<AiDraft> drafts;
}

class AiAction {
  const AiAction({
    required this.tool,
    required this.title,
    required this.ok,
    this.error,
  });

  factory AiAction.fromJson(Map<String, dynamic> json) => AiAction(
    tool: (json['tool'] ?? '').toString(),
    title: (json['title'] ?? '').toString(),
    ok: asBool(json['ok']),
    error: json['error']?.toString(),
  );

  final String tool;
  final String title;
  final bool ok;
  final String? error;
}

class AiChatReply {
  const AiChatReply({
    required this.reply,
    required this.actions,
    required this.ui,
    required this.reasoning,
    required this.elapsed,
  });

  factory AiChatReply.fromJson(Map<String, dynamic> json) => AiChatReply(
    reply: (json['reply'] ?? '').toString(),
    actions: _list(json['actions'], AiAction.fromJson),
    ui: [
      for (final item in (json['ui'] as List? ?? const []))
        if (item is Map<String, dynamic>) item,
    ],
    reasoning: (json['reasoning'] ?? '').toString(),
    elapsed: asDoubleOpt(json['elapsed']) ?? 0,
  );

  final String reply;
  final List<AiAction> actions;

  /// What the client does next: navigate, switch_account or refresh.
  final List<Map<String, dynamic>> ui;
  final String reasoning;
  final double elapsed;
}

/// A chat answer computed on the server in the background: stage, tools and thinking so far.
class AiChatProgress {
  const AiChatProgress({
    required this.status,
    required this.elapsed,
    required this.stage,
    required this.actions,
    required this.reasoning,
    this.result,
    this.error,
  });

  factory AiChatProgress.fromJson(Map<String, dynamic> json) => AiChatProgress(
    status: (json['status'] ?? 'running').toString(),
    elapsed: asDoubleOpt(json['elapsed']) ?? 0,
    stage: (json['stage'] ?? '').toString(),
    actions: _list(json['actions'], AiAction.fromJson),
    reasoning: _strings(json['reasoning']),
    result: json['result'] is Map<String, dynamic>
        ? AiChatReply.fromJson(json['result'] as Map<String, dynamic>)
        : null,
    error: json['error']?.toString(),
  );

  /// running, done or error.
  final String status;
  final double elapsed;
  final String stage;
  final List<AiAction> actions;
  final List<String> reasoning;
  final AiChatReply? result;
  final String? error;
}
