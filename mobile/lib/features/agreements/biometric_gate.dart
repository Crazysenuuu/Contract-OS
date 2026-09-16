import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/security/biometric_auth.dart';

/// Step-up gate over agreement content (spec 2.02 §23): while locked, a
/// scrim hides everything; unlocking runs the system biometric prompt.
///
/// Behaviour:
///  - No biometrics available (or check throws): content renders unlocked.
///  - App loses focus or backgrounds (inactive/paused/hidden): re-locks.
///  - Unlock attempt runs the OS prompt; success reveals content.
class BiometricGate extends ConsumerStatefulWidget {
  const BiometricGate({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<BiometricGate> createState() => _BiometricGateState();
}

class _BiometricGateState extends ConsumerState<BiometricGate>
    with WidgetsBindingObserver {
  bool? _available; // null = still checking
  bool _locked = false;
  bool _checking = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _checkAvailability();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _checkAvailability() async {
    try {
      final available =
          await ref.read(biometricAuthProvider).isAvailable();
      if (!mounted) return;
      setState(() {
        _available = available;
        _locked = available; // start locked only when a gate can run
      });
    } on Exception {
      if (!mounted) return;
      setState(() => _available = false);
    }
  }

  /// Re-lock whenever the app is left (recents switch, notification shade,
  /// backgrounding) — the guard required for contract data (spec §23).
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (_available == true &&
        (state == AppLifecycleState.paused ||
            state == AppLifecycleState.inactive ||
            state == AppLifecycleState.hidden)) {
      setState(() => _locked = true);
    }
    if (_available == true && state == AppLifecycleState.resumed && _locked) {
      _promptBiometrics();
    }
  }

  Future<void> _promptBiometrics() async {
    if (_checking) return;
    setState(() => _checking = true);
    try {
      final ok = await ref
          .read(biometricAuthProvider)
          .authenticate('Unlock to view agreements');
      if (!mounted) return;
      setState(() {
        _locked = !ok;
        _checking = false;
      });
    } on Exception {
      if (!mounted) return;
      setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    // Availability not yet known: show content behind a short scrim to
    // avoid flashing protected data before the check completes.
    if (_available == null) {
      return const _GateScaffold(
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (!_locked) return widget.child;

    return _GateScaffold(
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.lock_outline, size: 48),
            const SizedBox(height: 16),
            const Text(
              'Agreements are locked',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Text(
                'Authenticate with biometrics or your device PIN to view '
                'contract content.',
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.black54),
              ),
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _checking ? null : _promptBiometrics,
              icon: _checking
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.fingerprint),
              label: Text(_checking ? 'Checking…' : 'Unlock'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Hosts gate content on a scaffold-independent surface (the agreements
/// tab already lives inside a Scaffold; this only fills the body slot).
class _GateScaffold extends StatelessWidget {
  const _GateScaffold({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(color: Theme.of(context).scaffoldBackgroundColor, child: child);
  }
}
