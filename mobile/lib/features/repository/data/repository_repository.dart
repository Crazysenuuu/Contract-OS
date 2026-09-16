import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/features/repository/data/repository_api.dart';
import 'package:contractos_mobile/features/repository/models/document.dart';
import 'package:contractos_mobile/features/repository/models/repository_record.dart';
import 'package:contractos_mobile/features/repository/models/timeline_event.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class RepositoryRepository {
  const RepositoryRepository(this._api);

  final RepositoryApi _api;

  Future<List<RepositoryRecord>> getRepository(String agreementId) async {
    final data = await _api.repository(agreementId);
    final items = (data['records'] ?? data['items'] ?? []) as List<dynamic>;
    return [
      for (final r in items)
        RepositoryRecord.fromJson(r as Map<String, dynamic>)
    ];
  }

  Future<List<Document>> getDocuments(String agreementId) async {
    final data = await _api.documents(agreementId);
    return [for (final d in data) Document.fromJson(d as Map<String, dynamic>)];
  }

  Future<List<TimelineEvent>> getTimeline(String agreementId) async {
    final data = await _api.timeline(agreementId);
    return [
      for (final e in data) TimelineEvent.fromJson(e as Map<String, dynamic>)
    ];
  }
}

final repositoryRepositoryProvider = Provider<RepositoryRepository>((ref) {
  return RepositoryRepository(RepositoryApi(ref.watch(apiClientProvider)));
});