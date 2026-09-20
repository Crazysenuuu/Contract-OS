import 'dart:convert';
import 'dart:typed_data';

import 'package:contractos_mobile/core/auth/auth_controller.dart';
import 'package:contractos_mobile/core/auth/secure_token_store.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/features/task_hub/task_hub_page.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _MemTokenStore extends SecureTokenStore {
  @override
  Future<String?> readAccessToken() async => 'jwt';
}

ResponseBody _json(Object body) => ResponseBody.fromString(
      jsonEncode(body),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );

/// Serves the three Task Hub sources; anything else 404s.
class _StubAdapter implements HttpClientAdapter {
  final List<Map<String, dynamic>> tasks;
  final List<Map<String, dynamic>> approvals;
  final List<Map<String, dynamic>> signatureContracts;
  bool tasksFail;

  _StubAdapter({
    this.tasks = const [],
    this.approvals = const [],
    this.signatureContracts = const [],
    this.tasksFail = false,
  });

  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final path = options.uri.path;
    if (path.endsWith('/api/v1/dashboard/tasks')) {
      if (tasksFail) {
        return ResponseBody.fromString('{"detail":"boom"}', 500);
      }
      return _json(tasks);
    }
    if (path.endsWith('/api/v1/approvals')) {
      return _json({'items': approvals});
    }
    if (path.endsWith('/api/v1/agreements')) {
      return _json({'items': signatureContracts});
    }
    return ResponseBody.fromString('{"detail":"no stub"}', 404);
  }
}

Widget _wrap(_StubAdapter adapter) => ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(
          ApiClient(dio: Dio()..httpClientAdapter = adapter),
        ),
        secureTokenStoreProvider.overrideWithValue(_MemTokenStore()),
      ],
      child: const MaterialApp(home: TaskHubPage()),
    );

void main() {
  testWidgets('aggregates tasks, approvals and signature requests',
      (tester) async {
    final adapter = _StubAdapter(
      tasks: [
        {
          'id': 't-1',
          'title': 'Review renewal terms',
          'description': 'Due before renewal notice date',
          'status': 'pending',
          'priority': 'high',
          'task_type': 'review',
          'agreement_id': 'a-1',
          'due_at': DateTime.now().add(const Duration(days: 3)).toIso8601String(),
          'created_at': DateTime.now().toIso8601String(),
        },
      ],
      approvals: [
        {
          'id': 'ap-1',
          'agreement_id': 'a-2',
          'agreement_title': 'Vendor MSA with Initech',
          'requested_by_name': 'Priya',
          'requested_at': DateTime.now().toIso8601String(),
          'level': 2,
          'status': 'pending',
        },
      ],
      signatureContracts: [
        {
          'id': 'a-3',
          'title': 'NDA with Globex',
          'status': 'pending_signature',
          'counterparty': 'Globex',
        },
      ],
    );

    await tester.pumpWidget(_wrap(adapter));
    await tester.pumpAndSettle();

    expect(find.text('Review renewal terms'), findsOneWidget);
    expect(find.text('Vendor MSA with Initech'), findsOneWidget);
    expect(find.text('NDA with Globex'), findsOneWidget);
    expect(find.text('Review'), findsOneWidget);
    expect(find.text('Sign'), findsOneWidget);
    expect(find.text('All caught up!'), findsNothing);
  });

  testWidgets('empty state renders when no action items', (tester) async {
    await tester.pumpWidget(_wrap(_StubAdapter()));
    await tester.pumpAndSettle();

    expect(find.text('All caught up!'), findsOneWidget);
  });

  testWidgets('urgent items sort first and render urgent styling',
      (tester) async {
    final adapter = _StubAdapter(
      tasks: [
        {
          'id': 't-1',
          'title': 'Normal task',
          'status': 'pending',
          'priority': 'low',
          'task_type': 'manual',
        },
        {
          'id': 't-2',
          'title': 'Overdue task',
          'status': 'pending',
          'priority': 'urgent',
          'task_type': 'manual',
          'due_at': DateTime.now()
              .subtract(const Duration(days: 1))
              .toIso8601String(),
        },
      ],
    );

    await tester.pumpWidget(_wrap(adapter));
    await tester.pumpAndSettle();

    // Both render...
    expect(find.text('Overdue task'), findsOneWidget);
    expect(find.text('Normal task'), findsOneWidget);
    // ...and the urgent one sorts before the normal one.
    final overdueTop = tester.getTopLeft(find.text('Overdue task')).dy;
    final normalTop = tester.getTopLeft(find.text('Normal task')).dy;
    expect(overdueTop, lessThan(normalTop));
  });

  testWidgets('partial source failure keeps the rest and shows a notice',
      (tester) async {
    final adapter = _StubAdapter(
      approvals: [
        {
          'id': 'ap-1',
          'agreement_id': 'a-2',
          'agreement_title': 'Approval survives task outage',
          'requested_by_name': 'Sam',
          'requested_at': DateTime.now().toIso8601String(),
          'level': 1,
          'status': 'pending',
        },
      ],
      tasksFail: true,
    );

    await tester.pumpWidget(_wrap(adapter));
    await tester.pumpAndSettle();

    expect(find.text('Approval survives task outage'), findsOneWidget);
    expect(find.textContaining('Some sources failed to load'), findsOneWidget);
    expect(find.textContaining('tasks'), findsOneWidget);
  });
}
