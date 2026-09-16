import 'package:flutter/material.dart';

/// Step-up authentication page (spec 2.06.12): the signer enters the OTP
/// code delivered out-of-band. Biometrics stay a local convenience; the
/// server verifies the signing session (spec M2.02 section 23).
class AuthenticationPage extends StatelessWidget {
  const AuthenticationPage({super.key, required this.sessionId});

  final String sessionId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Verify Your Identity')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'A one-time code has been sent to you. Enter it below to continue signing.',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 24),
            TextField(
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'One-time code',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: () {},
              child: const Text('Verify'),
            ),
          ],
        ),
      ),
    );
  }
}