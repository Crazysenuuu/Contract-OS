import 'dart:typed_data';

import 'package:contractos_mobile/core/deeplink/deep_link_service.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/core/notifications/push_banner_actions.dart';
import 'package:contractos_mobile/features/signing/presentation/signing_entry_page.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Adapter that fails every request immediately — no sockets, no pending
/// timers. SigningEntryPage's auto-exchange fails fast into its error state.
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

/// Flushes the frames Dio's internal request timers need to finish so the
/// test teardown sees no pending timers.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  Future<ProviderContainer> pumpShell(WidgetTester tester) async {
    final container = ProviderContainer(overrides: [
      apiClientProvider.overrideWithValue(_offlineClient()),
    ]);
    addTearDown(container.dispose);
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          navigatorKey: rootNavigatorKey,
          home: const SizedBox.shrink(),
        ),
      ),
    );
    await tester.pump();
    return container;
  }

  testWidgets('deep link /signing/<token> opens the signing flow',
      (tester) async {
    final container = await pumpShell(tester);
    final service = container.read(deepLinkServiceProvider);

    service.handleUri(
      Uri.parse('https://app.contractos.example.com/signing/abc123'),
    );
    await _settle(tester);

    expect(find.byType(SigningEntryPage), findsOneWidget);
  });

  testWidgets('custom-scheme signing link opens the signing flow',
      (tester) async {
    final container = await pumpShell(tester);
    final service = container.read(deepLinkServiceProvider);

    service.handleUri(Uri.parse('contractos://signing/tok-42'));
    await _settle(tester);

    expect(find.byType(SigningEntryPage), findsOneWidget);
  });

  test('deep link ignores unknown routes', () {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final service = container.read(deepLinkServiceProvider);
    // Must not throw on unrecognised paths; no navigator is mounted so the
    // signing route is silently dropped.
    service.handleUri(Uri.parse('https://app.contractos.example.com/other'));
    service.handleUri(Uri.parse('contractos://open'));
  });

  test('single-segment signing route is ignored (needs a token)', () {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final service = container.read(deepLinkServiceProvider);
    service.handleUri(Uri.parse('https://app.contractos.example.com/signing'));
  });
}
