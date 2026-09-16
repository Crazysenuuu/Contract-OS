import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';

/// Abstracted biometric authentication (spec 2.02 §23).
///
/// Wraps local_auth behind an interface so widgets can be tested with a
/// fake and the underlying implementation can evolve (e.g. adding a
/// secure-store-backed PIN fallback) without touching call sites.
abstract class BiometricAuth {
  /// True when the device supports authentication and has enrolled
  /// biometrics (or a device credential local_auth can use).
  Future<bool> isAvailable();

  /// Shows the system biometric prompt. Returns true on success.
  /// [reason] is displayed by the OS dialog.
  Future<bool> authenticate(String reason);
}

class LocalAuthBiometricAuth implements BiometricAuth {
  LocalAuthBiometricAuth({LocalAuthentication? auth})
      : _auth = auth ?? LocalAuthentication();

  final LocalAuthentication _auth;

  @override
  Future<bool> isAvailable() async {
    try {
      if (!await _auth.isDeviceSupported()) return false;
      final available = await _auth.getAvailableBiometrics();
      return available.isNotEmpty;
    } on Exception {
      // Plugin missing on this platform (web/tests), emulator without
      // enrolment, etc. — biometric step-up is simply unavailable.
      return false;
    }
  }

  @override
  Future<bool> authenticate(String reason) async {
    try {
      return await _auth.authenticate(
        localizedReason: reason,
        // Device PIN/passcode is an acceptable fallback when biometrics
        // fail to read (wet hands, sensor issues) — spec §23 requires
        // step-up auth, not necessarily a fingerprint.
        biometricOnly: false,
      );
    } on Exception {
      // Locked out after too many attempts, cancelled enrolment, plugin
      // missing — treat as a failed gate.
      return false;
    }
  }
}

/// Default provider wired in the app composition root; overridden in tests.
final biometricAuthProvider = Provider<BiometricAuth>(
  (ref) => LocalAuthBiometricAuth(),
);
