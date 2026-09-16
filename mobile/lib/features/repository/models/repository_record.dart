/// Repository record model (spec 2.07.39).
class RepositoryRecord {
  const RepositoryRecord({
    required this.id,
    required this.agreementId,
    required this.docType,
    required this.version,
    required this.immutable,
    required this.createdAt,
  });

  final String id;
  final String agreementId;
  final String docType;
  final int? version;
  final bool immutable;
  final DateTime? createdAt;

  factory RepositoryRecord.fromJson(Map<String, dynamic> json) =>
      RepositoryRecord(
        id: json['id'] as String? ?? '',
        agreementId: json['agreement_id'] as String? ?? '',
        docType: json['doc_type'] as String? ?? '',
        version: (json['version'] as num?)?.toInt(),
        immutable: json['immutable'] as bool? ?? false,
        createdAt:
            DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}