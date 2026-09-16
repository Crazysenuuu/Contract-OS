import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/signing/presentation/signing_entry_page.dart';
import '../notifications/push_banner_actions.dart';

/// Deep-link handling (spec 2.02 §23): https:// links (iOS universal links,
/// Android app links) and custom schemes land here. Recognised routes:
///
///   /signing/`<token>`  -> SigningEntryPage (one-time signer token)
///
/// Signing is the highest-risk navigation target so, like signing push
/// banners, the entry page itself enforces the biometric gate before any
/// signature action. Unknown routes are ignored rather than crashing.
class DeepLinkService {
  DeepLinkService({AppLinks? links}) : _links = links ?? AppLinks();

  final AppLinks _links;
  StreamSubscription<Uri>? _sub;
  bool _started = false;

  /// Idempotent: restarts (e.g. after a full sign-out) are safe.
  Future<void> start() async {
    if (_started) return;
    _started = true;
    try {
      // Cold start: a link that launched the app.
      final initial = await _links.getInitialLink();
      if (initial != null) handleUri(initial);
      // Warm starts: links while the app is running.
      _sub = _links.uriLinkStream.listen(handleUri, onError: (_) {});
    } catch (_) {
      _started = false; // allow retry on next start()
    }
  }

  void dispose() {
    _sub?.cancel();
    _sub = null;
    _started = false;
  }

  /// Testable core: maps a URI to an action. Public so tests can drive
  /// routing without platform channels.
  ///
  /// Route shapes differ by link style:
  ///   https://host/signing/`<token>`  -> path segments [signing, token]
  ///   contractos://signing/`<token>`  -> host 'signing', segments [token]
  /// so custom-scheme links read the token from [Uri.host].
  void handleUri(Uri uri) {
    final isHttps = uri.scheme == 'https' || uri.scheme == 'http';
    final signing = isHttps
        ? uri.pathSegments.length >= 2 && uri.pathSegments[0] == 'signing'
        : uri.host == 'signing';
    if (signing) {
      final token = isHttps
          ? uri.pathSegments[1]
          : uri.host == 'signing'
          ? uri.pathSegments.first
          : null;
      if (token != null && token.isNotEmpty) _openSigning(token);
    }
  }

  void _openSigning(String token) {
    final navigator = rootNavigatorKey.currentState;
    if (navigator == null) return; // app not booted yet; drop silently
    navigator.push(
      MaterialPageRoute<void>(builder: (_) => SigningEntryPage(token: token)),
    );
  }
}

final deepLinkServiceProvider = Provider<DeepLinkService>((ref) {
  final service = DeepLinkService();
  ref.onDispose(service.dispose);
  return service;
});

/// Starts deep-link handling once the shell is mounted so links that arrive
/// during boot are not lost.
final deepLinkStartProvider = Provider<void>((ref) {
  final service = ref.watch(deepLinkServiceProvider);
  unawaited(service.start());
});
