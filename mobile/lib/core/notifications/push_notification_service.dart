import 'dart:async';
import 'dart:io';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../auth/auth_controller.dart';
import '../network/api_client.dart';
import 'push_banner_controller.dart';

/// Push notifications (spec 2.02 §24–26).
///
/// Lifecycle:
///   authenticated → request permission → fetch FCM token → register with
///   backend (`POST /mobile/devices`) → listen for token rotation and
///   re-register → on sign-out, delete the backend device record.
///
/// On platforms where Firebase is not configured (web, tests, simulators
/// without the plist/JSON), initialization degrades to a disabled no-op so
/// the app still runs.
class PushNotificationService {
  PushNotificationService(this._ref);

  final Ref _ref;
  StreamSubscription<String>? _tokenRotationSub;
  StreamSubscription<RemoteMessage>? _foregroundSub;
  bool _initialized = false;

  /// Stable per-install device id, persisted in plain shared_preferences
  /// (it is an identifier, not a secret — it must survive the secure-store
  /// wipe on logout). Overridable in tests.
  static Future<String> Function()? deviceIdLoaderForTest;

  Future<String> _deviceId() async {
    final loader = deviceIdLoaderForTest;
    if (loader != null) return loader();
    final prefs = await SharedPreferences.getInstance();
    const key = 'contractos_device_id';
    final existing = prefs.getString(key);
    if (existing != null && existing.isNotEmpty) return existing;
    final generated = DateTime.now().microsecondsSinceEpoch.toRadixString(36);
    await prefs.setString(key, generated);
    return generated;
  }

  String? currentToken;

  /// Requests permission, fetches and registers the FCM token.
  /// Returns the token, or null when push is unavailable/denied.
  Future<String?> initialize() async {
    if (_initialized) return currentToken;
    _initialized = true;

    try {
      await Firebase.initializeApp();
    } on Exception catch (e) {
      debugPrint('[push] Firebase unavailable, push disabled: $e');
      return null;
    }

    final messaging = FirebaseMessaging.instance;

    // Permission prompt on first authenticated launch (spec §24).
    final settings = await messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
    );
    if (settings.authorizationStatus != AuthorizationStatus.authorized &&
        settings.authorizationStatus != AuthorizationStatus.provisional) {
      debugPrint('[push] permission denied — proceeding without push');
    }

    final token = await _fetchAndRegisterToken(messaging);

    // Token rotation (spec §26): FCM rotates tokens (app restore, OS
    // updates, key rotation) — re-register whenever it changes.
    _tokenRotationSub = messaging.onTokenRefresh.listen((newToken) async {
      debugPrint('[push] FCM token rotated');
      await _registerToken(newToken);
    });

    // Foreground presentation (spec §25): the system tray does not show
    // notifications while the app is open, so surface them as in-app
    // banners. Background/terminated messages arrive via the system tray.
    _foregroundSub = FirebaseMessaging.onMessage.listen((message) {
      debugPrint(
        '[push] foreground message: '
        '${message.notification?.title ?? message.data}',
      );
      _ref.read(pushBannerControllerProvider.notifier)
          .showFromRemoteMessage(message);
    });

    return token;
  }

  Future<String?> _fetchAndRegisterToken(FirebaseMessaging messaging) async {
    try {
      final token = await messaging.getToken();
      if (token == null) return null;
      await _registerToken(token);
      return token;
    } on Exception catch (e) {
      debugPrint('[push] token fetch failed: $e');
      return null;
    }
  }

  Future<void> _registerToken(String token) async {
    final auth = _ref.read(authControllerProvider);
    if (auth.status != AuthStatus.authenticated) return;

    currentToken = token;
    final client = _ref.read(apiClientProvider);
    try {
      await client.dio.post('/mobile/devices', data: {
        'device_id': await _deviceId(),
        'platform': _platform,
        'push_token': token,
        'app_version': '1.0.0',
      });
    } on Exception catch (e) {
      debugPrint('[push] device registration failed: $e');
    }
  }

  /// Removes the backend device record (logout) and stops listeners.
  Future<void> tearDown() async {
    await _tokenRotationSub?.cancel();
    await _foregroundSub?.cancel();
    _tokenRotationSub = null;
    _foregroundSub = null;
    _initialized = false;
    currentToken = null;
    // No banners from a dead session.
    _ref.read(pushBannerControllerProvider.notifier).clearAll();
    try {
      final client = _ref.read(apiClientProvider);
      await client.dio.delete('/mobile/devices/${await _deviceId()}');
    } on Exception catch (_) {
      // Best-effort: local sign-out proceeds regardless.
    }
  }

  String get _platform {
    if (kIsWeb) return 'web';
    if (Platform.isAndroid) return 'android';
    if (Platform.isIOS) return 'ios';
    return 'other';
  }
}

/// Lazy push service provider: initialized by the app shell once the user
/// is authenticated.
final pushNotificationServiceProvider = Provider<PushNotificationService>(
  (ref) => PushNotificationService(ref),
);
