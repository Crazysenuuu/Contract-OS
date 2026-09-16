import 'package:contractos_mobile/core/network/api_client.dart';

/// Repository API client — consumes the backend /api/v1/agreements/{id}
/// repository, timeline, and evidence endpoints (spec 2.07.39).
class RepositoryApi {
  const RepositoryApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> repository(String agreementId) async {
    final resp =
        await _client.dio.get('/agreements/$agreementId/repository');
    return resp.data as Map<String, dynamic>;
  }

  Future<List<dynamic>> documents(String agreementId) async {
    final resp = await _client.dio.get('/agreements/$agreementId/documents');
    return resp.data as List<dynamic>;
  }

  Future<List<dynamic>> timeline(String agreementId) async {
    final resp = await _client.dio.get('/agreements/$agreementId/timeline');
    return resp.data as List<dynamic>;
  }

  Future<List<dynamic>> evidence(String agreementId) async {
    final resp = await _client.dio.get('/agreements/$agreementId/evidence');
    return resp.data as List<dynamic>;
  }

  Future<Map<String, dynamic>> download(String agreementId, String documentId) async {
    final resp = await _client.dio
        .get('/agreements/$agreementId/documents/$documentId/download');
    return resp.data as Map<String, dynamic>;
  }
}