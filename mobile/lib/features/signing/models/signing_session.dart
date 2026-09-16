/// Signing session model (spec 2.06.1 / 2.06.27).
class SigningSession {
  const SigningSession({
    required this.id,
    required this.token,
    required this.status,
    required this.signatureRequestId,
    required this.agreementId,
    required this.signerName,
    required this.signerEmail,
    required this.expiresAt,
  });

  final String id;
  final String? token;
  final String status;
  final String signatureRequestId;
  final String agreementId;
  final String signerName;
  final String signerEmail;
  final DateTime? expiresAt;

  factory SigningSession.fromJson(Map<String, dynamic> json) {
    return SigningSession(
      id: json['session_id'] as String? ?? json['id'] as String? ?? '',
      token: json['one_time_token'] as String?,
      status: json['status'] as String? ?? json['session_status'] as String? ?? '',
      signatureRequestId: json['signature_request_id'] as String? ?? '',
      agreementId: json['agreement_id'] as String? ?? '',
      signerName: json['signer_name'] as String? ?? '',
      signerEmail: json['signer_email'] as String? ?? '',
      expiresAt: DateTime.tryParse(json['expires_at'] as String? ?? ''),
    );
  }

  bool get isReadyToSign => status == 'READY';
  bool get isSigned => status == 'SIGNED';
  bool get isDeclined => status == 'DECLINED';
  bool get isExpired => status == 'EXPIRED';
}