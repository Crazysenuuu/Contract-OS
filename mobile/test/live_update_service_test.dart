import 'dart:async';
import 'dart:convert';

import 'package:contractos_mobile/core/live/live_update_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// Scriptable in-memory WebSocket channel: tests push server frames via
/// [serverAdd] and can close the socket to simulate drops.
class FakeChannel extends StreamChannelMixin<dynamic>
    implements WebSocketChannel {
  FakeChannel(this.uri);

  final Uri uri;
  final _controller = StreamController<dynamic>.broadcast();
  final serverToClient = <dynamic>[];
  final clientToServer = <dynamic>[];
  final sentFrames = _FakeSink();
  bool closed = false;
  @override
  int? closeCode;

  WebSocketSink? _sinkOverride;

  @override
  Future<void> get ready => Future.value();

  @override
  Stream<dynamic> get stream => _controller.stream;

  @override
  WebSocketSink get sink => _sinkOverride ?? sentFrames;

  void serverAdd(dynamic data) {
    serverToClient.add(data);
    _controller.add(data);
  }

  /// Simulates a network drop without a clean close frame.
  void serverDrop() {
    _controller
      ..addError(StateError('connection reset'))
      ..close();
  }

  Future<void> closeChannel([int? code, String? reason]) async {
    closed = true;
    await _controller.close();
  }

  @override
  String? get closeReason => null;

  @override
  String? get protocol => null;
}

class _FakeSink implements WebSocketSink {
  final lines = <dynamic>[];

  @override
  void add(dynamic data) => lines.add(data);

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}

  @override
  Future<void> addStream(Stream<dynamic> stream) async {}

  @override
  Future<void> close([int? closeCode, String? closeReason]) async {}

  @override
  Future<void> get done => Future.value();
}

class FakeFactory implements WebSocketChannelFactory {
  final channels = <FakeChannel>[];

  /// URIs the client tried to connect to, in order.
  final uris = <Uri>[];

  /// When non-null, the next connect() throws (server unreachable).
  Object? throwNext;

  @override
  WebSocketChannel connect(Uri uri) {
    uris.add(uri);
    if (throwNext != null) {
      throw throwNext!;
    }
    final ch = FakeChannel(uri);
    channels.add(ch);
    return ch;
  }
}

