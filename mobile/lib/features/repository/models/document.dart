/// Executed document reference in the contract repository (spec 2.07.39).
class Document {
  const Document({
    required this.id,
    required this.title,
    required this.docType,
    required this.immutable,
    this.downloadUrl,
  });

  final String id;
  final String title;
  final String docType;
  final bool immutable;
  final String? downloadUrl;

  factory Document.fromJson(Map<String, dynamic> json) => Document(
        id: json['id'] as String? ?? json['document_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        docType: json['doc_type'] as String? ?? '',
        immutable: json['immutable'] as bool? ?? false,
        downloadUrl: json['download_url'] as String?,
      );
}