import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/errors.dart';
import '../api/fixture_api.dart';
import '../api/http_api.dart';
import '../api/kassandra_api.dart';
import '../api/models.dart';
import '../api/token_store.dart';
import '../api/v11.dart';
import 'config.dart';

final tokenStoreProvider = Provider<TokenStore>((ref) => MemoryTokenStore());

final dioProvider = Provider<Dio>((ref) {
  final base = kApiBaseUrl.endsWith('/')
      ? kApiBaseUrl.substring(0, kApiBaseUrl.length - 1)
      : kApiBaseUrl;
  final dio = Dio(
    BaseOptions(
      baseUrl: base,
      connectTimeout: const Duration(seconds: 20),
      receiveTimeout: const Duration(seconds: 90),
      headers: const {'Accept': 'application/json'},
    ),
  );
  dio.interceptors.add(
    InterceptorsWrapper(
      onRequest: (options, handler) {
        final token = ref.read(tokenStoreProvider).token;
        if (token != null && token.isNotEmpty) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
    ),
  );
  ref.onDispose(dio.close);
  return dio;
});

final apiProvider = Provider<KassandraApi>((ref) {
  if (kUseFixtures) return FixtureApi();
  return HttpApi(ref.watch(dioProvider));
});

class IdempotencyKeys {
  final Map<String, String> _keys = {};
  String get(String scope) => _keys.putIfAbsent(scope, newId);
  void rotate(String scope) => _keys[scope] = newId();
}

final idempotencyProvider = Provider<IdempotencyKeys>(
  (ref) => IdempotencyKeys(),
);

final noticeProvider = StateProvider<String?>((ref) => null);

/// Phone «+» in the shell. Hidden while a calendar sheet covers the list.
final phoneAddVisibleProvider = StateProvider<bool>((ref) => true);

/// Set before refreshing a deleted registry payment. Cleared when the toast is shown.
class RemovedPaymentHint {
  const RemovedPaymentHint({
    required this.amountMinor,
    required this.categoryLabel,
  });

  final int amountMinor;
  final String categoryLabel;
}

final removedPaymentHintProvider = StateProvider<RemovedPaymentHint?>(
  (ref) => null,
);
final needsLoginProvider = StateProvider<bool>((ref) => false);

class SessionState {
  const SessionState({
    this.accountId,
    this.asOf,
    this.themeMode = ThemeMode.system,
    // X10 ensemble: the honest best on trajectory and minimum (docs/presentation_metrics.md).
    this.model = 'ensemble',
    this.generation = 0,
    this.refreshOnly = false,
  });

  final String? accountId;
  final DateTime? asOf;
  final ThemeMode themeMode;
  final String model;
  final int generation;
  final bool refreshOnly;

  SessionState copyWith({
    String? accountId,
    DateTime? asOf,
    ThemeMode? themeMode,
    String? model,
    int? generation,
    bool? refreshOnly,
  }) {
    return SessionState(
      accountId: accountId ?? this.accountId,
      asOf: asOf ?? this.asOf,
      themeMode: themeMode ?? this.themeMode,
      model: model ?? this.model,
      generation: generation ?? this.generation,
      refreshOnly: refreshOnly ?? this.refreshOnly,
    );
  }
}

class SessionNotifier extends Notifier<SessionState> {
  @override
  SessionState build() => const SessionState();

  void applyAccount(Account account, {DateTime? asOf}) {
    final date = dateOnly(
      asOf ?? account.lastDate ?? DateTime.utc(2026, 4, 26),
    );
    state = state.copyWith(
      accountId: account.id,
      asOf: date,
      refreshOnly: false,
    );
  }

  void setAccountId(String id) => state = state.copyWith(accountId: id);

  void setAsOf(DateTime date) => state = state.copyWith(
    asOf: dateOnly(date),
    refreshOnly: false,
    generation: state.generation + 1,
  );

  void setTheme(ThemeMode mode) => state = state.copyWith(themeMode: mode);

  void setModel(String model) => state = state.copyWith(
    model: model,
    generation: state.generation + 1,
    refreshOnly: false,
  );

  void refresh() => state = state.copyWith(
    generation: state.generation + 1,
    refreshOnly: true,
  );
}

final sessionProvider = NotifierProvider<SessionNotifier, SessionState>(
  SessionNotifier.new,
);

class SavedScenarios extends Notifier<List<String>> {
  @override
  List<String> build() => const [];

  void add(String id) {
    if (state.contains(id)) return;
    state = [...state, id];
  }
}

final savedScenariosProvider = NotifierProvider<SavedScenarios, List<String>>(
  SavedScenarios.new,
);

class ForecastBundle {
  const ForecastBundle({
    required this.forecast,
    required this.explanation,
    required this.clients,
    required this.daily,
  });

  final ForecastResult forecast;
  final GapExplanation explanation;
  final List<TopClient> clients;
  final List<DailyPoint> daily;
}

final accountsProvider = FutureProvider<List<Account>>((ref) {
  return ref.watch(apiProvider).accounts();
});

final dictionariesProvider = FutureProvider<Dictionaries>((ref) {
  return ref.watch(apiProvider).dictionaries();
});

final counterpartyRisksProvider = FutureProvider<List<CounterpartyRisk>>((
  ref,
) async {
  final session = ref.watch(sessionProvider);
  final accountId = session.accountId;
  final asOf = session.asOf;
  if (accountId == null || asOf == null) return const [];
  return ref.watch(apiProvider).counterpartyRisks(accountId, asOf);
});

final lastRunIdProvider = StateProvider<String?>((ref) => null);
final pendingScenarioProvider = StateProvider<Map<String, dynamic>?>(
  (ref) => null,
);

String labelForSource(Dictionaries? dictionaries, String code) {
  if (dictionaries != null) {
    for (final item in dictionaries.sources) {
      if (item.code == code) return item.label;
    }
    return 'Другой источник';
  }
  return sourceLabel(code);
}

final forecastBundleProvider = FutureProvider<ForecastBundle>((ref) async {
  final session = ref.watch(sessionProvider);
  final accountId = session.accountId;
  final asOf = session.asOf;
  if (accountId == null || asOf == null) {
    throw ApiFailure('Выберите счёт и дату расчёта');
  }
  final api = ref.watch(apiProvider);
  final keys = ref.read(idempotencyProvider);
  final scope =
      'forecast:$accountId:${isoDate(asOf)}:${session.model}:${session.generation}';

  Future<ForecastBundle> load(String key) async {
    final previous = ref.read(lastRunIdProvider);
    late final ForecastResult forecast;
    if (session.refreshOnly && previous != null) {
      final refreshed = await api.refreshForecast(previous);
      forecast = refreshed.forecast;
      final hint = ref.read(removedPaymentHintProvider);
      ref.read(removedPaymentHintProvider.notifier).state = null;
      ref.read(noticeProvider.notifier).state = refreshNotice(
        refreshed.changes,
        removedMinor: hint?.amountMinor,
        categoryLabel: hint?.categoryLabel,
        registered: hint != null,
      );
    } else {
      forecast = await api.createForecast(
        ForecastRequest(accountId: accountId, asOf: asOf, model: session.model),
        idempotencyKey: key,
      );
    }
    ref.read(lastRunIdProvider.notifier).state = forecast.runId;
    final explanation = await api.forecastExplanation(forecast.runId);
    final daily = await api.daily(accountId);
    final clients = await api.topClients(accountId, asOf);
    return ForecastBundle(
      forecast: forecast,
      explanation: explanation,
      clients: clients,
      daily: daily,
    );
  }

  try {
    return await load(keys.get(scope));
  } on ConflictException {
    keys.rotate(scope);
    ref.read(noticeProvider.notifier).state =
        'Данные обновились, прогноз пересчитан';
    return load(keys.get(scope));
  } on UnauthorizedException {
    ref.read(needsLoginProvider.notifier).state = true;
    rethrow;
  }
});

String sectionQuery(SessionState session) {
  if (session.accountId == null || session.asOf == null) return '';
  return '?account=${session.accountId}&as_of=${isoDate(session.asOf!)}';
}
