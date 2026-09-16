import 'dart:async';

import 'package:dio/dio.dart';

import 'auth_api.dart';
import 'secure_token_store.dart';

/// Attaches the bearer token and transparently refreshes on 401.
///
/// Single-flight refresh: when several concurrent requests hit 401 at once
/// (common on app resume), only ONE /auth/refresh is issued; the others
/// await the same future and retry with the new token. If refresh fails,
/// every waiter receives [SessionExpiredException] and the app signs out.
class AuthInterceptor extends Interceptor {
  AuthInterceptor(this._dio, this._store, this._api);

  final Dio _dio;
  final SecureTokenStore _store;
  final AuthApi _api;

  /// Callback when the session is unrecoverable (refresh rejected) — the
  /// app layer uses it to reset to the login screen.
  void Function()? onSessionExpired;

  Future<String>? _refreshing;
  String? _cachedToken;

  Future<String> _accessToken() async {
    _cachedToken ??= await _store.readAccessToken();
    return _cachedToken!;
  }

  void invalidateCache() {
    _cachedToken = null;
  }

  @override
  void onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    // Never attach credentials to the auth endpoints themselves.
    final path = options.path;
    final isAuthEndpoint =
        path.contains('/auth/login') ||
            path.contains('/auth/refresh') ||
            path.contains('/auth/logout');
    if (!isAuthEndpoint) {
      final token = await _accessToken();
      if (token.isNotEmpty) {
        options.headers['Authorization'] = 'Bearer $token';
      }
    }
    handler.next(options);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) async {
    final status = err.response?.statusCode;
    final path = err.requestOptions.path;

    // Only 401 from a protected endpoint is refreshable. The refresh
    // endpoint itself must fail through (a rejected refresh token means
    // the session is over — no loop).
    if (status != 401 ||
        path.contains('/auth/refresh') ||
        path.contains('/auth/login')) {
      handler.next(err);
      return;
    }

    // Never retry a request that already used the refreshed token.
    final alreadyRetried =
        (err.requestOptions.extra['__auth_retried__'] as bool?) ?? false;
    if (alreadyRetried) {
      await _handleSessionExpired(err, handler);
      return;
    }

    try {
      final newToken = await _refreshSingleFlight();
      _cachedToken = newToken;
      final response = await _retry(err.requestOptions, newToken);
      handler.resolve(response);
    } on SessionExpiredException {
      await _handleSessionExpired(err, handler);
    }
  }

  Future<String> _refreshSingleFlight() {
    // Join an in-flight refresh instead of starting another one.
    _refreshing ??= _api
        .refresh()
        .whenComplete(() => _refreshing = null);
    return _refreshing!;
  }

  Future<Response<dynamic>> _retry(RequestOptions requestOptions, String token) {
    requestOptions
      ..extra['__auth_retried__'] = true
      ..headers['Authorization'] = 'Bearer $token';
    return _dio.fetch(requestOptions);
  }

  Future<void> _handleSessionExpired(
    DioException err,
    ErrorInterceptorHandler handler,
  ) async {
    await _store.clear();
    invalidateCache();
    onSessionExpired?.call();
    handler.next(err);
  }
}
