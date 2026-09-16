import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/features/signing/data/signing_api.dart';
import 'package:contractos_mobile/features/signing/models/signature_field.dart';
import 'package:contractos_mobile/features/signing/models/signing_event.dart';
import 'package:contractos_mobile/features/signing/models/signing_session.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// High-level signing data source used by the UI layer (spec 2.06.27).
class SigningRepository {
  const SigningRepository(this._api);

  final SigningApi _api;

  /// Start a signing session for a signature request.
  Future<SigningSession> startSession(String signatureRequestId) async {
    return SigningSession.fromJson(
      await _api.startSession(signatureRequestId),
    );
  }

  Future<SigningSession> exchange(String token) async =>
      SigningSession.fromJson(await _api.exchangeToken(token));

  Future<List<SignatureField>> getFields(String sessionId) async {
    final data = await _api.getFields(sessionId);
    return [
      for (final f in data) SignatureField.fromJson(f as Map<String, dynamic>)
    ];
  }

  Future<List<SigningEvent>> getEvents(String sessionId) async {
    final data = await _api.getEvents(sessionId);
    return SigningEvent.listFromJson(data);
  }
}

final signingRepositoryProvider = Provider<SigningRepository>((ref) {
  return SigningRepository(SigningApi(ref.watch(apiClientProvider)));
});