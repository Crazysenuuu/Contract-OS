import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_controller.dart';
import '../../core/network/api_client.dart';
import '../approvals/approvals_page.dart';
import '../contracts/contracts_page.dart';

// ---------------------------------------------------------------------------
// Models (spec 2.02 mobile mockup: unified task inbox —
// "Contract awaiting your signature → Review → Approve/Sign").
// Aggregates the three action surfaces into one Home/Tasks screen:
//   1. Dashboard tasks  (GET /api/v1/dashboard/tasks?status=pending)
//   2. Pending approvals (GET /api/v1/approvals?status=pending)
//   3. Contracts awaiting signature (GET /api/v1/agreements?status=…)
// ---------------------------------------------------------------------------

class TaskItem {
  const TaskItem({
    required this.kind,
    required this.title,
    required this.subtitle,
    required this.actionLabel,
    this.agreementId,
    this.approvalId,
    this.dueAt,
    this.urgent = false,
  });

  final TaskKind kind;
  final String title;
  final String subtitle;
  final String actionLabel;
  final String? agreementId;

  /// The approval record id (route target for approval actions); null for
  /// non-approval items.
  final String? approvalId;
  final DateTime? dueAt;
  final bool urgent;

  factory TaskItem.fromDashboardJson(Map<String, dynamic> j) {
    final due = j['due_at'] != null
        ? DateTime.tryParse(j['due_at'] as String)
        : null;
    final urgent =
        (j['priority'] as String? ?? 'normal') == 'urgent' ||
            (j['priority'] as String? ?? '') == 'high' ||
            (due != null && due.isBefore(DateTime.now()));
    return TaskItem(
      kind: TaskKind.task,
      title: j['title'] as String? ?? 'Untitled task',
      subtitle: j['description'] as String? ?? 'Assigned to you',
      actionLabel: 'Open',
      agreementId: j['agreement_id'] as String?,
      dueAt: due,
      urgent: urgent,
    );
  }

  factory TaskItem.fromApprovalJson(Map<String, dynamic> j) {
    final due = j['deadline'] != null
        ? DateTime.tryParse(j['deadline'] as String)
        : null;
    return TaskItem(
      kind: TaskKind.approval,
      title: j['agreement_title'] as String? ?? 'Untitled',
      subtitle:
          'Approval requested by ${j['requested_by_name'] as String? ?? '—'}'
          ' · Level ${j['level'] as int? ?? 1}',
      actionLabel: 'Review',
      approvalId: j['id'] as String?,
      agreementId: j['agreement_id'] as String?,
      dueAt: due,
      urgent: due != null &&
          due.difference(DateTime.now()).inDays <= 2,
    );
  }

  factory TaskItem.fromContractJson(Map<String, dynamic> j) {
    final names = (j['party_names'] as List?)?.cast<String>() ?? const [];
    final counterparty = (j['counterparty'] as String?) ??
        (names.isEmpty ? 'Unknown Party' : names.join(', '));
    return TaskItem(
      kind: TaskKind.signature,
      title: j['title'] as String? ?? 'Untitled',
      subtitle: 'Awaiting signature · $counterparty',
      actionLabel: 'Sign',
      agreementId: j['id'] as String?,
    );
  }
}

enum TaskKind { task, approval, signature }

// ---------------------------------------------------------------------------
// Aggregation provider
// ---------------------------------------------------------------------------

class TaskHubLoad {
  const TaskHubLoad({
    required this.items,
    required this.failedSources,
  });

  final List<TaskItem> items;
  final List<String> failedSources;

  bool get hasFailures => failedSources.isNotEmpty;
}

final taskHubProvider =
    FutureProvider.autoDispose<TaskHubLoad>((ref) async {
  final client = ref.watch(apiClientProvider);
  final store = ref.watch(secureTokenStoreProvider);
  final token = await store.readAccessToken();
  if (token == null || token.isEmpty) {
    return const TaskHubLoad(items: [], failedSources: []);
  }
  final headers = Options(headers: {'Authorization': 'Bearer $token'});

  final items = <TaskItem>[];
  final failed = <String>[];

  // 1. Dashboard tasks (primary surface; failures here fail the load).
  try {
    final resp = await client.dio.get<List<dynamic>>(
      '/api/v1/dashboard/tasks',
      queryParameters: {'status': 'pending', 'limit': 50},
      options: headers,
    );
    for (final e in (resp.data ?? const [])) {
      items.add(TaskItem.fromDashboardJson(e as Map<String, dynamic>));
    }
  } on DioException {
    failed.add('tasks');
  }

  // 2. Pending approvals (spec 2.02: "Contract awaiting your approval").
  try {
    final resp = await client.dio.get<Map<String, dynamic>>(
      '/api/v1/approvals',
      queryParameters: {'status': 'pending', 'page_size': 50},
      options: headers,
    );
    for (final e in (resp.data?['items'] as List<dynamic>? ?? const [])) {
      items.add(TaskItem.fromApprovalJson(e as Map<String, dynamic>));
    }
  } on DioException {
    failed.add('approvals');
  }

  // 3. Contracts awaiting signature (spec 2.02: "awaiting your signature").
  try {
    final resp = await client.dio.get<Map<String, dynamic>>(
      '/api/v1/agreements',
      queryParameters: {'status': 'pending_signature', 'limit': 20},
      options: headers,
    );
    for (final e in (resp.data?['items'] as List<dynamic>? ?? const [])) {
      items.add(TaskItem.fromContractJson(e as Map<String, dynamic>));
    }
  } on DioException {
    failed.add('signatures');
  }

  // Urgent first, then by due date, then by kind for stability.
  items.sort((a, b) {
    if (a.urgent != b.urgent) return a.urgent ? -1 : 1;
    final ad = a.dueAt, bd = b.dueAt;
    if (ad != null && bd != null) return ad.compareTo(bd);
    if (ad != null) return -1;
    if (bd != null) return 1;
    return a.kind.index.compareTo(b.kind.index);
  });

  return TaskHubLoad(items: items, failedSources: failed);
});

