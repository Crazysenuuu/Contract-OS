import 'package:sqflite/sqflite.dart';

import 'offline_cache.dart';

/// sqflite-backed [CacheStore] (spec 2.02 §34): the production persistence
/// for the offline cache. Bound from main() via [offlineCacheStoreProvider].
class SqfliteCacheStore implements CacheStore {
  SqfliteCacheStore._(this._db);

  final Database _db;
  static const _schemaVersion = 1;

  static Future<SqfliteCacheStore> open() async {
    final dir = await getDatabasesPath();
    final db = await openDatabase(
      '$dir/contractos_cache.db',
      version: _schemaVersion,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE contract_summaries (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE obligation_lists (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            fetched_at TEXT NOT NULL
          )
        ''');
      },
    );
    return SqfliteCacheStore._(db);
  }

  @override
  Future<void> upsert(String table, String key, String payload) async {
    await _db.insert(
      table,
      {
        'cache_key': key,
        'payload': payload,
        'fetched_at': DateTime.now().toUtc().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  @override
  Future<String?> get(String table, String key) async {
    final rows = await _db.query(
      table,
      columns: ['payload'],
      where: 'cache_key = ?',
      whereArgs: [key],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return rows.first['payload'] as String?;
  }
}
