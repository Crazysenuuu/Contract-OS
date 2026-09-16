/// A signing event from the evidence ledger (spec 2.06.28).
class SigningEvent {
  const SigningEvent({
    required this.type,
    required this.timestamp,
    required this.agreementId,
    this.payload,
  });

  final String type;
  final DateTime? timestamp;
  final String agreementId;
  final Map<String, dynamic>? payload;

  factory SigningEvent.fromJson(Map<String, dynamic> json) => SigningEvent(
        type: json['type'] as String? ?? '',
        timestamp: DateTime.tryParse(json['timestamp'] as String? ?? ''),
        agreementId: json['agreement_id'] as String? ?? '',
        payload: json,
      );

  static List<SigningEvent> listFromJson(List<dynamic> items) =>
      [for (final e in items) SigningEvent.fromJson(e as Map<String, dynamic>)];
}