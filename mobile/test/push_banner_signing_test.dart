import 'dart:typed_data';

import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/core/notifications/push_banner_actions.dart';
import 'package:contractos_mobile/core/notifications/push_banner_controller.dart';
import 'package:contractos_mobile/core/notifications/push_banner_overlay.dart';
import 'package:contractos_mobile/core/security/biometric_auth.dart';
import 'package:contractos_mobile/features/signing/presentation/signing_entry_page.dart';
import 'package:dio/dio.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Adapter that fails every request immediately — no sockets, no pending
/// timers — for tests that push SigningEntryPage with a token.
class _ImmediateErrorAdapter implements HttpClientAdapter {
  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    throw DioException.connectionError(
      requestOptions: options,
      reason: 'offline test adapter',
    );
  }
}

ApiClient _offlineClient() {
  final dio = Dio()..httpClientAdapter = _ImmediateErrorAdapter();
  return ApiClient(dio: dio);
}

class _FakeBiometricAuth implements BiometricAuth {
  final bool available = true;
  bool result = true;
  int authenticateCalls = 0;
  String? lastReason;

  @override
  Future<bool> isAvailable() async => available;

  @override
  Future<bool> authenticate(String reason) async {
    authenticateCalls++;
    lastReason = reason;
    return result;
  }
}

class _RecordingHandler implements PushBannerActionHandler {
  PushBanner? opened;

  @override
  void openSigningRequest(PushBanner banner) => opened = banner;
}

RemoteMessage _remote(
  String title, {
  Map<String, dynamic> data = const {},
  String? body,
}) {
  return RemoteMessage(
    senderId: 's',
    messageId: 'm-$title',
    data: {'title': title, 'body': body ?? '', ...data},
    notification: RemoteNotification(title: title, body: body ?? ''),
  );
}