void main() {
  late FakeFactory factory;
  late LiveUpdateService service;
  late List<LiveEvent> events;
  late StreamSubscription<LiveEvent> sub;

  setUp(() {
    factory = FakeFactory();
    service = LiveUpdateService(
      baseUrl: 'http://api.example.com/api/v1',
      tokenProvider: () async => 'jwt-token',
      channelFactory: factory,
      pingInterval: const Duration(seconds: 30),
      initialBackoff: const Duration(milliseconds: 10),
      maxBackoff: const Duration(milliseconds: 50),
    );
    events = [];
    sub = service.events.listen(events.add);
  });

  tearDown(() async {
    await sub.cancel();
    await service.stop();
  });

  group('connection', () {
    test('connects with token query and http->ws upgrade', () async {
      await service.start();

      expect(factory.uris, hasLength(1));
      final uri = factory.uris.single;
      expect(uri.scheme, 'ws');
      expect(uri.path, '/ws/notifications');
      expect(uri.queryParameters['token'], 'jwt-token');
      expect(service.currentState, LiveConnectionState.connected);
    });

    test('does not connect without a token', () async {
      service = LiveUpdateService(
        baseUrl: 'http://api.example.com/api/v1',
        tokenProvider: () async => null,
        channelFactory: factory,
      );
      await service.start();
      expect(factory.uris, isEmpty);
      expect(service.currentState, LiveConnectionState.disconnected);
    });

    test('start is idempotent', () async {
      await service.start();
      await service.start();
      expect(factory.uris, hasLength(1));
    });
  });

  group('event stream', () {
    test('parses socket frames into LiveEvents', () async {
      await service.start();
      factory.channels.first.serverAdd(jsonEncode({
        'event_id': 'e-1',
        'event_type': 'signature.requested',
        'notification_type': 'signature_request',
        'subject': 'Signature requested',
        'payload': {
          'agreement_id': 'a-1',
          'reason': 'MSA v2 awaits signature',
          'token': 'st-42',
          'route': '/signing/st-42',
        },
      }));

      await Future<void>.delayed(Duration.zero);
      expect(events, hasLength(1));
      final e = events.single;
      expect(e.eventId, 'e-1');
      expect(e.notificationType, 'signature_request');
      expect(e.title, 'Signature requested');
      expect(e.body, 'MSA v2 awaits signature');
      expect(e.agreementId, 'a-1');
      expect(e.token, 'st-42');
      expect(e.route, '/signing/st-42');
      expect(e.isSigningRequest, isTrue);
    });

    test('ordinary events are not signing requests', () async {
      await service.start();
      factory.channels.first.serverAdd(jsonEncode({
        'event_id': 'e-2',
        'event_type': 'obligation.reminder',
        'notification_type': 'obligation_reminder',
        'subject': 'Obligation due',
        'payload': {},
      }));
      await Future<void>.delayed(Duration.zero);
      expect(events.single.isSigningRequest, isFalse);
    });

    test('duplicate event ids are delivered once', () async {
      await service.start();
      final frame = jsonEncode({
        'event_id': 'dup-1',
        'event_type': 'x',
        'notification_type': 'workflow_transition',
        'subject': 'S',
        'payload': {},
      });
      factory.channels.first.serverAdd(frame);
      factory.channels.first.serverAdd(frame); // socket re-delivery
      await Future<void>.delayed(Duration.zero);
      expect(events, hasLength(1));
    });

    test('control frames (pong) do not produce events', () async {
      await service.start();
      factory.channels.first
          .serverAdd(jsonEncode({'type': 'pong', 'ts': 'now'}));
      await Future<void>.delayed(Duration.zero);
      expect(events, isEmpty);
    });

    test('sends ping keepalives on the interval', () async {
      service = LiveUpdateService(
        baseUrl: 'http://api.example.com/api/v1',
        tokenProvider: () async => 'jwt',
        channelFactory: factory,
        pingInterval: const Duration(milliseconds: 5),
      );
      await service.start();
      await Future<void>.delayed(const Duration(milliseconds: 30));

      final pings = factory.channels.first.sentFrames.lines
          .where((l) => l == 'ping')
          .length;
      expect(pings, greaterThanOrEqualTo(2));
    });
  });

  group('reconnect', () {
    test('reconnects with backoff after a dropped socket', () async {
      await service.start();
      expect(factory.channels, hasLength(1));

      factory.channels.first.serverDrop();
      // Backoff is 10ms + jitter; wait generously.
      await Future<void>.delayed(const Duration(milliseconds: 120));

      expect(service.currentState, LiveConnectionState.connected);
      expect(factory.uris, hasLength(2));
      expect(service.currentState, LiveConnectionState.connected);
    });

    test('recovery fetch is skipped on first connect, used on reconnect',
        () async {
      final fetched = <DateTime>[];
      service.fetchMissedEvents = (since) async {
        fetched.add(since);
        return const <LiveEvent>[];
      };

      await service.start();
      expect(fetched, isEmpty); // first connect: nothing missed yet

      factory.channels.first.serverDrop();
      await Future<void>.delayed(const Duration(milliseconds: 120));
      expect(fetched, hasLength(1)); // reconnect: replay what was missed
    });

    test('replayed missed events are delivered oldest-first', () async {
      final now = DateTime.now().toUtc();
      service.fetchMissedEvents = (since) async {
        return [
          LiveEvent(
            eventId: 'missed-2',
            eventType: 'e',
            notificationType: 'workflow_transition',
            title: 'Second',
            body: '',
            receivedAt: now.add(const Duration(seconds: 5)),
          ),
          LiveEvent(
            eventId: 'missed-1',
            eventType: 'e',
            notificationType: 'workflow_transition',
            title: 'First',
            body: '',
            receivedAt: now,
          ),
        ];
      };

      await service.start();
      factory.channels.first.serverDrop();
      await Future<void>.delayed(const Duration(milliseconds: 120));

      final titles = events.map((e) => e.title).toList();
      expect(titles, containsAllInOrder(['First', 'Second']));
    });

    test('replayed events dedupe against live socket duplicates', () async {
      service.fetchMissedEvents = (since) async {
        return [
          LiveEvent(
            eventId: 'shared-1',
            eventType: 'e',
            notificationType: 'workflow_transition',
            title: 'From REST',
            body: '',
          ),
        ];
      };

      await service.start();
      factory.channels.first.serverDrop(); // triggers replay of shared-1
      await Future<void>.delayed(const Duration(milliseconds: 120));

      // The new socket also delivers shared-1 — must be suppressed.
      factory.channels.last.serverAdd(jsonEncode({
        'event_id': 'shared-1',
        'event_type': 'e',
        'notification_type': 'workflow_transition',
        'subject': 'From socket',
        'payload': {},
      }));
      await Future<void>.delayed(Duration.zero);

      expect(events, hasLength(1));
      expect(events.single.title, 'From REST');
    });

    test('unreachable server retries until it connects', () async {
      factory.throwNext = SocketExceptionFake();
      await service.start();
      expect(factory.uris, hasLength(1)); // failed attempt
      expect(service.currentState, LiveConnectionState.reconnecting);

      // Retry chain doubles backoff (10, 20, 40, 80ms + jitter); wait for
      // enough cumulative time for a couple of attempts, then succeed.
      factory.throwNext = null;
      await Future<void>.delayed(const Duration(milliseconds: 150));
      expect(service.currentState, LiveConnectionState.connected);
      expect(factory.channels, hasLength(1));
    });

    test('stop cancels pending reconnects and closes the socket', () async {
      factory.throwNext = SocketExceptionFake();
      await service.start();

      await service.stop();
      await Future<void>.delayed(const Duration(milliseconds: 120));

      expect(service.currentState, LiveConnectionState.disconnected);
      // Only the initial failed attempt; no reconnect after stop.
      expect(factory.uris, hasLength(1));
    });
  });

  group('notification row mapping', () {
    test('fromNotificationRow maps the REST recovery shape', () {
      final e = LiveEvent.fromNotificationRow({
        'id': 'n-1',
        'notification_type': 'signature_request',
        'subject': 'Sign this',
        'agreement_id': 'a-9',
        'created_at': '2026-09-09T10:00:00.000Z',
        'metadata_': {
          'outbox_event_id': 'evt-55',
          'event_type': 'signature.requested',
          'payload': {'token': 'st-9', 'route': '/signing/st-9'},
        },
      });
      expect(e.eventId, 'evt-55');
      expect(e.notificationType, 'signature_request');
      expect(e.title, 'Sign this');
      expect(e.token, 'st-9');
      expect(e.isSigningRequest, isTrue);
      expect(e.receivedAt, isNotNull);
    });
  });
}

/// SocketException stand-in (avoids dart:io import in the whole test).
class SocketExceptionFake implements Exception {}
