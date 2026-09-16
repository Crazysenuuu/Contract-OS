import 'package:contractos_mobile/core/auth/auth_api.dart';
import 'package:contractos_mobile/core/auth/auth_controller.dart';
import 'package:contractos_mobile/core/auth/secure_token_store.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

class _MockTokenStore extends Mock implements SecureTokenStore {}

class _MockAuthApi extends Mock implements AuthApi {}

const _userJson = {
  'id': 'u-1',
  'email': 'e@x.com',
  'name': 'Jane',
  'status': 'active',
};

AuthUser _user() => AuthUser.fromJson(Map<String, dynamic>.from(_userJson));

DioException _unauthorized() => DioException(
      requestOptions: RequestOptions(path: '/auth/me'),
      response: Response<dynamic>(
        requestOptions: RequestOptions(path: '/auth/me'),
        statusCode: 401,
      ),
    );

void main() {
  late _MockTokenStore store;
  late _MockAuthApi api;
  late ProviderContainer container;
  late List<(AuthStatus, AuthStatus)> transitions;

  setUpAll(() {
    registerFallbackValue(<String, dynamic>{});
  });

  setUp(() {
    store = _MockTokenStore();
    api = _MockAuthApi();
    transitions = [];

    // Default stubs: an empty (signed-out) store.
    when(() => store.readAccessToken()).thenAnswer((_) async => null);
    when(() => store.readRefreshToken()).thenAnswer((_) async => null);
    when(() => store.readUser()).thenAnswer((_) async => null);
    when(() => store.saveTokens(
          accessToken: any(named: 'accessToken'),
          refreshToken: any(named: 'refreshToken'),
        )).thenAnswer((_) async {});
    when(() => store.saveUser(any())).thenAnswer((_) async {});
    when(() => store.clear()).thenAnswer((_) async {});

    container = ProviderContainer(overrides: [
      apiClientProvider.overrideWithValue(ApiClient(dio: Dio())),
      secureTokenStoreProvider.overrideWithValue(store),
      authApiProvider.overrideWithValue(api),
    ]);
    addTearDown(container.dispose);
  });

  /// Attaches a keep-alive listener AFTER test-specific stubs are in place.
  ///
  /// The notifier provider is auto-dispose: reading it builds it and
  /// schedules the restore microtask, so it must not be touched before the
  /// stubs that restore will observe are registered.
  void attach() {
    container.listen<AuthState>(authControllerProvider, (previous, next) {
      if (previous != null && previous.status != next.status) {
        transitions.add((previous.status, next.status));
      }
    });
  }

  /// Lets the build()-scheduled restore microtask and its awaits drain.
  Future<void> flush() => Future<void>.delayed(Duration.zero);

  group('AuthController', () {
    test('starts unknown, then unauthenticated when nothing is stored',
        () async {
      attach();

      expect(container.read(authControllerProvider).status,
          AuthStatus.unknown);

      await flush();

      expect(container.read(authControllerProvider).status,
          AuthStatus.unauthenticated);
      expect(
        transitions,
        contains((AuthStatus.unknown, AuthStatus.unauthenticated)),
      );
    });

    test('restores an authenticated session when tokens + user validate',
        () async {
      when(() => store.readAccessToken()).thenAnswer((_) async => 'jwt');
      when(() => store.readRefreshToken()).thenAnswer((_) async => 'r');
      when(() => store.readUser())
          .thenAnswer((_) async => Map<String, dynamic>.from(_userJson));
      when(() => api.me(accessToken: 'jwt')).thenAnswer((_) async => _user());

      attach();
      await flush();

      final state = container.read(authControllerProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.id, 'u-1');
      verify(() => store.saveUser(any())).called(1);
    });

    test('expired access token with a refresh token recovers the session',
        () async {
      when(() => store.readAccessToken()).thenAnswer((_) async => 'jwt');
      when(() => store.readRefreshToken()).thenAnswer((_) async => 'r');
      when(() => api.me(accessToken: 'jwt')).thenThrow(_unauthorized());
      when(() => api.refresh()).thenAnswer((_) async => 'new-jwt');
      when(() => api.me(accessToken: 'new-jwt'))
          .thenAnswer((_) async => _user());

      attach();
      await flush();

      final state = container.read(authControllerProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.id, 'u-1');
      verify(() => api.refresh()).called(1);
    });

    test('rejected refresh on restore signs the session out', () async {
      when(() => store.readAccessToken()).thenAnswer((_) async => 'jwt');
      when(() => store.readRefreshToken()).thenAnswer((_) async => 'r');
      when(() => api.me(accessToken: 'jwt')).thenThrow(_unauthorized());
      when(() => api.refresh()).thenThrow(SessionExpiredException());

      attach();
      await flush();

      expect(container.read(authControllerProvider).status,
          AuthStatus.unauthenticated);
      // Local wipe itself happens inside AuthApi.refresh (mocked here).
    });

    test('offline launch keeps the session when a refresh token exists',
        () async {
      when(() => store.readAccessToken()).thenAnswer((_) async => 'jwt');
      when(() => store.readRefreshToken()).thenAnswer((_) async => 'r');
      when(() => store.readUser())
          .thenAnswer((_) async => Map<String, dynamic>.from(_userJson));
      when(() => api.me(accessToken: 'jwt')).thenThrow(DioException(
        requestOptions: RequestOptions(path: '/auth/me'),
        type: DioExceptionType.connectionError,
      ));

      attach();
      await flush();

      final state = container.read(authControllerProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.id, 'u-1');
      expect(state.error, contains('Offline'));
      verifyNever(() => api.refresh());
    });

    test('login persists tokens + user and becomes authenticated', () async {
      when(() => api.login(
            email: any(named: 'email'),
            password: any(named: 'password'),
          )).thenAnswer((_) async => _user());

      attach();
      await flush(); // signed-out restore
      final controller = container.read(authControllerProvider.notifier);

      await controller.login(email: 'e@x.com', password: 'secret');

      final state = container.read(authControllerProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.email, 'e@x.com');
      // Login shows a loading state, so the final hop is unknown -> authed.
      expect(transitions.last, (AuthStatus.unknown, AuthStatus.authenticated));
    });

    test('MFA challenge moves state to mfaRequired', () async {
      when(() => api.login(
            email: any(named: 'email'),
            password: any(named: 'password'),
          )).thenThrow(MfaRequiredException());

      attach();
      await flush();
      final controller = container.read(authControllerProvider.notifier);

      await controller.login(email: 'e@x.com', password: 'secret');

      expect(container.read(authControllerProvider).status,
          AuthStatus.mfaRequired);
    });

    test('failed login surfaces the message and stays unauthenticated',
        () async {
      when(() => api.login(
            email: any(named: 'email'),
            password: any(named: 'password'),
          )).thenThrow(AuthException('Invalid credentials'));

      attach();
      await flush();
      final controller = container.read(authControllerProvider.notifier);

      await controller.login(email: 'e@x.com', password: 'wrong');

      final state = container.read(authControllerProvider);
      expect(state.status, AuthStatus.unauthenticated);
      expect(state.error, contains('Invalid credentials'));
    });

    test('logout revokes server-side, clears storage, and signs out',
        () async {
      when(() => store.readAccessToken()).thenAnswer((_) async => 'jwt');
      when(() => store.readRefreshToken()).thenAnswer((_) async => 'r');
      when(() => api.me(accessToken: 'jwt')).thenAnswer((_) async => _user());
      when(() => api.logout(accessToken: 'jwt')).thenAnswer((_) async {});

      attach();
      await flush();
      expect(container.read(authControllerProvider).status,
          AuthStatus.authenticated);
      final controller = container.read(authControllerProvider.notifier);

      await controller.logout();

      expect(container.read(authControllerProvider).status,
          AuthStatus.unauthenticated);
      expect(
        transitions,
        contains((AuthStatus.authenticated, AuthStatus.unauthenticated)),
      );
      verify(() => api.logout(accessToken: 'jwt')).called(1);
      verify(() => store.clear()).called(1);
    });

    test('logout without a stored token skips the API but still clears',
        () async {
      attach();
      await flush(); // signed-out restore
      final controller = container.read(authControllerProvider.notifier);

      await controller.logout();

      expect(container.read(authControllerProvider).status,
          AuthStatus.unauthenticated);
      verifyNever(() => api.logout(accessToken: any(named: 'accessToken')));
      verify(() => store.clear()).called(1);
    });
  });
}
