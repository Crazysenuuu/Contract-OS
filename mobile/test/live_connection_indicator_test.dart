import 'dart:async';

import 'package:contractos_mobile/core/live/live_update_providers.dart';
import 'package:contractos_mobile/core/live/live_update_service.dart';
import 'package:contractos_mobile/features/shell/live_connection_indicator.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'live_update_service_test.dart' show FakeFactory;

/// Drives the indicator through a controller-backed stream instead of a real
/// [LiveUpdateService]: awaiting service lifecycle calls inside testWidgets
/// hangs in the fake-async zone, and widget tests don't need sockets —
/// the provider is the seam.
class _Harness {
  final controller = StreamController<LiveConnectionState>();
  late final Widget widget;

  _Harness() {
    widget = ProviderScope(
      overrides: [
        liveConnectionStateProvider.overrideWith((ref) => controller.stream),
      ],
      child: const MaterialApp(
        home: Scaffold(body: LiveConnectionIndicator()),
      ),
    );
  }

  void emit(LiveConnectionState state) => controller.add(state);
}

Future<void> _pump(WidgetTester tester, _Harness h) async {
  await tester.pumpWidget(h.widget);
  await tester.pump();
}

void main() {
  group('indicator widget', () {
    testWidgets('connected session renders the quiet Live chip',
        (tester) async {
      final h = _Harness()..emit(LiveConnectionState.connected);
      addTearDown(h.controller.close);

      await _pump(tester, h);

      expect(find.text('Live'), findsOneWidget);
      expect(find.text('Reconnecting…'), findsNothing);
      expect(find.text('Offline'), findsNothing);
    });

    testWidgets('no data yet falls back to Offline', (tester) async {
      final h = _Harness(); // provider still loading -> value is null
      addTearDown(h.controller.close);

      await _pump(tester, h);

      expect(find.text('Offline'), findsOneWidget);
      expect(find.text('Live'), findsNothing);
    });

    testWidgets('reconnecting renders error-styled label', (tester) async {
      final h = _Harness()..emit(LiveConnectionState.reconnecting);
      addTearDown(h.controller.close);

      await _pump(tester, h);

      final context = tester.element(find.text('Reconnecting…'));
      final label = tester.widget<Text>(find.text('Reconnecting…'));
      expect(label.style?.color, Theme.of(context).colorScheme.error);
    });

    testWidgets('connecting renders the connecting label', (tester) async {
      final h = _Harness()..emit(LiveConnectionState.connecting);
      addTearDown(h.controller.close);

      await _pump(tester, h);

      expect(find.text('Connecting…'), findsOneWidget);
    });

    testWidgets('follows state transitions live→reconnecting→live',
        (tester) async {
      final h = _Harness();
      addTearDown(h.controller.close);

      await tester.pumpWidget(h.widget);
      await tester.pump();

      // Two pumps per emit: the stream delivery runs on microtasks and the
      // rebuild lands on the frame after that.
      h.emit(LiveConnectionState.connected);
      await tester.pump();
      await tester.pump();
      expect(find.text('Live'), findsOneWidget);

      h.emit(LiveConnectionState.reconnecting);
      await tester.pump();
      await tester.pump();
      expect(find.text('Reconnecting…'), findsOneWidget);
      expect(find.text('Live'), findsNothing);

      h.emit(LiveConnectionState.connected);
      await tester.pump();
      await tester.pump();
      expect(find.text('Live'), findsOneWidget);
      expect(find.text('Reconnecting…'), findsNothing);
    });

    testWidgets('disconnected (signed out) renders Offline', (tester) async {
      final h = _Harness()..emit(LiveConnectionState.disconnected);
      addTearDown(h.controller.close);

      await _pump(tester, h);

      expect(find.text('Offline'), findsOneWidget);
    });
  });

  // The provider bridge runs as plain Dart: awaiting service lifecycle
  // calls inside testWidgets hangs in the fake-async zone, but the bridge
  // is pure stream plumbing that needs no widget tree.
  group('provider bridge', () {
    late FakeFactory factory;
    late LiveUpdateService service;
    late ProviderContainer container;

    setUp(() {
      factory = FakeFactory();
      service = LiveUpdateService(
        baseUrl: 'http://api.example.com/api/v1',
        tokenProvider: () async => 'jwt',
        channelFactory: factory,
        initialBackoff: const Duration(milliseconds: 10),
        maxBackoff: const Duration(milliseconds: 50),
      );
      container = ProviderContainer(
        overrides: [liveUpdateServiceProvider.overrideWithValue(service)],
      );
    });

    tearDown(() async {
      await service.stop();
      container.dispose();
    });

    /// Drains the microtask hops (stream -> async* -> provider) so reads
    /// see settled values.
    Future<void> settle() async {
      await Future<void>.delayed(Duration.zero);
      await container.pump();
    }

    test('seeds currentState without waiting for a transition', () async {
      final sub = container.listen(liveConnectionStateProvider, (_, _) {});
      addTearDown(sub.close);

      await settle();
      // Service never started: the seed itself must be disconnected.
      expect(sub.read().value, LiveConnectionState.disconnected);
    });

    test('follows transitions: connected after start, offline after stop',
        () async {
      final states = <LiveConnectionState>[];
      final sub = container.listen(
        liveConnectionStateProvider,
        (prev, next) {
          final v = next.value;
          if (v != null) states.add(v);
        },
      );
      addTearDown(sub.close);

      await service.start();
      await settle();
      expect(sub.read().value, LiveConnectionState.connected);

      await service.stop();
      await settle();
      expect(sub.read().value, LiveConnectionState.disconnected);

      expect(
        states,
        containsAllInOrder([
          LiveConnectionState.connected,
          LiveConnectionState.disconnected,
        ]),
      );
    });
  });
}
