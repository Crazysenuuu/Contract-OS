import 'dart:convert';

import 'package:contractos_mobile/core/auth/auth_api.dart';
import 'package:contractos_mobile/core/auth/secure_token_store.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

class _MockPlugin extends Mock implements FlutterSecureStorage {}

void main() {
  late _MockPlugin plugin;
  late SecureTokenStore store;

  setUp(() {
    plugin = _MockPlugin();
    store = SecureTokenStore(storage: plugin);

    when(() => plugin.write(
          key: any(named: 'key'),
          value: any(named: 'value'),
        )).thenAnswer((_) async {});
    when(() => plugin.read(key: any(named: 'key')))
        .thenAnswer((_) async => null);
    when(() => plugin.delete(key: any(named: 'key')))
        .thenAnswer((_) async {});
  });

  group('SecureTokenStore', () {
    test('save then read back access + refresh tokens', () async {
      when(() => plugin.read(key: 'contractos_access_token'))
          .thenAnswer((_) async => 'access-1');
      when(() => plugin.read(key: 'contractos_refresh_token'))
          .thenAnswer((_) async => 'refresh-1');
      String? writtenAccess;
      when(() => plugin.write(
            key: 'contractos_access_token',
            value: captureAny(named: 'value'),
          )).thenAnswer((inv) async {
        writtenAccess = inv.namedArguments[const Symbol('value')] as String?;
        return;
      });

      await store.saveTokens(accessToken: 'access-1', refreshToken: 'refresh-1');

      expect(await store.readAccessToken(), 'access-1');
      expect(await store.readRefreshToken(), 'refresh-1');
      expect(writtenAccess, 'access-1');
    });

    test('saving tokens without a refresh keeps the existing refresh',
        () async {
      await store.saveTokens(accessToken: 'a1', refreshToken: 'r1');
      await store.saveTokens(accessToken: 'a2');

      final refreshWrites = verify(() => plugin.write(
            key: 'contractos_refresh_token',
            value: captureAny(named: 'value'),
          )).captured;
      // Only the first write carried a refresh token — the rotation-safe
      // partial write must not clobber the stored refresh token.
      expect(refreshWrites, ['r1']);
    });

    test('clear wipes tokens and user', () async {
      when(() => plugin.read(key: any(named: 'key')))
          .thenAnswer((_) async => null);

      await store.saveTokens(accessToken: 'a', refreshToken: 'r');
      await store.saveUser({'id': 'u1'});
      await store.clear();

      verify(() => plugin.delete(key: 'contractos_access_token')).called(1);
      verify(() => plugin.delete(key: 'contractos_refresh_token')).called(1);
      verify(() => plugin.delete(key: 'contractos_user')).called(1);
      expect(await store.readAccessToken(), isNull);
      expect(await store.readRefreshToken(), isNull);
      expect(await store.readUser(), isNull);
    });

    test('corrupt user JSON is treated as absent', () async {
      when(() => plugin.read(key: 'contractos_user'))
          .thenAnswer((_) async => '{not json');
      expect(await store.readUser(), isNull);
    });

    test('user roundtrip preserves profile fields', () async {
      Map<String, dynamic>? written;
      when(() => plugin.write(
            key: 'contractos_user',
            value: any(named: 'value', that: isNotNull),
          )).thenAnswer((inv) async {
        written =
            jsonDecode(inv.namedArguments[const Symbol('value')] as String)
                as Map<String, dynamic>;
        return;
      });
      when(() => plugin.read(key: 'contractos_user')).thenAnswer(
        (_) async => jsonEncode({
          'id': 'u1',
          'email': 'e@x.com',
          'name': 'Jane',
          'status': 'active',
          'is_admin': true,
          'mfa_enabled': false,
        }),
      );

      await store.saveUser({
        'id': 'u1',
        'email': 'e@x.com',
        'name': 'Jane',
        'status': 'active',
        'is_admin': true,
        'mfa_enabled': false,
      });
      expect(written, isNotNull);

      final user = await store.readUser();
      expect(user?['name'], 'Jane');
      expect(user?['is_admin'], true);
    });
  });

  group('AuthTokens / AuthUser parsing', () {
    test('parses backend TokenResponse', () {
      final tokens = AuthTokens.fromJson({
        'access_token': 'jwt',
        'refresh_token': 'r',
        'token_type': 'bearer',
        'user_id': 'u-1',
      });
      expect(tokens.accessToken, 'jwt');
      expect(tokens.refreshToken, 'r');
      expect(tokens.userId, 'u-1');
    });

    test('parses UserResponse with defaults', () {
      final user = AuthUser.fromJson({
        'id': 'u-1',
        'email': 'e@x.com',
        'name': 'Jane',
        'status': 'active',
        'email_verified_at': '2026-01-01T00:00:00Z',
        'created_at': '2026-01-01T00:00:00Z',
      });
      expect(user.isAdmin, isFalse);
      expect(user.mfaEnabled, isFalse);
      expect(jsonEncode(user.toJson()), contains('"name":"Jane"'));
    });
  });
}