// ---------------------------------------------------------------------------
// Home / Task Hub page
// ---------------------------------------------------------------------------

class TaskHubPage extends ConsumerWidget {
  const TaskHubPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(taskHubProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('My Tasks'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: () => ref.invalidate(taskHubProvider),
          ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline, size: 40, color: Colors.red),
              const SizedBox(height: 8),
              Text('Failed to load: $e',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: () => ref.invalidate(taskHubProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (load) {
          if (load.items.isEmpty && !load.hasFailures) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.task_alt,
                      size: 48, color: Colors.green.shade300),
                  const SizedBox(height: 12),
                  Text(
                    'All caught up!',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 4),
                  const Text('No pending tasks, approvals or signatures.'),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(taskHubProvider),
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: load.items.length + (load.hasFailures ? 1 : 0),
              separatorBuilder: (context, index) =>
                  const Divider(height: 1, indent: 16, endIndent: 16),
              itemBuilder: (context, i) {
                if (i == load.items.length && load.hasFailures) {
                  return _PartialLoadNotice(sources: load.failedSources);
                }
                return _TaskTile(item: load.items[i]);
              },
            ),
          );
        },
      ),
    );
  }
}

class _TaskTile extends StatelessWidget {
  const _TaskTile({required this.item});
  final TaskItem item;

  @override
  Widget build(BuildContext context) {
    final (icon, color) = switch (item.kind) {
      TaskKind.signature => (Icons.draw_outlined, Colors.orange),
      TaskKind.approval => (Icons.approval_outlined, Colors.blue),
      TaskKind.task => (Icons.checklist_outlined, Colors.teal),
    };

    return ListTile(
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: CircleAvatar(
        backgroundColor: item.urgent
            ? Colors.red.shade100
            : color.withValues(alpha: 0.15),
        child: Icon(
          icon,
          size: 20,
          color: item.urgent ? Colors.red : color,
        ),
      ),
      title: Text(
        item.title,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontWeight: FontWeight.w600),
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(item.subtitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall),
          if (item.dueAt != null)
            Text(
              'Due ${item.dueAt!.day}/${item.dueAt!.month}/${item.dueAt!.year}',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: item.urgent ? Colors.red : Colors.orange,
                    fontWeight: FontWeight.w500,
                  ),
            ),
        ],
      ),
      trailing: FilledButton.tonal(
        onPressed: () => _open(context),
        style: FilledButton.styleFrom(
          visualDensity: VisualDensity.compact,
          backgroundColor:
              item.urgent ? Colors.red.shade50 : null,
        ),
        child: Text(item.actionLabel),
      ),
      onTap: () => _open(context),
    );
  }

  void _open(BuildContext context) {
    switch (item.kind) {
      case TaskKind.approval:
        final approvalId = item.approvalId;
        if (approvalId == null || approvalId.isEmpty) break;
        Navigator.of(context).push<void>(
          MaterialPageRoute(
            builder: (_) => ApprovalDetailPage(
              item: ApprovalItem(
                id: approvalId,
                agreementId: item.agreementId ?? '',
                agreementTitle: item.title,
                requestedByName: item.subtitle,
                requestedAt: DateTime.now(),
                level: 1,
                status: 'pending',
                deadline: item.dueAt,
              ),
            ),
          ),
        );
      case TaskKind.signature || TaskKind.task:
        if (item.agreementId != null && item.agreementId!.isNotEmpty) {
          Navigator.of(context).push<void>(
            MaterialPageRoute(
              builder: (_) =>
                  ContractDetailPage(contractId: item.agreementId!),
            ),
          );
        }
    }
  }
}

class _PartialLoadNotice extends StatelessWidget {
  const _PartialLoadNotice({required this.sources});
  final List<String> sources;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Icon(Icons.warning_amber_outlined,
          size: 20, color: Colors.orange.shade700),
      title: Text(
        'Some sources failed to load: ${sources.join(', ')}',
        style: Theme.of(context)
            .textTheme
            .bodySmall
            ?.copyWith(color: Colors.orange.shade800),
      ),
    );
  }
}
