/// A signature field placement on the review document (spec 2.06.9).
class SignatureField {
  const SignatureField({
    required this.id,
    required this.fieldKey,
    required this.page,
    required this.signatureType,
  });

  final String id;
  final String fieldKey;
  final int page;
  final String signatureType;

  factory SignatureField.fromJson(Map<String, dynamic> json) => SignatureField(
        id: json['id'] as String? ?? '',
        fieldKey: json['field_key'] as String? ?? '',
        page: (json['page'] as num?)?.toInt() ?? 1,
        signatureType: json['signature_type'] as String? ?? 'DRAWN',
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'field_key': fieldKey,
        'page': page,
        'signature_type': signatureType,
      };
}