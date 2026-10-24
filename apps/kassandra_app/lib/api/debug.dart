import 'models.dart';

/// Debug datasets: `/api/v1/debug/...`, a hackathon-format upload under its own dbgN- ids.
class DebugRowError {
  const DebugRowError({required this.line, required this.reason});

  factory DebugRowError.fromJson(Map<String, dynamic> json) => DebugRowError(
    line: asInt(json['line']),
    reason: (json['reason'] ?? '').toString(),
  );

  final int line;
  final String reason;
}

class DebugQuality {
  const DebugQuality({
    required this.inconsistentTransitions,
    required this.materialTransitions,
    required this.suspiciousDuplicateGroups,
    required this.emptyPurpose,
    required this.emptyInn,
    required this.invalidDays,
  });

  factory DebugQuality.fromJson(Map<String, dynamic> json) => DebugQuality(
    inconsistentTransitions: asInt(json['inconsistent_transitions']),
    materialTransitions: asInt(json['material_transitions']),
    suspiciousDuplicateGroups: asInt(json['suspicious_duplicate_groups']),
    emptyPurpose: asInt(json['empty_purpose']),
    emptyInn: asInt(json['empty_inn']),
    invalidDays: asInt(json['invalid_days']),
  );

  final int inconsistentTransitions;
  final int materialTransitions;
  final int suspiciousDuplicateGroups;
  final int emptyPurpose;
  final int emptyInn;
  final int invalidDays;
}

class DebugCategory {
  const DebugCategory({
    required this.label,
    required this.operations,
    required this.amountMinor,
  });

  factory DebugCategory.fromJson(Map<String, dynamic> json) => DebugCategory(
    label: (json['label'] ?? json['code'] ?? '').toString(),
    operations: asInt(json['operations']),
    amountMinor: asInt(json['amount_minor']),
  );

  final String label;
  final int operations;
  final int amountMinor;
}

class DebugAccount {
  const DebugAccount({
    required this.accountId,
    required this.companyId,
    required this.name,
    required this.operations,
    required this.firstDate,
    required this.lastDate,
    required this.lastBalanceMinor,
    required this.inconsistentTransitions,
    required this.suspiciousDuplicates,
    required this.invalidDays,
    required this.unknownShare,
  });

  factory DebugAccount.fromJson(Map<String, dynamic> json) => DebugAccount(
    accountId: json['account_id'].toString(),
    companyId: (json['company_id'] ?? '').toString(),
    name: (json['name'] ?? '').toString(),
    operations: asInt(json['operations']),
    firstDate: asDate(json['first_date']),
    lastDate: asDate(json['last_date']),
    lastBalanceMinor: asInt(json['last_balance_minor']),
    inconsistentTransitions: asInt(json['inconsistent_transitions']),
    suspiciousDuplicates: asInt(json['suspicious_duplicates']),
    invalidDays: asInt(json['invalid_days']),
    unknownShare: asDoubleOpt(json['unknown_share']) ?? 0,
  );

  final String accountId;
  final String companyId;
  final String name;
  final int operations;
  final DateTime firstDate;
  final DateTime lastDate;
  final int lastBalanceMinor;
  final int inconsistentTransitions;
  final int suspiciousDuplicates;
  final int invalidDays;
  final double unknownShare;
}

class DebugDataset {
  const DebugDataset({
    required this.id,
    required this.filename,
    required this.source,
    required this.sizeBytes,
    required this.rows,
    required this.accepted,
    required this.rejected,
    required this.errors,
    required this.inserted,
    required this.duplicates,
    required this.companies,
    required this.firstDate,
    required this.lastDate,
    required this.quality,
    required this.unknownOperations,
    required this.unknownShare,
    required this.topCategories,
    required this.accounts,
    this.notes = const [],
    this.historyCompanies = 0,
    this.historyOperations = 0,
  });

