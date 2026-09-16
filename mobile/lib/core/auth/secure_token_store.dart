import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Secure token storage (spec 2.02 §5).
///
/// Backed by flutter_secure_storage: iOS Keychain on iOS, encrypted
/// SharedPreferences (Keystore Tink AES-GCM) on Android. Refresh tokens and
/// access tokens must never live in plain-text shared_preferences.
class SecureTokenStore {
  SecureTokenStore({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
              iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
            );

  final FlutterSecureStorage _storage;

  static const _kAccess = 'contractos_access_token';
  static const _kRefresh = 'contractos_refresh_token';
  static const _kUser = 'contractos_user';

  Future<void> saveTokens({
    required String accessToken,
    String? refreshToken,
  }) async {
    await _storage.write(key: _kAccess, value: accessToken);
    // Rotation: the backend rotates refresh tokens on every /auth/refresh,
    // so overwrite only when a new one was actually issued.
    if (refreshToken != null && refreshToken.isNotEmpty) {
      await _storage.write(key: _kRefresh, value: refreshToken);
    }
  }

  Future<String?> readAccessToken() => _storage.read(key: _kAccess);

  Future<String?> readRefreshToken() => _storage.read(key: _kRefresh);

  Future<void> saveUser(Map<String, dynamic>? user) async {
    if (user == null) {
      await _storage.delete(key: _kUser);
      return;
    }
    await _storage.write(key: _kUser, value: jsonEncode(user));
  }

  Future<Map<String, dynamic>?> readUser() async {
    final raw = await _storage.read(key: _kUser);
    if (raw == null || raw.isEmpty) return null;
    try {
      return jsonDecode(raw) as Map<String, dynamic>;
    } on FormatException {
      // Corrupt entry — treat as absent rather than crash at startup.
      await _storage.delete(key: _kUser);
      return null;
    }
  }

  /// Clears all credential material (logout, forced sign-out on 401
  /// after refresh failure).
  Future<void> clear() async {
    await _storage.delete(key: _kAccess);
    await _storage.delete(key: _kRefresh);
    await _storage.delete(key: _kUser);
  }
}
