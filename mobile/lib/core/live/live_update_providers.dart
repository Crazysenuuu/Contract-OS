import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/auth_controller.dart';
import '../network/api_client.dart';
import '../notifications/push_banner_controller.dart';
import 'live_update_service.dart';

/// REST backfill for events missed while disconnected (spec 2.02 §33):
/// GET /notifications with a `since` timestamp. Rows are mapped into the same
/// LiveEvent shape as socket frames; duplicates are suppressed by the
/// service's event-id dedupe.
Future<List<LiveEvent>> fetchMissedEventsOverRest(
  Ref ref,
  DateTime since,
) async {
  final dio = ref.read(apiClientProvider).dio;
  final res = await dio.get<List<dynamic>>(
    '/notifications',
    queryParameters: {
      'since': since.toIso8601String(),
      'limit': 100,
    },
  );
  final rows = res.data ?? const <dynamic>[];
  return [
    for (final row in rows)
      LiveEvent.fromNotificationRow(row as Map<String, dynamic>),
  ];
}

final liveUpdateServiceProvider = Provider<LiveUpdateService>((ref) {
  final service = LiveUpdateService(
    baseUrl: ref.watch(apiClientProvider).dio.options.baseUrl,
    tokenProvider: () async {
      // Reuse the auth stack: the secure store holds the current token.
      return ref.read(secureTokenStoreProvider).readAccessToken();
    },
  );

  // Recover missed events on reconnect via REST.
  service.fetchMissedEvents = (since) => fetchMissedEventsOverRest(ref, since);

  // Bridge live events into the banner queue; signing requests keep the
  // token/route fields so the biometric step-up still applies.
  final sub = service.events.listen((event) {
    ref.read(pushBannerControllerProvider.notifier).show(
          PushBanner(
            id: event.eventId,
            title: event.title,
            body: event.body,
            data: {
              if (event.route != null) 'route': event.route!,
              if (event.token != null) 'token': event.token!,
              'type': event.notificationType,
            },
          ),
        );
  });
  ref.onDispose(() => sub.cancel());

  return service;
});

/// Starts/stops the live connection with the auth session and re-locks
/// the connection when the app is backgrounded past the socket timeout.
final liveUpdateLifecycleProvider = Provider<void>((ref) {
  final auth = ref.watch(authControllerProvider);
  final service = ref.watch(liveUpdateServiceProvider);

  if (auth.status == AuthStatus.authenticated) {
    // Microtask: never start network work during provider build.
    Future.microtask(() => service.start());
  } else if (auth.status == AuthStatus.unauthenticated) {
    Future.microtask(() => service.stop());
  }
});

/// Connection state for UI (e.g. the shell's live-status chip).
///
/// Seeded at listen time from the service's authoritative `currentState` so
/// the indicator renders the truth immediately and no transition fired
/// before the subscription existed can be lost (a plain `async*` +
/// `await for` bridge has exactly that gap: the broadcast stream drops
/// events emitted between consuming the seed and subscribing).
final liveConnectionStateProvider = StreamProvider<LiveConnectionState>((ref) {
  final service = ref.watch(liveUpdateServiceProvider);
  late final StreamController<LiveConnectionState> controller;
  final sub = service.state.listen(
    (s) => controller.add(s),
    onError: (Object e, StackTrace st) => controller.addError(e, st),
    onDone: () => controller.close(),
  );
  controller = StreamController<LiveConnectionState>(
    onListen: () => controller.add(service.currentState),
  );
  ref.onDispose(() {
    sub.cancel();
    controller.close();
  });
  return controller.stream;
});