  factory DebugDataset.fromJson(Map<String, dynamic> json) {
    final categories = json['categories'] as Map<String, dynamic>? ?? const {};
    return DebugDataset(
      id: json['id'].toString(),
      filename: (json['filename'] ?? '').toString(),
      source: (json['source'] ?? '').toString(),
      sizeBytes: asInt(json['size_bytes']),
      rows: asInt(json['rows']),
      accepted: asInt(json['accepted']),
      rejected: asInt(json['rejected']),
      errors: [
        for (final item in (json['errors'] as List? ?? const []))
          DebugRowError.fromJson(item as Map<String, dynamic>),
      ],
      inserted: asInt(json['inserted']),
      duplicates: asInt(json['duplicates']),
      companies: asInt(json['companies']),
      firstDate: asDate(json['first_date']),
      lastDate: asDate(json['last_date']),
      quality: DebugQuality.fromJson(
        json['quality'] as Map<String, dynamic>? ?? const {},
      ),
      unknownOperations: asInt(categories['unknown_operations']),
      unknownShare: asDoubleOpt(categories['unknown_share']) ?? 0,
      topCategories: [
        for (final item in (categories['top'] as List? ?? const []))
          DebugCategory.fromJson(item as Map<String, dynamic>),
      ],
      accounts: [
        for (final item in (json['accounts'] as List? ?? const []))
          DebugAccount.fromJson(item as Map<String, dynamic>),
      ],
      notes: [for (final note in (json['notes'] as List? ?? const [])) '$note'],
      historyCompanies: asInt(json['history_companies']),
      historyOperations: asInt(json['history_operations']),
    );
  }

  final String id;
  final String filename;
  final String source;
  final int sizeBytes;
  final int rows;
  final int accepted;
  final int rejected;
  final List<DebugRowError> errors;
  final int inserted;
  final int duplicates;
  final int companies;
  final DateTime firstDate;
  final DateTime lastDate;
  final DebugQuality quality;
  final int unknownOperations;
  final double unknownShare;
  final List<DebugCategory> topCategories;

  /// Empty in the list response: the companies come with the full report.
  final List<DebugAccount> accounts;

  /// How the file was read: encoding, delimiter, renamed columns.
  final List<String> notes;

  /// Companies whose earlier history was copied from the main dataset.
  final int historyCompanies;
  final int historyOperations;
}

class DebugBacktest {
  const DebugBacktest({
    required this.asOf,
    required this.days,
    required this.maeMinor,
    required this.maeConstantMinor,
    required this.skill,
    required this.forecastMinimumMinor,
    required this.actualMinimumMinor,
    required this.forecastGapDate,
    required this.actualGapDate,
  });

  factory DebugBacktest.fromJson(Map<String, dynamic> json) => DebugBacktest(
    asOf: asDate(json['as_of']),
    days: asInt(json['days']),
    maeMinor: asInt(json['mae_minor']),
    maeConstantMinor: asInt(json['mae_constant_minor']),
    skill: asDoubleOpt(json['skill']),
    forecastMinimumMinor: asInt(json['forecast_minimum_minor']),
    actualMinimumMinor: asInt(json['actual_minimum_minor']),
    forecastGapDate: asDateOpt(json['forecast_gap_date']),
    actualGapDate: asDateOpt(json['actual_gap_date']),
  );

  final DateTime asOf;
  final int days;
  final int maeMinor;
  final int maeConstantMinor;
  final double? skill;
  final int forecastMinimumMinor;
  final int actualMinimumMinor;
  final DateTime? forecastGapDate;
  final DateTime? actualGapDate;
}

class DebugCheck {
  const DebugCheck({
    required this.accountId,
    required this.asOf,
    required this.model,
    required this.gapDate,
    required this.needMinor,
    required this.minimumMinor,
    required this.probability,
    required this.backtest,
    required this.backtestNote,
  });

  factory DebugCheck.fromJson(Map<String, dynamic> json) {
    final forecast = json['forecast'] as Map<String, dynamic>? ?? const {};
    final backtest = json['backtest'];
    return DebugCheck(
      accountId: json['account_id'].toString(),
      asOf: asDate(json['as_of']),
      model: (json['model'] ?? 'hgb').toString(),
      gapDate: asDateOpt(forecast['first_gap_date']),
      needMinor: asInt(forecast['liquidity_need_minor']),
      minimumMinor: asInt(forecast['minimum_balance_minor']),
      probability: asDoubleOpt(forecast['probability_30d']),
      backtest: backtest is Map<String, dynamic>
          ? DebugBacktest.fromJson(backtest)
          : null,
      backtestNote: json['backtest_note']?.toString(),
    );
  }