void main() {
  group('PushBanner signing detection', () {
    test('type containing "sign" is a signing request', () {
      final b = PushBanner.fromRemoteMessage(_remote(
        'Signature requested',
        data: {'type': 'signature_request', 'token': 't-123'},
      ));
      expect(b.isSigningRequest, isTrue);
      expect(b.route, isNull);
    });

    test('signing_session_started type is a signing request', () {
      final b = PushBanner.fromRemoteMessage(_remote(
        'Signing session',
        data: {'notification_type': 'signing_session_started'},
      ));
      expect(b.isSigningRequest, isTrue);
    });

    test('route into /signing is a signing request', () {
      final b = PushBanner.fromRemoteMessage(_remote(
        'Sign now',
        data: {'route': '/signing/abc-token'},
      ));
      expect(b.isSigningRequest, isTrue);
      expect(b.route, '/signing/abc-token');
    });

    test('ordinary notifications are not signing requests', () {
      final b = PushBanner.fromRemoteMessage(_remote(
        'Reminder',
        data: {'type': 'obligation_reminder'},
      ));
      expect(b.isSigningRequest, isFalse);
    });

    test('route is read from route/deep_link/link aliases', () {
      expect(
        PushBanner.fromRemoteMessage(_remote('a', data: {'deep_link': '/x'}))
            .route,
        '/x',
      );
      expect(
        PushBanner.fromRemoteMessage(_remote('b', data: {'link': '/y'})).route,
        '/y',
      );
      expect(
        PushBanner.fromRemoteMessage(_remote('c')).route,
        isNull,
      );
    });
  });

  group('NavigatorPushBannerActionHandler (real navigation)', () {
    testWidgets('data token pushes SigningEntryPage with that token',
        (tester) async {
      final handler = NavigatorPushBannerActionHandler();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [apiClientProvider.overrideWithValue(_offlineClient())],
          child: MaterialApp(
            navigatorKey: rootNavigatorKey,
            home: const Scaffold(body: Text('Home')),
          ),
        ),
      );

      handler.openSigningRequest(PushBanner(
        id: 'b',
        title: 'Sign',
        body: '',
        data: {'type': 'signature_request', 'token': 'tok-42'},
      ));
      await tester.pumpAndSettle();

      expect(find.byType(SigningEntryPage), findsOneWidget);
    });

    testWidgets("route deep link's last segment becomes the token",
        (tester) async {
      final handler = NavigatorPushBannerActionHandler();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [apiClientProvider.overrideWithValue(_offlineClient())],
          child: MaterialApp(
            navigatorKey: rootNavigatorKey,
            home: const Scaffold(body: Text('Home')),
          ),
        ),
      );

      handler.openSigningRequest(PushBanner(
        id: 'b',
        title: 'Sign',
        body: '',
        data: {'route': '/signing/link-token'},
      ));
      await tester.pumpAndSettle();

      expect(find.byType(SigningEntryPage), findsOneWidget);
      // The entry page shows its "ready" state only when it holds a token.
      expect(find.text('Continue to signing'), findsOneWidget);
    });

    testWidgets('missing token still opens the entry page (link-required state)',
        (tester) async {
      final handler = NavigatorPushBannerActionHandler();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [apiClientProvider.overrideWithValue(_offlineClient())],
          child: MaterialApp(
            navigatorKey: rootNavigatorKey,
            home: const Scaffold(body: Text('Home')),
          ),
        ),
      );

      handler.openSigningRequest(PushBanner(
        id: 'b',
        title: 'Sign',
        body: '',
        data: {'type': 'signature_request'},
      ));
      await tester.pumpAndSettle();

      expect(find.byType(SigningEntryPage), findsOneWidget);
      expect(find.text('No signing link provided.'), findsOneWidget);
    });
  });

  group('Signing banner overlay flow', () {
    late _FakeBiometricAuth auth;
    late _RecordingHandler handler;
    late ProviderContainer container;

    setUp(() {
      auth = _FakeBiometricAuth();
      handler = _RecordingHandler();
      container = ProviderContainer(overrides: [
        biometricAuthProvider.overrideWithValue(auth),
        pushBannerActionHandlerProvider.overrideWithValue(handler),
      ]);
      addTearDown(container.dispose);
    });

    Future<void> pump(WidgetTester tester) async {
      // The SAME container the tests write banner state into — a fresh
      // ProviderScope would build its own and never see the queue. The
      // overlay is mounted via builder exactly like the real app.
      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            navigatorKey: rootNavigatorKey,
            builder: (context, child) => Stack(
              children: [
                ?child,
                const Positioned(
                  top: 0,
                  left: 0,
                  right: 0,
                  child: PushBannerOverlay(),
                ),
              ],
            ),
            home: const Scaffold(body: Text('Underlying page')),
          ),
        ),
      );
      await tester.pump();
    }

    void showSigningBanner() {
      container
          .read(pushBannerControllerProvider.notifier)
          .showFromRemoteMessage(_remote(
            'Signature requested',
            data: {'type': 'signature_request', 'token': 'tok-1'},
          ));
    }

    void showPlainBanner() {
      container
          .read(pushBannerControllerProvider.notifier)
          .showFromRemoteMessage(_remote(
            'Obligation due',
            data: {'type': 'obligation_reminder'},
          ));
    }

    testWidgets('signing banner shows Open + Dismiss with fingerprint icon',
        (tester) async {
      showSigningBanner();
      await pump(tester);

      expect(find.text('Signature requested'), findsOneWidget);
      expect(find.text('Open'), findsOneWidget);
      expect(find.byIcon(Icons.fingerprint), findsOneWidget);
      expect(find.byIcon(Icons.draw_outlined), findsOneWidget);
      container.read(pushBannerControllerProvider.notifier).clearAll();
    });

    testWidgets(
        'successful fingerprint notifies the handler and dismisses the banner',
        (tester) async {
      showSigningBanner();
      await pump(tester);

      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();

      expect(auth.authenticateCalls, 1);
      expect(auth.lastReason, contains('signing request'));
      expect(handler.opened?.id, 'm-Signature requested');
      expect(container.read(pushBannerControllerProvider), isEmpty);
      container.read(pushBannerControllerProvider.notifier).clearAll();
    });

    testWidgets('failed fingerprint keeps the banner and does not navigate',
        (tester) async {
      showSigningBanner();
      await pump(tester);

      auth.result = false;
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();

      expect(auth.authenticateCalls, 1);
      expect(handler.opened, isNull);
      expect(find.byType(SigningEntryPage), findsNothing);
      // Banner still present for retry.
      expect(find.text('Signature requested'), findsOneWidget);
      expect(find.text('Open'), findsOneWidget);
      container.read(pushBannerControllerProvider.notifier).clearAll();
    });

    testWidgets('plain banner has no Open action and needs no biometrics',
        (tester) async {
      showPlainBanner();
      await pump(tester);

      expect(find.text('Open'), findsNothing);
      expect(find.byIcon(Icons.fingerprint), findsNothing);
      expect(find.text('Dismiss'), findsOneWidget);
      container.read(pushBannerControllerProvider.notifier).clearAll();
    });
  });
}
