import 'package:flutter/foundation.dart';

/// `fixtures` (default) reads the bundled C003 slice. `http` calls the API.
const kDataMode = String.fromEnvironment(
  'KASSANDRA_DATA',
  defaultValue: 'fixtures',
);

/// No trailing slash. Web release should pass `--dart-define=API_BASE_URL=/`
/// so nginx serves the client and the API on one origin.
const kApiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://127.0.0.1:8100',
);

bool get kUseFixtures => kDataMode != 'http';

void kLog(String message) {
  if (kDebugMode) debugPrint(message);
}

/// The LLM on top of the forecast; the provider is server configuration (now Xiaomi MiMo).
const kAiName = 'MiMo';
