import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../../core/network/api_client.dart';
import '../models/signing_session.dart';
import 'signature_page.dart';

/// Entry point for a signer arriving via a push / deep link (spec 2.06.27-28).
/// Enforces the signer-side boundaries: the server remains authoritative for
/// authorization, state, and concurrency (spec M2.02 section 58).
class SigningEntryPage extends ConsumerStatefulWidget {
  const SigningEntryPage({super.key, this.token});

  /// One-time token from the notification deep link.
  final String? token;

  @override
  ConsumerState<SigningEntryPage> createState() => _SigningEntryPageState();
}

class _SigningEntryPageState extends ConsumerState<SigningEntryPage> {
  bool _exchanging = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.token != null) {
      // Auto-exchange immediately on deep-link arrival
      WidgetsBinding.instance.addPostFrameCallback((_) => _exchange());
    }
  }

  Future<void> _exchange() async {
    if (widget.token == null) return;
    setState(() {
      _exchanging = true;
      _error = null;
    });
    try {
      final resp = await ref.read(apiClientProvider).dio.post(
        '/signing/sessions/exchange',
        data: {'token': widget.token},
      );
      final session =
          SigningSession.fromJson(resp.data as Map<String, dynamic>);
      if (!mounted) return;
      // Replace this page so back doesn't loop
      Navigator.of(context).pushReplacement<void, void>(
        MaterialPageRoute(
          builder: (_) => SignaturePage(sessionId: session.id),
        ),
      );
    } on DioException catch (e) {
      if (!mounted) return;
      setState(() {
        _exchanging = false;
        _error = e.response?.data?['detail'] as String? ??
            'Failed to load signing session. The link may have expired.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Begin Signing')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: _exchanging
              ? const Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CircularProgressIndicator(),
                    SizedBox(height: 20),
                    Text('Loading your signing session…'),
                  ],
                )
              : widget.token == null
                  ? const Text('No signing link provided.')
                  : Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.edit_document,
                            size: 64, color: Colors.blue),
                        const SizedBox(height: 16),
                        const Text(
                          'You have been invited to sign a document.',
                          textAlign: TextAlign.center,
                          style: TextStyle(fontSize: 16),
                        ),
                        if (_error != null) ...[
                          const SizedBox(height: 16),
                          Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: Colors.red.shade50,
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              _error!,
                              style: const TextStyle(color: Colors.red),
                              textAlign: TextAlign.center,
                            ),
                          ),
                        ],
                        const SizedBox(height: 24),
                        FilledButton(
                          onPressed: _exchanging ? null : _exchange,
                          child: const Text('Continue to signing'),
                        ),
                      ],
                    ),
        ),
      ),
    );
  }
}