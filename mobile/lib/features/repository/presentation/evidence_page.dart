import 'package:flutter/material.dart';

class EvidencePage extends StatelessWidget {
  const EvidencePage({super.key, required this.agreementId});

  final String agreementId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Evidence')),
      body: Center(child: Text('Execution evidence ledger for $agreementId')),
    );
  }
}