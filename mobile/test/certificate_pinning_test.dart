import 'dart:convert';

import 'package:contractos_mobile/core/security/certificate_pinning.dart';
import 'package:crypto/crypto.dart' as crypto;
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('CertificatePinner', () {
    // A fixed "DER" payload and its Base64 SHA-256 digest.
    const derBytes = <int>[1, 2, 3, 4, 5, 6, 7, 8];
    final expectedDigest =
        base64Encode(crypto.sha256.convert(derBytes).bytes);

    test('accepts a certificate whose SPKI digest matches a pin', () {
      final pinner = CertificatePinner(pins: {'sha256/$expectedDigest'});
      expect(
        pinner.validate(_FakeCert(derBytes), 'api.example.com', 443),
        isTrue,
      );
    });

    test('rejects a certificate that matches no pin', () {
      final pinner = CertificatePinner(pins: {'sha256/AAAA'});
      expect(
        pinner.validate(_FakeCert(derBytes), 'api.example.com', 443),
        isFalse,
      );
    });

    test('fails closed when the certificate exposes no DER bytes', () {
      final pinner = CertificatePinner(pins: {'sha256/$expectedDigest'});
      expect(pinner.validate(null, 'api.example.com', 443), isFalse);
      expect(pinner.validate(Object(), 'api.example.com', 443), isFalse);
    });

    test('accepts any of the pins in a rotation set', () {
      final pinner = CertificatePinner(pins: {
        'sha256/AAAA',
        'sha256/$expectedDigest',
      });
      expect(
        pinner.validate(_FakeCert(derBytes), 'api.example.com', 443),
        isTrue,
      );
    });
  });
}

class _FakeCert {
  _FakeCert(this.der);
  final List<int> der;
}
