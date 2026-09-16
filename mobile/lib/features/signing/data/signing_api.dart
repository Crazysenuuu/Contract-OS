import 'package:contractos_mobile/core/network/api_client.dart';

/// Signing API surface — mirrors backend /api/v1/signing/* (spec 2.06.27).
class SigningApi {
  const SigningApi(this._client);

  final ApiClient _client;

  Future<Map<String, dynamic>> startSession(String signatureRequestId) async {
    final resp = await _client.dio.post(
      '/signing/sessions',
      data: {'signature_request_id': signatureRequestId},
    );
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getSession(String sessionId) async {
    final resp = await _client.dio.get('/signing/sessions/$sessionId');
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> exchangeToken(String token) async {
    final resp = await _client.dio
        .post('/signing/sessions/exchange', data: {'token': token});
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getDocument(String sessionId) async {
    final resp = await _client.dio
        .get('/signing/sessions/$sessionId/document');
    return resp.data as Map<String, dynamic>;
  }

  Future<void> giveConsent(String sessionId) async {
    await _client.dio.post('/signing/sessions/$sessionId/consent');
  }

  Future<Map<String, dynamic>> authenticate(String sessionId, String challengeId, String code) async {
    final resp = await _client.dio.post(
      '/signing/sessions/$sessionId/authenticate',
      data: {'challenge_id': challengeId, 'code': code},
    );
    return resp.data as Map<String, dynamic>;
  }

  Future<List<dynamic>> getFields(String sessionId) async {
    final resp = await _client.dio.get('/signing/sessions/$sessionId/fields');
    return resp.data as List<dynamic>;
  }

  Future<Map<String, dynamic>> submitSignature({
    required String sessionId,
    required String placementId,
    required String signatureType,
    required String signaturePayload,
  }) async {
    final resp = await _client.dio.post(
      '/signing/sessions/$sessionId/signature',
      data: {
        'placement_id': placementId,
        'signature_type': signatureType,
        'signature_payload': signaturePayload,
      },
    );
    return resp.data as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> declineSession(String sessionId, String reason) async {
    final resp = await _client.dio.post(
      '/signing/sessions/$sessionId/decline',
      data: {'reason': reason},
    );
    return resp.data as Map<String, dynamic>;
  }

  Future<List<dynamic>> getEvents(String sessionId) async {
    final resp = await _client.dio.get('/signing/sessions/$sessionId/events');
    return resp.data as List<dynamic>;
  }
}