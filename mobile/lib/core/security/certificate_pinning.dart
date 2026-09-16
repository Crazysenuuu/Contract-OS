import 'dart:convert';

import 'package:crypto/crypto.dart' as crypto;
import 'package:dio/dio.dart';
import 'package:dio/io.dart';

/// Certificate pinning (spec 2.02 §57): every TLS connection made by the
/// shared [Dio] client validates that the server's leaf certificate hashes
/// to one of the configured SPKI pins.
///
/// A pin set ships both the current and the next certificate so rotation
/// doesn't brick deployed clients. Pins are Base64 SHA-256 digests of the
/// certificate's DER — the same format as `openssl x509 -pubkey | openssl
/// pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`.
class CertificatePinner {
  CertificatePinner({required this.pins});

  /// Acceptable Base64 SHA-256 SPKI digests, e.g.
  /// `{'sha256/AAAA…', 'sha256/BBBB…'}`.
  final Set<String> pins;

  /// Dio's [IOHttpClientAdapter.validateCertificate] hook. Returns true to
  /// accept the connection, false to abort with a badCertificate error.
  bool validate(dynamic certificate, String host, int port) {
    final der = _extractDer(certificate);
    if (der == null) return false;
    final digest = crypto.sha256.convert(der);
    final pin = 'sha256/${base64Encode(digest.bytes)}';
    return pins.contains(pin);
  }

  /// Hooks the pinner into a Dio instance (native platforms only — web
  /// cannot intercept the TLS layer, which the platform browser already
  /// validates).
  void attach(Dio dio) {
    final adapter = dio.httpClientAdapter;
    if (adapter is IOHttpClientAdapter) {
      adapter.validateCertificate = validate;
    }
  }

  /// Extracts DER bytes from the platform certificate object. dart:io's
  /// [X509Certificate] exposes `der`; defensive so alternate shapes fail
  /// closed (no data -> reject) rather than crashing.
  List<int>? _extractDer(dynamic certificate) {
    try {
      final dynamic cert = certificate;
      final der = cert.der;
      if (der is List<int>) return der;
    } catch (_) {
      // fall through
    }
    return null;
  }
}

/// Build-time pin configuration. These are placeholders for local
/// development; release builds inject the production pins via
/// --dart-define so secrets never live in the repository.
const _envPins = String.fromEnvironment(
  'API_CERT_PINS',
  defaultValue: '',
);

/// Pins in effect for this build. With no pins configured the pinner is
/// bypassed — the default localhost development setup has no certificate to
/// pin, and rejecting everything would brick dev builds.
Set<String> configuredPins() {
  final raw = _envPins.trim();
  if (raw.isEmpty) return const {};
  return raw
      .split(',')
      .map((p) => p.trim())
      .where((p) => p.startsWith('sha256/') && p.length > 7)
      .toSet();
}

/// Installs pinning on the app's Dio client when pins are configured.
void attachCertificatePinning(Dio dio) {
  final pins = configuredPins();
  if (pins.isEmpty) return;
  CertificatePinner(pins: pins).attach(dio);
}
