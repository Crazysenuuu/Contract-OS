import 'package:flutter/material.dart';

/// Review page: signer reads the negotiated document before consent (2.06.9).
class ReviewPage extends StatelessWidget {
  const ReviewPage({super.key, required this.sessionId});

  final String sessionId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Review Document')),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Text('Document review — fetched via the session document API'),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: () => Navigator.of(context)
                  .pushNamed('/signing/auth', arguments: sessionId),
              child: const Text('Consent & Continue'),
            ),
          ],
        ),
      ),
    );
  }
}