  final String accountId;
  final DateTime asOf;

  /// `hgb`, or `weekday` when the account ends before hgb's training cutoff.
  final String model;
  final DateTime? gapDate;
  final int needMinor;
  final int minimumMinor;
  final double? probability;
  final DebugBacktest? backtest;
  final String? backtestNote;
}

/// The whole-dataset check: the step log, forecasts on the last day and the honest backtest
/// with balance, gap and probability metrics (`/debug/datasets/{id}/evaluation`).
class EvaluationState {
  const EvaluationState({
    required this.datasetId,
    required this.status,
    required this.stage,
    required this.done,
    required this.total,
    required this.elapsed,
    required this.log,
    this.result,
    this.error,
  });

  factory EvaluationState.fromJson(Map<String, dynamic> json) {
    final result = json['result'];
    return EvaluationState(
      datasetId: json['dataset_id'].toString(),
      status: (json['status'] ?? 'none').toString(),
      stage: (json['stage'] ?? '').toString(),
      done: asInt(json['done']),
      total: asInt(json['total']),
      elapsed: asDoubleOpt(json['elapsed']) ?? 0,
      log: [for (final line in (json['log'] as List? ?? const [])) '$line'],
      result: result is Map<String, dynamic>
          ? EvaluationResult.fromJson(result)
          : null,
      error: json['error']?.toString(),
    );
  }

  final String datasetId;

  /// none, running, done or error.
  final String status;
  final String stage;
  final int done;
  final int total;
  final double elapsed;
  final List<String> log;
  final EvaluationResult? result;
  final String? error;

  bool get running => status == 'running';
}

class HorizonError {
  const HorizonError({
    required this.day,
    required this.maeMinor,
    required this.constantMaeMinor,
  });

  final int day;
  final int maeMinor;
  final int constantMaeMinor;
}

class BalanceMetrics {
  const BalanceMetrics({
    required this.points,
    required this.windows,
    required this.companies,
    required this.maeMinor,
    required this.mse,
    required this.rmseMinor,
    required this.wape,
    required this.biasMinor,
    required this.constantMaeMinor,
    required this.constantRmseMinor,
    required this.constantMse,
    required this.skillMae,
    required this.skillMse,
    required this.companiesBetter,
    required this.minimumMaeMinor,
    required this.minimumConstantMaeMinor,
    required this.byHorizon,
  });

  factory BalanceMetrics.fromJson(Map<String, dynamic> json) => BalanceMetrics(
    points: asInt(json['points']),
    windows: asInt(json['windows']),
    companies: asInt(json['companies']),
    maeMinor: asInt(json['mae_minor']),
    mse: asDoubleOpt(json['mse']),
    rmseMinor: asInt(json['rmse_minor']),
    wape: asDoubleOpt(json['wape']),
    biasMinor: asInt(json['bias_minor']),
    constantMaeMinor: asInt(json['constant_mae_minor']),
    constantRmseMinor: asInt(json['constant_rmse_minor']),
    constantMse: asDoubleOpt(json['constant_mse']),
    skillMae: asDoubleOpt(json['skill_mae']),
    skillMse: asDoubleOpt(json['skill_mse']),
    companiesBetter: asInt(json['companies_better']),
    minimumMaeMinor: asInt(json['minimum_mae_minor']),
    minimumConstantMaeMinor: asInt(json['minimum_constant_mae_minor']),
    byHorizon: [
      for (final item in (json['by_horizon'] as List? ?? const []))
        HorizonError(
          day: asInt((item as Map)['day']),
          maeMinor: asInt(item['mae_minor']),
          constantMaeMinor: asInt(item['constant_mae_minor']),
        ),
    ],
  );

