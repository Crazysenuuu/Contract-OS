import 'package:flutter/material.dart';

class CompletionPage extends StatelessWidget {
  const CompletionPage({super.key, required this.sessionId});

  final String sessionId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Signing Complete')),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.check_circle, size: 72, color: Colors.green),
            const SizedBox(height: 16),
            const Text('Your signature has been recorded.'),
            const SizedBox(height: 8),
            const Text(
              'The other parties will be notified when it is their turn.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}