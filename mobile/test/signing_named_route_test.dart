import 'dart:typed_data';

import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/core/security/biometric_auth.dart';
import 'package:contractos_mobile/features/signing/presentation/signature_page.dart';
import 'package:contractos_mobile/features/signing/presentation/signing_entry_page.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Adapter that fails every request immediately — no sockets, no pending
/// timers. Route tests assert navigation, not API success.
class _ImmediateErrorAdapter implements HttpClientAdapter {
  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    throw DioException.connectionError(
      requestOptions: options,
      reason: 'offline test adapter',
    );
  }
}

class _NoBiometrics extends BiometricAuth {
  @override
  Future<bool> isAvailable() async => false;

  @override
  Future<bool> authenticate(String reason) async => false;
}

Widget _app() => ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(
          ApiClient(dio: Dio()..httpClientAdapter = _ImmediateErrorAdapter()),
        ),
        biometricAuthProvider.overrideWithValue(_NoBiometrics()),
      ],
      // Mirrors app.dart route table without the config gate.
      child: MaterialApp(
        onGenerateRoute: (settings) {
          if (settings.name == '/signing/auth') {
            final arg = settings.arguments;
            final page = (arg is String && arg.isNotEmpty)
                ? SignaturePage(sessionId: arg)
                : const SigningEntryPage();
            return MaterialPageRoute<void>(
              settings: settings,
              builder: (_) => page,
            );
          }
          return null;
        },
        home: const Scaffold(body: Text('Home')),
      ),
    );

void main() {
  testWidgets('/signing/auth with a sessionId opens SignaturePage',
      (tester) async {
    await tester.pumpWidget(_app());
    Navigator.of(tester.element(find.text('Home')))
        .pushNamed('/signing/auth', arguments: 'sess-1');
    await tester.pumpAndSettle();

    expect(find.byType(SignaturePage), findsOneWidget);
    // Consent step (biometrics unavailable) is reached before any API call.
    expect(find.text('Electronic Signature Consent'), findsOneWidget);
  });

  testWidgets('/signing/auth without arguments opens the token-entry page',
      (tester) async {
    await tester.pumpWidget(_app());
    Navigator.of(tester.element(find.text('Home'))).pushNamed('/signing/auth');
    await tester.pumpAndSettle();

    expect(find.byType(SigningEntryPage), findsOneWidget);
    expect(find.text('No signing link provided.'), findsOneWidget);
  });
}