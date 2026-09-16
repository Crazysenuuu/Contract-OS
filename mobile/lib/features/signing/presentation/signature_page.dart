import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../../core/network/api_client.dart';
import '../../../core/security/biometric_auth.dart';
import '../models/signing_session.dart';

// ---------------------------------------------------------------------------
// Repository / Providers
// ---------------------------------------------------------------------------

class SigningRepository {
  SigningRepository(this._dio);
  final Dio _dio;

  Future<SigningSession> getSession(String sessionId) async {
    final resp = await _dio.get('/api/v1/signing-sessions/$sessionId');
    return SigningSession.fromJson(resp.data as Map<String, dynamic>);
  }

  Future<SigningSession> exchangeToken(String oneTimeToken) async {
    final resp = await _dio.post('/api/v1/signing-sessions/exchange', data: {
      'token': oneTimeToken,
    });
    return SigningSession.fromJson(resp.data as Map<String, dynamic>);
  }

  Future<void> recordConsent(String sessionId) async {
    await _dio.post('/api/v1/signing-sessions/$sessionId/consent');
  }

  Future<void> submitSignature({
    required String sessionId,
    required String signatureData, // base64 or consent-click marker
    required String signatureType, // 'drawn' | 'click_to_sign'
  }) async {
    await _dio.post('/api/v1/signing-sessions/$sessionId/sign', data: {
      'signature_data': signatureData,
      'signature_type': signatureType,
    });
  }

  Future<Map<String, dynamic>> getAgreementPreview(String agreementId) async {
    final resp = await _dio.get('/api/v1/agreements/$agreementId');
    return resp.data as Map<String, dynamic>;
  }
}

final signingRepositoryProvider = Provider<SigningRepository>((ref) {
  return SigningRepository(ref.watch(apiClientProvider).dio);
});

// Provider keyed on sessionId
final signingSessionProvider =
    FutureProvider.autoDispose.family<SigningSession, String>((ref, id) async {
  return ref.watch(signingRepositoryProvider).getSession(id);
});

// ---------------------------------------------------------------------------
// Signature page — step 3 of the signing flow
// ---------------------------------------------------------------------------

class SignaturePage extends ConsumerStatefulWidget {
  const SignaturePage({super.key, required this.sessionId});

  final String sessionId;

  @override
  ConsumerState<SignaturePage> createState() => _SignaturePageState();
}

class _SignaturePageState extends ConsumerState<SignaturePage> {
  _SignStep _step = _SignStep.biometricCheck;
  bool _submitting = false;
  String? _error;

  // For drawn signature we would integrate a canvas package; here we use
  // click-to-sign (legally equivalent under most jurisdictions' e-sign acts).
  static const _signatureType = 'click_to_sign';
  static const _signatureMarker = 'CONSENT_CLICK_ACCEPTED';

  @override
  void initState() {
    super.initState();
    _runBiometricCheck();
  }

  Future<void> _runBiometricCheck() async {
    final biometrics = ref.read(biometricAuthProvider);
    final available = await biometrics.isAvailable();
    if (!available) {
      // No biometrics — proceed directly
      setState(() {
        _step = _SignStep.consent;
      });
      return;
    }
    final ok = await biometrics.authenticate(
      'Authenticate to sign this document',
    );
    if (!mounted) return;
    if (ok) {
      setState(() {
        _step = _SignStep.consent;
      });
    } else {
      setState(() =>
          _error = 'Biometric authentication failed. Please try again.');
    }
  }

  Future<void> _recordConsent() async {
    try {
      await ref
          .read(signingRepositoryProvider)
          .recordConsent(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _step = _SignStep.sign;
      });
    } on DioException catch (e) {
      setState(() => _error = e.message);
    }
  }

  Future<void> _submitSignature() async {
    if (_submitting) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await ref.read(signingRepositoryProvider).submitSignature(
            sessionId: widget.sessionId,
            signatureData: _signatureMarker,
            signatureType: _signatureType,
          );
      if (!mounted) return;
      setState(() => _step = _SignStep.complete);
      // Invalidate session cache so the sessions list refreshes
      ref.invalidate(signingSessionProvider(widget.sessionId));
    } on DioException catch (e) {
      setState(() => _error = e.response?.data?['detail'] as String? ??
          e.message ??
          'Signing failed. Please try again.');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Sign Document'),
        automaticallyImplyLeading: _step != _SignStep.complete,
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: switch (_step) {
            _SignStep.biometricCheck => _BiometricCheckBody(
                error: _error,
                onRetry: _runBiometricCheck,
              ),
            _SignStep.consent => _ConsentBody(
                onConsent: _recordConsent,
              ),
            _SignStep.sign => _SignBody(
                sessionId: widget.sessionId,
                submitting: _submitting,
                error: _error,
                onSign: _submitSignature,
              ),
            _SignStep.complete => const _CompleteBody(),
          },
        ),
      ),
    );
  }
}

