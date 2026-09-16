import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Offline cache for read-model summaries (spec 2.02 §34-35).
///
/// Previously-fetched contract summaries and obligation lists are persisted
/// locally so the app stays browsable when the network drops. Writes are
/// best-effort: a cache failure must never break a live data flow, so every
/// method swallows storage errors.
///
/// The store is injectable: production binds a sqflite-backed store from
/// main(); tests use [MemoryCacheStore].
abstract class CacheStore {
  Future<void> upsert(String table, String key, String payload);
  Future<String?> get(String table, String key);
}

/// In-memory store for tests and as a graceful fallback when the platform
/// database is unavailable (e.g. some desktop targets).
class MemoryCacheStore implements CacheStore {
  final _data = <String, String>{};

  @override
  Future<void> upsert(String table, String key, String payload) async {
    _data['$table/$key'] = payload;
  }

  @override
  Future<String?> get(String table, String key) async =>
      _data['$table/$key'];
}

class OfflineCache {
  OfflineCache(this._store);

  static const _contracts = 'contract_summaries';
  static const _obligations = 'obligation_lists';

  final CacheStore _store;

  Future<void> putContractSummaries(
    String cacheKey,
    List<Map<String, dynamic>> items,
  ) async {
    try {
      await _store.upsert(_contracts, cacheKey, jsonEncode(items));
    } catch (_) {
      // Cache is an optimisation — never surface storage failures.
    }
  }

  Future<List<Map<String, dynamic>>?> getContractSummaries(
    String cacheKey,
  ) async {
    final raw = await _safeGet(_contracts, cacheKey);
    if (raw == null) return null;
    try {
      return (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    } catch (_) {
      return null;
    }
  }

  Future<void> putObligations(
    String cacheKey,
    List<Map<String, dynamic>> items,
  ) async {
    try {
      await _store.upsert(_obligations, cacheKey, jsonEncode(items));
    } catch (_) {
      // Ignore.
    }
  }

  Future<List<Map<String, dynamic>>?> getObligations(String cacheKey) async {
    final raw = await _safeGet(_obligations, cacheKey);
    if (raw == null) return null;
    try {
      return (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    } catch (_) {
      return null;
    }
  }

  Future<String?> _safeGet(String table, String key) async {
    try {
      return await _store.get(table, key);
    } catch (_) {
      return null;
    }
  }

  /// Cache stamp for the contracts list, partitioned by status filter.
  static String contractsKey(String? status) => 'contracts:$status';

  /// Cache stamp for an agreement's obligation list.
  static String obligationsKey(String agreementId) =>
      'obligations:$agreementId';
}

/// Overrides with the platform store in main(); defaults to memory so the
/// app boots (and tests run) without platform channels.
final offlineCacheStoreProvider =
    Provider<CacheStore>((ref) => MemoryCacheStore());

final offlineCacheProvider = Provider<OfflineCache>((ref) {
  return OfflineCache(ref.watch(offlineCacheStoreProvider));
});
