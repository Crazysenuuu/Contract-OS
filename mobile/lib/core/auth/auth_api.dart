import 'package:dio/dio.dart';

import 'secure_token_store.dart';

/// Auth API contracts mirroring backend app/schemas/auth.py.
class AuthTokens {
  AuthTokens({
    required this.accessToken,
    this.refreshToken,
    required this.userId,
  });

  final String accessToken;
  final String? refreshToken;
  final String userId;

  factory AuthTokens.fromJson(Map<String, dynamic> json) => AuthTokens(
        accessToken: json['access_token'] as String,
        refreshToken: json['refresh_token'] as String?,
        userId: json['user_id'] as String,
      );
}

class AuthUser {
  AuthUser({
    required this.id,
    required this.email,
    required this.name,
    required this.status,
    required this.isAdmin,
    required this.mfaEnabled,
  });

  final String id;
  final String email;
  final String name;
  final String status;
  final bool isAdmin;
  final bool mfaEnabled;

  factory AuthUser.fromJson(Map<String, dynamic> json) => AuthUser(
        id: json['id'] as String,
        email: json['email'] as String,
        name: json['name'] as String,
        status: json['status'] as String,
        isAdmin: (json['is_admin'] as bool?) ?? false,
        mfaEnabled: (json['mfa_enabled'] as bool?) ?? false,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'email': email,
        'name': name,
        'status': status,
        'is_admin': isAdmin,
        'mfa_enabled': mfaEnabled,
      };
}

class AuthApi {
  AuthApi(this._dio, this._store);

  final Dio _dio;
  final SecureTokenStore _store;

  /// Login with email/password (+ optional TOTP for MFA-enabled accounts).
  ///
  /// The backend answers 401 with `WWW-Authenticate: Bearer error="mfa_required"`
  /// when a code is required — surfaced as [MfaRequiredException].
  Future<AuthUser> login({
    required String email,
    required String password,
    String? mfaCode,
  }) async {
    try {
      final res = await _dio.post(
        '/auth/login',
        data: {
          'email': email,
          'password': password,
          if (mfaCode != null && mfaCode.isNotEmpty) 'mfa_code': mfaCode,
        },
      );
      final tokens = AuthTokens.fromJson(res.data as Map<String, dynamic>);
      await _store.saveTokens(
        accessToken: tokens.accessToken,
        refreshToken: tokens.refreshToken,
      );
      final user = await me(accessToken: tokens.accessToken);
      await _store.saveUser(user.toJson());
      return user;
    } on DioException catch (e) {
      final data = e.response?.data;
      final detail = data is Map ? data['detail']?.toString() : null;
      final mfaRequired =
          e.response?.headers.value('www-authenticate')?.contains('mfa_required') ??
              false ||
                  detail == 'MFA code required';
      if (mfaRequired) {
        throw MfaRequiredException();
      }
      throw AuthException(detail ?? 'Login failed');
    }
  }

  /// Fetch the current user profile with an explicit token (used right
  /// after login/refresh before the interceptor is armed).
  Future<AuthUser> me({required String accessToken}) async {
    final res = await _dio.get(
      '/auth/me',
      options: Options(headers: {'Authorization': 'Bearer $accessToken'}),
    );
    return AuthUser.fromJson(res.data as Map<String, dynamic>);
  }

  /// Rotate the refresh token, persist the new pair, and return the new
  /// access token. Throws [SessionExpiredException] when the refresh
  /// token is rejected (revoked/expired) — the caller must sign out.
  Future<String> refresh({bool force = false}) async {
    final refreshToken = await _store.readRefreshToken();
    if (refreshToken == null || refreshToken.isEmpty) {
      throw SessionExpiredException();
    }
    try {
      // Bypass the interceptor's refresh path via a bare Dio instance:
      // a refresh call must never trigger another refresh.
      final bare = Dio(_dio.options)
        ..interceptors.clear();
      final res = await bare.post(
        '${_dio.options.baseUrl}/auth/refresh',
        data: {'refresh_token': refreshToken},
      );
      final tokens = AuthTokens.fromJson(res.data as Map<String, dynamic>);
      await _store.saveTokens(
        accessToken: tokens.accessToken,
        refreshToken: tokens.refreshToken,
      );
      return tokens.accessToken;
    } on DioException {
      await _store.clear();
      throw SessionExpiredException();
    }
  }

  /// Revoke the refresh token server-side and wipe local credentials.
  Future<void> logout({required String accessToken}) async {
    try {
      await _dio.post(
        '/auth/logout',
        options: Options(headers: {'Authorization': 'Bearer $accessToken'}),
      );
    } on DioException {
      // Server-side revocation is best-effort; local credentials are
      // always cleared.
    }
    await _store.clear();
  }
}

class AuthException implements Exception {
  AuthException(this.message);
  final String message;
  @override
  String toString() => message;
}

class MfaRequiredException extends AuthException {
  MfaRequiredException() : super('MFA code required');
}

class SessionExpiredException extends AuthException {
  SessionExpiredException() : super('Session expired — please sign in again');
}