enum _SignStep { biometricCheck, consent, sign, complete }

// ---------------------------------------------------------------------------
// Step bodies
// ---------------------------------------------------------------------------

class _BiometricCheckBody extends StatelessWidget {
  const _BiometricCheckBody({this.error, required this.onRetry});
  final String? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    if (error == null) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.fingerprint, size: 72, color: Colors.blue),
            SizedBox(height: 20),
            CircularProgressIndicator(),
            SizedBox(height: 16),
            Text('Verifying your identity…', textAlign: TextAlign.center),
          ],
        ),
      );
    }
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.lock_outline, size: 64, color: Colors.red),
          const SizedBox(height: 16),
          Text(error!, textAlign: TextAlign.center),
          const SizedBox(height: 24),
          FilledButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.fingerprint),
            label: const Text('Try Again'),
          ),
        ],
      ),
    );
  }
}

class _ConsentBody extends StatelessWidget {
  const _ConsentBody({required this.onConsent});
  final VoidCallback onConsent;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Icon(Icons.gavel_outlined, size: 48, color: Colors.blue),
        const SizedBox(height: 20),
        Text(
          'Electronic Signature Consent',
          style: Theme.of(context)
              .textTheme
              .titleLarge
              ?.copyWith(fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.grey.shade100,
            borderRadius: BorderRadius.circular(8),
          ),
          child: const Text(
            'By proceeding, you agree to sign this document electronically. '
            'Your electronic signature carries the same legal weight as a '
            'handwritten signature under the Electronic Transactions Act.\n\n'
            'Your identity, IP address, timestamp, and this consent event '
            'will be recorded in the audit trail.',
            style: TextStyle(fontSize: 13),
          ),
        ),
        const Spacer(),
        FilledButton(
          onPressed: onConsent,
          child: const Padding(
            padding: EdgeInsets.symmetric(vertical: 4),
            child: Text('I Understand and Consent'),
          ),
        ),
        const SizedBox(height: 8),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
      ],
    );
  }
}

class _SignBody extends ConsumerWidget {
  const _SignBody({
    required this.sessionId,
    required this.submitting,
    this.error,
    required this.onSign,
  });

  final String sessionId;
  final bool submitting;
  final String? error;
  final VoidCallback onSign;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sessionAsync = ref.watch(signingSessionProvider(sessionId));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        sessionAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (err, _) => Text('Error: $err'),
          data: (session) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Ready to sign',
                style: Theme.of(context)
                    .textTheme
                    .titleLarge
                    ?.copyWith(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Text('Signing as: ${session.signerName}',
                  style: const TextStyle(color: Colors.grey)),
              Text('Email: ${session.signerEmail}',
                  style: const TextStyle(color: Colors.grey)),
              if (session.expiresAt != null)
                Text(
                  'Link expires: ${session.expiresAt!.toLocal()}',
                  style: const TextStyle(color: Colors.orange, fontSize: 12),
                ),
            ],
          ),
        ),
        const SizedBox(height: 20),

        // Signature representation area
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              border:
                  Border.all(color: Theme.of(context).colorScheme.outline),
              borderRadius: BorderRadius.circular(12),
              color: Colors.white,
            ),
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.draw_outlined,
                    size: 48,
                    color: Theme.of(context)
                        .colorScheme
                        .onSurface
                        .withValues(alpha: 0.3),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Click "Sign Document" to apply\nyour electronic signature',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: Theme.of(context)
                          .colorScheme
                          .onSurface
                          .withValues(alpha: 0.4),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),

        if (error != null) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.red.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.red.shade200),
            ),
            child: Text(error!,
                style: const TextStyle(color: Colors.red, fontSize: 13)),
          ),
        ],

        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: submitting ? null : onSign,
          icon: submitting
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Colors.white),
                )
              : const Icon(Icons.check_circle_outline),
          label: Text(submitting ? 'Signing…' : 'Sign Document'),
        ),
      ],
    );
  }
}

class _CompleteBody extends StatelessWidget {
  const _CompleteBody();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.verified, size: 80, color: Colors.green),
          const SizedBox(height: 20),
          Text(
            'Document Signed!',
            style: Theme.of(context)
                .textTheme
                .headlineSmall
                ?.copyWith(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          Text(
            'Your signature has been recorded and a certified copy\n'
            'will be sent to all parties.',
            textAlign: TextAlign.center,
            style:
                TextStyle(color: Colors.grey.shade600),
          ),
          const SizedBox(height: 32),
          FilledButton(
            onPressed: () => Navigator.of(context).popUntil((r) => r.isFirst),
            child: const Text('Back to Dashboard'),
          ),
        ],
      ),
    );
  }
}