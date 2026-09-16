import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// In-app banner queue for foreground push messages (spec 2.02 §25).
///
/// The system tray does not show notifications while the app is open, so
/// foreground FCM messages are surfaced here as stacked Material banners.
/// The queue is bounded: at most [_maxVisible] banners are shown at once
/// and the rest wait; each banner auto-dismisses after [autoDismissAfter]
/// unless the user taps it away first.
class PushBannerController extends Notifier<List<PushBanner>> {
  static const _maxVisible = 3;

  /// Per-banner auto-dismiss delay. Injectable for tests.
  static Duration autoDismissAfter = const Duration(seconds: 5);

  final Map<String, Timer> _timers = {};

  @override
  List<PushBanner> build() => const [];

  /// Adds a banner (or promotes an existing one). Safe to call from any
  /// stream; timers are owned by the controller and cancelled on removal.
  void show(PushBanner banner) {
    final current = [...state];
    // Same id (e.g. FCM collapsed-key re-delivery): move to front + refresh.
    current.removeWhere((b) => b.id == banner.id);
    current.insert(0, banner);
    while (current.length > _maxVisible) {
      current.removeLast();
    }
    state = current;
    _armAutoDismiss(banner.id);
  }

  /// Convenience mapper from a foreground FCM message.
  void showFromRemoteMessage(RemoteMessage message) {
    show(PushBanner.fromRemoteMessage(message));
  }

  void dismiss(String id) {
    _cancelTimer(id);
    state = state.where((b) => b.id != id).toList();
  }

  /// Sign-out / teardown: drop everything and cancel timers.
  void clearAll() {
    for (final t in _timers.values) {
      t.cancel();
    }
    _timers.clear();
    state = const [];
  }

  void _armAutoDismiss(String id) {
    _cancelTimer(id);
    _timers[id] = Timer(autoDismissAfter, () => dismiss(id));
  }

  void _cancelTimer(String id) {
    _timers.remove(id)?.cancel();
  }
}

/// One foreground-notification banner.
class PushBanner {
  const PushBanner({
    required this.id,
    required this.title,
    required this.body,
    this.data = const {},
  });

  final String id;
  final String title;
  final String body;
  final Map<String, dynamic> data;

  factory PushBanner.fromRemoteMessage(RemoteMessage message) {
    final notification = message.notification;
    final data = Map<String, dynamic>.from(message.data);
    return PushBanner(
      id: data['notification_id']?.toString() ??
          message.messageId ??
          'fcm-${DateTime.now().microsecondsSinceEpoch}',
      title: notification?.title ?? data['title']?.toString() ?? 'Notification',
      body: notification?.body ?? data['body']?.toString() ?? '',
      data: data,
    );
  }

  /// In-app navigation target from the push payload (e.g. '/signing/abc').
  String? get route {
    final raw = data['route'] ?? data['deep_link'] ?? data['link'];
    if (raw == null) return null;
    final value = raw.toString();
    return value.isEmpty ? null : value;
  }

  /// Signing requests get biometric step-up before their target screen
  /// opens (spec 2.02 §23): applying a legal signature is the highest-
  /// risk action a notification can lead to.
  ///
  /// Detection covers the backend notification types that involve signing
  /// ('signature_request', 'signing_session_started', ...) — any type
  /// containing 'sign' — plus anything routed into the signing flow.
  bool get isSigningRequest {
    final type =
        (data['type'] ?? data['notification_type'] ?? '').toString().toLowerCase();
    if (type.contains('sign')) return true;
    final r = route;
    return r != null && r.toLowerCase().startsWith('/sign');
  }
}

/// Performs the navigation a banner action asks for. Implemented by the
/// app shell (which owns the Navigator) and registered at boot; tests
/// inject a fake. Null = action buttons are hidden.
abstract class PushBannerActionHandler {
  /// Opens the signing flow for a [banner] that satisfies
  /// [PushBanner.isSigningRequest] and has passed biometric verification.
  void openSigningRequest(PushBanner banner);
}

final pushBannerActionHandlerProvider =
    Provider<PushBannerActionHandler?>((ref) => null);

final pushBannerControllerProvider =
    NotifierProvider<PushBannerController, List<PushBanner>>(
  PushBannerController.new,
);
