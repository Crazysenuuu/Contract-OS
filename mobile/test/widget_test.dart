import 'dart:typed_data';

import 'package:contractos_mobile/app.dart';
import 'package:flutter/material.dart';
import 'package:contractos_mobile/core/auth/auth_api.dart';
import 'package:contractos_mobile/core/auth/auth_controller.dart';
import 'package:contractos_mobile/core/auth/secure_token_store.dart';
import 'package:contractos_mobile/core/live/live_update_providers.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/core/network/mobile_config_service.dart';
import 'package:contractos_mobile/core/security/biometric_auth.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

class _MockAuthApi extends Mock implements AuthApi {}

/// Serves canned responses for the boot test's GET /agreements call;
/// anything else fails immediately (no sockets, no pending timers).
class _StubAdapter implements HttpClientAdapter {
  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final path = options.uri.path;
    if (path.endsWith('/agreements') && options.method == 'GET') {
      return ResponseBody.fromString(
        '''{"items": [{"id": "a-1", "title": "MSA with Globex", "status": "pending_signature", "counterparty": "Globex", "agreement_type_name": "MSA"}]}''',
        200,
        headers: {
          Headers.contentTypeHeader: [Headers.jsonContentType],
        },
      );
    }
    throw DioException.connectionError(
      requestOptions: options,
      reason: 'no stub for $path',
    );
  }
}

/// local_auth's platform channel never resolves in the test binding, so
/// boot tests run with biometrics unavailable (gate renders unlocked).
class _NoBiometrics extends BiometricAuth {
  @override
  Future<bool> isAvailable() async => false;

  @override
  Future<bool> authenticate(String reason) async => false;
}

/// In-memory stand-in for the Keychain/Keystore-backed store: platform
/// plugins are unavailable in widget tests, so restore must run without
/// touching real secure storage.
class _MemTokenStore extends SecureTokenStore {
  final Map<String, String> _mem = {};
  Map<String, dynamic>? savedUser;

  @override
  Future<void> saveTokens({
    required String accessToken,
    String? refreshToken,
  }) async {
    _mem['access'] = accessToken;
    if (refreshToken != null && refreshToken.isNotEmpty) {
      _mem['refresh'] = refreshToken;
    }
  }

  @override
  Future<String?> readAccessToken() async => _mem['access'];

  @override
  Future<String?> readRefreshToken() async => _mem['refresh'];

  @override
  Future<void> saveUser(Map<String, dynamic>? user) async {
    savedUser = user;
  }

  @override
  Future<Map<String, dynamic>?> readUser() async => savedUser;

  @override
  Future<void> clear() async {
    _mem.clear();
    savedUser = null;
  }
}

AuthUser _user() => AuthUser.fromJson(const {
      'id': 'u-1',
      'email': 'e@x.com',
      'name': 'Jane',
      'status': 'active',
    });

Future<Widget> _bootApp({
  required _MemTokenStore store,
  required _MockAuthApi api,
}) async {
  // ProviderScope construction is synchronous; wrap in a pump below.
  return ProviderScope(
    overrides: [
      mobileConfigProvider.overrideWith((ref) async => const MobileConfig(
            minimumSupportedVersion: '1.0.0',
            recommendedVersion: '1.0.0',
            maintenance: false,
          )),
      apiClientProvider.overrideWithValue(
        ApiClient(dio: Dio()..httpClientAdapter = _StubAdapter()),
      ),
      secureTokenStoreProvider.overrideWithValue(store),
      authApiProvider.overrideWithValue(api),
      biometricAuthProvider.overrideWithValue(_NoBiometrics()),
      // Boot tests must not open real WebSocket connections.
      liveUpdateLifecycleProvider.overrideWith((ref) {}),
    ],
    child: const ContractOSApp(),
  );
}

void main() {
  setUpAll(() {
    registerFallbackValue('');
  });

  testWidgets('signed-out boot: version gate passes and login is shown',
      (tester) async {
    final store = _MemTokenStore(); // empty store -> unauthenticated
    final api = _MockAuthApi();

    await tester.pumpWidget(await _bootApp(store: store, api: api));
    await tester.pump();
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsOneWidget);
    // The version gate did not block: no forced-upgrade screen.
    expect(
      find.textContaining('no longer supported'),
      findsNothing,
    );
  });

  testWidgets('signed-in boot: version gate passes and the shell renders',
      (tester) async {
    final store = _MemTokenStore()
      .._mem['access'] = 'jwt'
      .._mem['refresh'] = 'r';
    final api = _MockAuthApi();
    when(() => api.me(accessToken: any(named: 'accessToken')))
        .thenAnswer((_) async => _user());

    await tester.pumpWidget(await _bootApp(store: store, api: api));
    await tester.pump(); // start restore
    await tester.pump(const Duration(milliseconds: 50)); // restore completes
    await tester.pumpAndSettle();

    // The landing tab is the Task Hub (spec 2.02 unified task inbox). Its
    // sources (tasks/approvals) are not stubbed here, so it shows its error
    // state — the shell still renders, which is what this test asserts.
    expect(find.text('My Tasks'), findsOneWidget);

    // Contracts remain reachable via the bottom navigation.
    await tester.tap(
      find.descendant(
        of: find.byType(NavigationBar),
        matching: find.text('Contracts'),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Pending Signature'), findsOneWidget);
    expect(find.text('Profile'), findsWidgets);
  });
}
