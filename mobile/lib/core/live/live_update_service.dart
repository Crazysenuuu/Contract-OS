import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

/// Live-update event delivered over /ws/notifications (spec 1.14, 2.02 §30).
class LiveEvent {
  LiveEvent({
    required this.eventId,
    required this.eventType,
    required this.notificationType,
    required this.title,
    required this.body,
    this.agreementId,
    this.route,
    this.token,
    DateTime? receivedAt,
  }) : receivedAt = receivedAt ?? DateTime.now().toUtc();

  final String eventId;
  final String eventType;
  final String notificationType;
  final String title;
  final String body;
  final String? agreementId;
  final String? route;

  /// One-time signer token for signing-request events (biometric step-up).
  final String? token;
  final DateTime receivedAt;

  /// Signing requests get biometric step-up before opening (spec §23),
  /// mirroring the push-banner contract.
  bool get isSigningRequest =>
      notificationType.toLowerCase().contains('sign') ||
      (route?.toLowerCase().startsWith('/sign') ?? false);

  static LiveEvent fromSocketMessage(Map<String, dynamic> json) {
    final payload = (json['payload'] as Map<String, dynamic>?) ?? const {};
    return LiveEvent(
      eventId: json['event_id']?.toString() ?? '',
      eventType: json['event_type']?.toString() ?? '',
      notificationType: json['notification_type']?.toString() ?? '',
      title: json['subject']?.toString() ?? 'Notification',
      body: payload['reason']?.toString() ??
          payload['agreement_title']?.toString() ??
          '',
      agreementId: payload['agreement_id']?.toString(),
      route: payload['route']?.toString(),
      token: payload['token']?.toString(),
      receivedAt: DateTime.now().toUtc(),
    );
  }

  /// Map a REST notification row (missed-event recovery) into a LiveEvent.
  static LiveEvent fromNotificationRow(Map<String, dynamic> json) {
    final meta = (json['metadata_'] as Map<String, dynamic>?) ?? const {};
    final inner = (meta['payload'] as Map<String, dynamic>?) ?? const {};
    return LiveEvent(
      eventId: meta['outbox_event_id']?.toString() ?? json['id'].toString(),
      eventType: meta['event_type']?.toString() ?? '',
      notificationType: json['notification_type']?.toString() ?? '',
      title: json['subject']?.toString() ?? 'Notification',
      body: inner['reason']?.toString() ??
          inner['agreement_title']?.toString() ??
          '',
      agreementId: json['agreement_id']?.toString(),
      route: inner['route']?.toString(),
      token: inner['token']?.toString(),
      receivedAt:
          DateTime.tryParse(json['created_at']?.toString() ?? '')?.toUtc(),
    );
  }}

/// Transport seam for the WebSocket so tests can inject a fake channel.
abstract class WebSocketChannelFactory {
  WebSocketChannel connect(Uri uri);
}

class DefaultWebSocketChannelFactory implements WebSocketChannelFactory {
  const DefaultWebSocketChannelFactory();

  @override
  WebSocketChannel connect(Uri uri) => WebSocketChannel.connect(uri);
}

/// Live-update connection service (spec 2.02 §30-33).
///
/// Responsibilities:
///   - connect to /ws/notifications with the current access token
///   - expose a broadcast stream of [LiveEvent]s
///   - ping/pong keepalive every [pingInterval]
///   - reconnect with capped exponential backoff + jitter on drops
///   - on reconnect, fetch missed events via REST (`/notifications?since=`)
///     so events that fired while offline are replayed exactly once-ish
///     (WebSocket duplicates are suppressed by [LiveEvent.eventId]).
///
/// Reconnect is *deliberate*: callers trigger [start] (auth live) and
/// [stop] (signed out); internal retries only fire while [started].
class LiveUpdateService {
  LiveUpdateService({
    required this.baseUrl,
    required this.tokenProvider,
    WebSocketChannelFactory? channelFactory,
    this.pingInterval = const Duration(seconds: 30),
    this.initialBackoff = const Duration(seconds: 1),
    this.maxBackoff = const Duration(seconds: 60),
  }) : _channelFactory = channelFactory ?? const DefaultWebSocketChannelFactory();

  final String baseUrl;
  final Future<String?> Function() tokenProvider;
  final WebSocketChannelFactory _channelFactory;
  final Duration pingInterval;
  final Duration initialBackoff;
  final Duration maxBackoff;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _socketSub;
  StreamSubscription<dynamic>? _pongSub;
  Timer? _pingTimer;
  Timer? _reconnectTimer;
  final Set<String> _seenEventIds = {};
  DateTime? _lastEventAt;
  int _backoffAttempt = 0;
  bool _started = false;
  bool _firstConnectSinceStart = true;

  final _eventsController = StreamController<LiveEvent>.broadcast();
  final _stateController = StreamController<LiveConnectionState>.broadcast();

  /// Live events (socket + replayed missed events), deduped by event id.
  Stream<LiveEvent> get events => _eventsController.stream;

  /// Connection state transitions, for diagnostics/badges.
  Stream<LiveConnectionState> get state => _stateController.stream;

  LiveConnectionState _currentState = LiveConnectionState.disconnected;
  LiveConnectionState get currentState => _currentState;