  final int points;
  final int windows;
  final int companies;
  final int maeMinor;

  /// In kopecks squared: shown in rubles squared.
  final double? mse;
  final int rmseMinor;
  final double? wape;
  final int biasMinor;
  final int constantMaeMinor;
  final int constantRmseMinor;
  final double? constantMse;
  final double? skillMae;
  final double? skillMse;
  final int companiesBetter;
  final int minimumMaeMinor;
  final int minimumConstantMaeMinor;
  final List<HorizonError> byHorizon;
}

class GapMetrics {
  const GapMetrics({
    required this.windows,
    required this.skippedNegativeStart,
    required this.tp,
    required this.fp,
    required this.fn,
    required this.tn,
    required this.accuracy,
    required this.precision,
    required this.recall,
    required this.f1,
    required this.specificity,
    required this.dateWithin3Days,
    required this.dateMaeDays,
    required this.leadDaysMedian,
  });

  factory GapMetrics.fromJson(Map<String, dynamic> json) {
    final c = json['confusion'] as Map<String, dynamic>? ?? const {};
    return GapMetrics(
      windows: asInt(json['windows']),
      skippedNegativeStart: asInt(json['skipped_negative_start']),
      tp: asInt(c['tp']),
      fp: asInt(c['fp']),
      fn: asInt(c['fn']),
      tn: asInt(c['tn']),
      accuracy: asDoubleOpt(json['accuracy']),
      precision: asDoubleOpt(json['precision']),
      recall: asDoubleOpt(json['recall']),
      f1: asDoubleOpt(json['f1']),
      specificity: asDoubleOpt(json['specificity']),
      dateWithin3Days: asDoubleOpt(json['date_within_3_days']),
      dateMaeDays: asDoubleOpt(json['date_mae_days']),
      leadDaysMedian: json['lead_days_median'] == null
          ? null
          : asInt(json['lead_days_median']),
    );
  }

  final int windows;
  final int skippedNegativeStart;
  final int tp;
  final int fp;
  final int fn;
  final int tn;
  final double? accuracy;
  final double? precision;
  final double? recall;
  final double? f1;
  final double? specificity;
  final double? dateWithin3Days;
  final double? dateMaeDays;
  final int? leadDaysMedian;
}

class CalibrationBin {
  const CalibrationBin({
    required this.range,
    required this.windows,
    required this.predicted,
    required this.observed,
  });

  final String range;
  final int windows;
  final double predicted;
  final double observed;
}

class ProbabilityMetrics {
  const ProbabilityMetrics({
    required this.windows,
    required this.calibratedWindows,
    required this.baseRate,
    required this.meanProbability,
    required this.brier,
    required this.brierSkill,
    required this.rocAuc,
    required this.calibration,
  });

  factory ProbabilityMetrics.fromJson(Map<String, dynamic> json) =>
      ProbabilityMetrics(
        windows: asInt(json['windows']),
        calibratedWindows: asInt(json['calibrated_windows']),
        baseRate: asDoubleOpt(json['base_rate']),
        meanProbability: asDoubleOpt(json['mean_probability']),
        brier: asDoubleOpt(json['brier']),
        brierSkill: asDoubleOpt(json['brier_skill']),
        rocAuc: asDoubleOpt(json['roc_auc']),
        calibration: [
          for (final item in (json['calibration'] as List? ?? const []))
            CalibrationBin(
              range: '${(item as Map)['range']}',
              windows: asInt(item['windows']),
              predicted: asDoubleOpt(item['predicted']) ?? 0,
              observed: asDoubleOpt(item['observed']) ?? 0,
            ),
        ],
      );

  final int windows;
  final int calibratedWindows;
  final double? baseRate;
  final double? meanProbability;
  final double? brier;
  final double? brierSkill;
  final double? rocAuc;
  final List<CalibrationBin> calibration;
}

/// One company of the result: the forecast on its last day and its backtest error.
class EvaluationRow {
  const EvaluationRow({
    required this.accountId,
    required this.runId,
    required this.model,
    required this.asOf,
    required this.gapDate,
    required this.needMinor,
    required this.probability,
    required this.minimumMinor,
    required this.windows,
    required this.maeMinor,
    required this.constantMaeMinor,
    required this.skill,
    this.closed = false,
  });

