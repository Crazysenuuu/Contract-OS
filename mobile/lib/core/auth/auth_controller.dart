import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../network/api_client.dart';
import 'auth_api.dart';
import 'auth_interceptor.dart';
import 'secure_token_store.dart';

/// Composition root for the auth stack (single instances per app run).
final secureTokenStoreProvider = Provider<SecureTokenStore>((ref) {
  return SecureTokenStore();
});

final authApiProvider = Provider<AuthApi>((ref) {
  return AuthApi(ref.watch(apiClientProvider).dio, ref.watch(secureTokenStoreProvider));
});

final authInterceptorProvider = Provider<AuthInterceptor>((ref) {
  final interceptor = AuthInterceptor(
    ref.watch(apiClientProvider).dio,
    ref.watch(secureTokenStoreProvider),
    ref.watch(authApiProvider),
  );
  return interceptor;
});

/// Auth state exposed to the UI.
enum AuthStatus { unknown, authenticated, unauthenticated, mfaRequired }

class AuthState {
  const AuthState({
    this.status = AuthStatus.unknown,
    this.user,
    this.error,
  });

  final AuthStatus status;
  final AuthUser? user;
  final String? error;

  AuthState copyWith({AuthStatus? status, AuthUser? user, String? error}) {
    return AuthState(
      status: status ?? this.status,
      user: user ?? this.user,
      error: error,
    );
  }
}

class AuthController extends Notifier<AuthState> {
  late AuthApi _api;
  late SecureTokenStore _store;
  late AuthInterceptor _interceptor;
  bool _wired = false;

  void _ensureWired() {
    if (_wired) return;
    _api = ref.read(authApiProvider);
    _store = ref.read(secureTokenStoreProvider);
    _interceptor = ref.read(authInterceptorProvider);
    final client = ref.read(apiClientProvider);
    client.dio.interceptors.add(_interceptor);
    _interceptor.onSessionExpired = () {
      state = const AuthState(
        status: AuthStatus.unauthenticated,
        error: 'Session expired — please sign in again',
      );
    };
    _wired = true;
  }

  @override
  AuthState build() {
    _ensureWired();
    // Restore session asynchronously; start as unknown (splash shows).
    Future.microtask(_restoreSession);
    return const AuthState();
  }

  Future<void> _restoreSession() async {
    final access = await _store.readAccessToken();
    final refresh = await _store.readRefreshToken();
    final savedUser = await _store.readUser();

    if (access == null || access.isEmpty) {
      state = AuthState(
        status: AuthStatus.unauthenticated,
        user: savedUser == null ? null : AuthUser.fromJson(savedUser),
      );
      return;
    }

    // Warm the interceptor's token cache with the stored access token.
    _interceptor.invalidateCache();

    try {
      final user = await _api.me(accessToken: access);
      await _store.saveUser(user.toJson());
      state = AuthState(status: AuthStatus.authenticated, user: user);
    } on DioException catch (e) {
      if (e.response?.statusCode == 401 && refresh != null) {
        // Access token expired — try one refresh, then decide.
        try {
          final newAccess = await _api.refresh();
          final user = await _api.me(accessToken: newAccess);
          await _store.saveUser(user.toJson());
          state = AuthState(status: AuthStatus.authenticated, user: user);
          return;
        } on SessionExpiredException {
          state = const AuthState(status: AuthStatus.unauthenticated);
          return;
        }
      }
      // Network/5xx: keep the session if we hold valid-looking credentials
      // (offline launch must not force logout — spec 2.02 §28).
      final hasRefresh = refresh != null && refresh.isNotEmpty;
      state = AuthState(
        status: hasRefresh ? AuthStatus.authenticated : AuthStatus.unauthenticated,
        user: savedUser == null ? null : AuthUser.fromJson(savedUser),
        error: hasRefresh ? 'Offline — showing cached data' : null,
      );
    }
  }

  Future<void> login({
    required String email,
    required String password,
    String? mfaCode,
  }) async {
    state = state.copyWith(status: AuthStatus.unknown, error: null);
    try {
      final user = await _api.login(
        email: email,
        password: password,
        mfaCode: mfaCode,
      );
      _interceptor.invalidateCache();
      state = AuthState(status: AuthStatus.authenticated, user: user);
    } on MfaRequiredException {
      state = const AuthState(status: AuthStatus.mfaRequired);
    } on AuthException catch (e) {
      state = AuthState(status: AuthStatus.unauthenticated, error: e.message);
    }
  }

  Future<void> logout() async {
    final access = await _store.readAccessToken();
    if (access != null && access.isNotEmpty) {
      await _api.logout(accessToken: access);
    }
    await _store.clear();
    _interceptor.invalidateCache();
    state = const AuthState(status: AuthStatus.unauthenticated);
  }
}

final authControllerProvider =
    NotifierProvider<AuthController, AuthState>(AuthController.new);
