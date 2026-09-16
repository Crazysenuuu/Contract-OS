import 'package:flutter/material.dart';

class TimelinePage extends StatelessWidget {
  const TimelinePage({super.key, required this.agreementId});

  final String agreementId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Timeline')),
      body: Center(child: Text('Lifecycle history for $agreementId')),
    );
  }
}