  factory EvaluationRow.fromJson(Map<String, dynamic> json) => EvaluationRow(
    accountId: json['account_id'].toString(),
    runId: (json['run_id'] ?? '').toString(),
    model: (json['model'] ?? '').toString(),
    asOf: asDate(json['as_of']),
    gapDate: asDateOpt(json['gap_date']),
    needMinor: asInt(json['need_minor']),
    probability: asDoubleOpt(json['probability']),
    minimumMinor: asInt(json['minimum_minor']),
    windows: asInt(json['windows']),
    maeMinor: json['mae_minor'] == null ? null : asInt(json['mae_minor']),
    constantMaeMinor: json['constant_mae_minor'] == null
        ? null
        : asInt(json['constant_mae_minor']),
    skill: asDoubleOpt(json['skill']),
    closed: asBool(json['closed']),
  );

  final String accountId;
  final String runId;
  final String model;
  final DateTime asOf;
  final DateTime? gapDate;
  final int needMinor;
  final double? probability;
  final int minimumMinor;
  final int windows;
  final int? maeMinor;
  final int? constantMaeMinor;
  final double? skill;

  /// The account stopped at zero well before the set ends: no gap ahead, it was closed.
  final bool closed;
}

class EvaluationResult {
  const EvaluationResult({
    required this.computedAt,
    required this.balance,
    required this.gaps,
    required this.trajectory,
    required this.probability,
    required this.models,
    required this.companies,
    required this.closed,
    required this.withGap,
    required this.needMinor,
    required this.rows,
    required this.categorizationAccuracy,
    required this.categorizationF1,
    required this.categorizationLabelled,
  });

  factory EvaluationResult.fromJson(Map<String, dynamic> json) {
    final metrics = json['metrics'] as Map<String, dynamic>? ?? const {};
    final current = json['current'] as Map<String, dynamic>? ?? const {};
    final categorization =
        json['categorization'] as Map<String, dynamic>? ?? const {};
    final models = json['models'] as Map<String, dynamic>? ?? const {};
    return EvaluationResult(
      computedAt: DateTime.tryParse((json['computed_at'] ?? '').toString()),
      balance: BalanceMetrics.fromJson(
        metrics['balance'] as Map<String, dynamic>? ?? const {},
      ),
      gaps: GapMetrics.fromJson(
        metrics['gaps'] as Map<String, dynamic>? ?? const {},
      ),
      trajectory: GapMetrics.fromJson(
        (metrics['gaps_trajectory'] ?? metrics['gaps'])
                as Map<String, dynamic>? ??
            const {},
      ),
      probability: ProbabilityMetrics.fromJson(
        metrics['probability'] as Map<String, dynamic>? ?? const {},
      ),
      models: {
        for (final entry in models.entries) entry.key: asInt(entry.value),
      },
      companies: asInt(current['companies']),
      closed: asInt(current['closed']),
      withGap: asInt(current['with_gap']),
      needMinor: asInt(current['need_minor']),
      rows: [
        for (final item in (json['companies'] as List? ?? const []))
          EvaluationRow.fromJson(item as Map<String, dynamic>),
      ],
      categorizationAccuracy: asDoubleOpt(categorization['accuracy']),
      categorizationF1: asDoubleOpt(categorization['macro_f1']),
      categorizationLabelled: asInt(categorization['labelled']),
    );
  }

  final DateTime? computedAt;
  final BalanceMetrics balance;

  /// The main alarm rule: calibrated probability from the X11 threshold.
  final GapMetrics gaps;

  /// The same windows with the plain rule «the forecast goes below zero».
  final GapMetrics trajectory;
  final ProbabilityMetrics probability;
  final Map<String, int> models;
  final int companies;
  final int closed;
  final int withGap;
  final int needMinor;
  final List<EvaluationRow> rows;
  final double? categorizationAccuracy;
  final double? categorizationF1;
  final int categorizationLabelled;
}