  /// Fetch missed events over REST. Exposed for tests; called
  /// automatically on every reconnect (not before the first connect).
  Future<List<LiveEvent>> Function(DateTime since)? fetchMissedEvents;

  void _setState(LiveConnectionState s) {
    if (_currentState == s) return;
    _currentState = s;
    if (!_stateController.isClosed) _stateController.add(s);
  }

  Future<void> start() async {
    if (_started) return;
    _started = true;
    _firstConnectSinceStart = true;
    await _connect();
  }

  Future<void> stop() async {
    _started = false;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    await _socketSub?.cancel();
    await _pongSub?.cancel();
    _socketSub = null;
    _pongSub = null;
    await _channel?.sink.close(1000, 'client-stop');
    _channel = null;
    _setState(LiveConnectionState.disconnected);
  }

  Future<void> _connect() async {
    if (!_started) return;
    final token = await tokenProvider();
    if (token == null || token.isEmpty) {
      _setState(LiveConnectionState.disconnected);
      return;
    }

    final wsUrl = baseUrl
        .replaceFirst(RegExp(r'^http'), 'ws')
        .replaceFirst(RegExp(r'/api/v1/?$'), '');
    final uri = Uri.parse('$wsUrl/ws/notifications?token=$token');

    _setState(LiveConnectionState.connecting);
    try {
      final channel = _channelFactory.connect(uri);
      _channel = channel;

      // Capture the socket stream before awaiting ready (the ready future
      // never completes against some servers in tests).
      _pongSub = channel.stream.listen(
        _onSocketData,
        onDone: _onDisconnected,
        onError: (_) => _onDisconnected(),
        cancelOnError: true,
      );

      // Backfill missed events BEFORE listening to the socket to avoid a
      // race: replayed + socket duplicates are suppressed by event id.
      if (!_firstConnectSinceStart) {
        await _replayMissedEvents();
      }
      _firstConnectSinceStart = false;

      _socketSub = Stream<void>.periodic(pingInterval)
          .listen((_) => _sendPing());
      _sendPing();

      _backoffAttempt = 0;
      _setState(LiveConnectionState.connected);
    } catch (_) {
      _onDisconnected();
    }
  }

  void _onSocketData(dynamic raw) {
    try {
      final json = jsonDecode(raw.toString()) as Map<String, dynamic>;
      final type = json['type']?.toString();
      if (type == 'pong' || type == 'stats') return; // control frames
      final event = LiveEvent.fromSocketMessage(json);
      _deliver(event);
    } on FormatException {
      // Malformed frame — ignore; the connection stays up.
    }
  }

  void _sendPing() {
    final sink = _channel?.sink;
    if (sink != null) {
      try {
        sink.add('ping');
      } catch (_) {
        _onDisconnected();
      }
    }
  }

  void _deliver(LiveEvent event) {
    if (event.eventId.isNotEmpty) {
      if (!_seenEventIds.add(event.eventId)) return; // duplicate
      // Bound the dedupe set.
      if (_seenEventIds.length > 500) {
        _seenEventIds.remove(_seenEventIds.first);
      }
    }
    _lastEventAt = _latest(_lastEventAt, event.receivedAt);
    if (!_eventsController.isClosed) _eventsController.add(event);
  }

  DateTime? _latest(DateTime? a, DateTime? b) {
    if (a == null) return b;
    if (b == null) return a;
    return b.isAfter(a) ? b : a;
  }

  Future<void> _replayMissedEvents() async {
    final fetch = fetchMissedEvents;
    if (fetch == null) return;
    final since = _lastEventAt ??
        DateTime.now().toUtc().subtract(const Duration(hours: 1));
    try {
      final missed = await fetch(since);
      // Oldest first so ordering matches wall-clock.
      final sorted = [...missed]..sort(
          (a, b) => a.receivedAt.compareTo(b.receivedAt),
        );
      for (final e in sorted) {
        _deliver(e);
      }
    } catch (_) {
      // Recovery is best-effort; the live socket still delivers.
    }
  }

  void _onDisconnected() {
    _pingTimer?.cancel();
    _pingTimer = null;
    _channel = null;
    if (!_started) {
      _setState(LiveConnectionState.disconnected);
      return;
    }
    _setState(LiveConnectionState.reconnecting);
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_reconnectTimer != null) return;
    // Capped exponential backoff with proportional jitter:
    // base, 2x base, 4x base, ... up to maxBackoff. Jitter stays within
    // half a step so it never dominates the schedule.
    final baseMs =
        initialBackoff.inMilliseconds * (1 << _backoffAttempt.clamp(0, 6));
    final jitterMs = DateTime.now().microsecondsSinceEpoch % (baseMs ~/ 2 + 1);
    final delayMs = (baseMs + jitterMs).clamp(0, maxBackoff.inMilliseconds);
    _backoffAttempt++;
    _reconnectTimer = Timer(Duration(milliseconds: delayMs), () async {
      _reconnectTimer = null;
      if (_started) {
        await _connect();
      } else {
        _setState(LiveConnectionState.disconnected);
      }
    });
  }
}

enum LiveConnectionState { disconnected, connecting, connected, reconnecting }
