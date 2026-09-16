import 'package:flutter/material.dart';

class DocumentPage extends StatelessWidget {
  const DocumentPage({super.key, required this.agreementId});

  final String agreementId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Document')),
      body: Center(
        child: Text(
          agreementId.isEmpty
              ? 'Select an executed document to download (work in progress).'
              : 'Showing document for $agreementId — download wired to document API.',
        ),
      ),
    );
  }
}