/// A backtest window: the forecast from [origin] against what followed.
class EvaluationWindow {
  const EvaluationWindow({
    required this.origin,
    required this.model,
    required this.startMinor,
    required this.forecast,
    required this.p10,
    required this.p90,
    required this.actual,
    required this.observed,
    required this.financingMinor,
    required this.actualGapDay,
    required this.predictedGapDay,
    required this.probability,
  });

  factory EvaluationWindow.fromJson(Map<String, dynamic> json) {
    List<int?> values(String key) => [
      for (final v in (json[key] as List? ?? const []))
        v == null ? null : asInt(v),
    ];
    return EvaluationWindow(
      origin: asDate(json['origin']),
      model: (json['model'] ?? '').toString(),
      startMinor: asInt(json['start_balance']),
      forecast: [for (final v in values('forecast')) v ?? 0],
      p10: values('p10'),
      p90: values('p90'),
      actual: values('actual'),
      observed: values('observed'),
      financingMinor: asInt(json['financing_minor']),
      actualGapDay: json['actual_gap_day'] == null
          ? null
          : asInt(json['actual_gap_day']),
      predictedGapDay: json['predicted_gap_day'] == null
          ? null
          : asInt(json['predicted_gap_day']),
      probability: asDoubleOpt(json['probability']),
    );
  }

  final DateTime origin;
  final String model;
  final int startMinor;
  final List<int> forecast;
  final List<int?> p10;
  final List<int?> p90;

  /// The balance without new financing after [origin] (protocol D3); null on masked days.
  final List<int?> actual;

  /// The balance as it was in the statement.
  final List<int?> observed;
  final int financingMinor;
  final int? actualGapDay;
  final int? predictedGapDay;
  final double? probability;
}

class TraceStep {
  const TraceStep({
    required this.n,
    required this.title,
    required this.detail,
    required this.items,
  });

  factory TraceStep.fromJson(Map<String, dynamic> json) => TraceStep(
    n: asInt(json['n']),
    title: (json['title'] ?? '').toString(),
    detail: (json['detail'] ?? '').toString(),
    items: [for (final item in (json['items'] as List? ?? const [])) '$item'],
  );

  final int n;
  final String title;
  final String detail;
  final List<String> items;
}

class EvaluationCompany {
  const EvaluationCompany({
    required this.accountId,
    required this.current,
    required this.history,
    required this.forecast,
    required this.windows,
    required this.trace,
  });

  factory EvaluationCompany.fromJson(
    Map<String, dynamic> json,
  ) => EvaluationCompany(
    accountId: json['account_id'].toString(),
    current: EvaluationRow.fromJson(
      json['current'] as Map<String, dynamic>? ?? const {},
    ),
    history: [
      for (final item in (json['history'] as List? ?? const []))
        HistoryPoint(
          date: asDate((item as Map)['date']),
          balanceMinor: asInt(item['balance_minor']),
        ),
    ],
    forecast: [
      for (final item in (json['forecast'] as List? ?? const []))
        ForecastBand(
          date: asDate((item as Map)['date']),
          balanceMinor: asInt(item['balance_minor']),
          p10Minor: item['p10_minor'] == null ? null : asInt(item['p10_minor']),
          p90Minor: item['p90_minor'] == null ? null : asInt(item['p90_minor']),
        ),
    ],
    windows: [
      for (final item in (json['windows'] as List? ?? const []))
        EvaluationWindow.fromJson(item as Map<String, dynamic>),
    ],
    trace: [
      for (final item in (json['trace'] as List? ?? const []))
        TraceStep.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String accountId;
  final EvaluationRow current;
  final List<HistoryPoint> history;
  final List<ForecastBand> forecast;
  final List<EvaluationWindow> windows;
  final List<TraceStep> trace;
}

class ForecastBand {
  const ForecastBand({
    required this.date,
    required this.balanceMinor,
    this.p10Minor,
    this.p90Minor,
  });

  final DateTime date;
  final int balanceMinor;
  final int? p10Minor;
  final int? p90Minor;
}
