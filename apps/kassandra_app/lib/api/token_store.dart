import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../app/config.dart';

abstract class TokenStore {
  String? get token;
  Future<void> save(String value);
  Future<void> clear();
}

class MemoryTokenStore implements TokenStore {
  @override
  String? token;

  @override
  Future<void> clear() async => token = null;

  @override
  Future<void> save(String value) async => token = value;
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore(this._storage);
  final FlutterSecureStorage _storage;
  @override
  String? token;

  static const _key = 'kassandra.token';

  Future<void> load() async {
    token = await _storage.read(key: _key);
  }

  @override
  Future<void> clear() async {
    token = null;
    await _storage.delete(key: _key);
  }

  @override
  Future<void> save(String value) async {
    token = value;
    await _storage.write(key: _key, value: value);
  }
}

/// Web keeps the token in the tab only. Desktop and mobile use the keystore
/// and fall back to memory when it is unavailable.
Future<TokenStore> openTokenStore() async {
  if (kUseFixtures || kIsWeb) return MemoryTokenStore();
  try {
    final store = SecureTokenStore(const FlutterSecureStorage());
    await store.load();
    return store;
  } catch (error) {
    kLog('Хранилище токена недоступно, токен останется в памяти сессии');
    return MemoryTokenStore();
  }
}
