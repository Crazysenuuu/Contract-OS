import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_controller.dart';
import '../../core/network/api_client.dart';

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

class ApprovalItem {
  const ApprovalItem({
    required this.id,
    required this.agreementId,
    required this.agreementTitle,
    required this.requestedByName,
    required this.requestedAt,
    required this.level,
    required this.status,
    this.deadline,
    this.notes,
  });

  final String id;
  final String agreementId;
  final String agreementTitle;
  final String requestedByName;
  final DateTime requestedAt;
  final int level;
  final String status;
  final DateTime? deadline;
  final String? notes;

  factory ApprovalItem.fromJson(Map<String, dynamic> j) => ApprovalItem(
        id: j['id'] as String,
        agreementId: j['agreement_id'] as String,
        agreementTitle: j['agreement_title'] as String? ?? 'Untitled',
        requestedByName: j['requested_by_name'] as String? ?? '—',
        requestedAt: DateTime.parse(j['requested_at'] as String),
        level: (j['level'] as int?) ?? 1,
        status: j['status'] as String? ?? 'pending',
        deadline: j['deadline'] != null
            ? DateTime.tryParse(j['deadline'] as String)
            : null,
        notes: j['notes'] as String?,
      );
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

final approvalsProvider =
    FutureProvider.autoDispose<List<ApprovalItem>>((ref) async {
  final client = ref.watch(apiClientProvider);
  final store = ref.watch(secureTokenStoreProvider);
  final token = await store.readAccessToken();
  if (token == null || token.isEmpty) return [];

  final resp = await client.dio.get<Map<String, dynamic>>(
    '/api/v1/approvals',
    queryParameters: {'status': 'pending', 'page_size': 50},
    options: Options(headers: {'Authorization': 'Bearer $token'}),
  );
  final items = (resp.data?['items'] as List<dynamic>?) ?? [];
  return items
      .map((e) => ApprovalItem.fromJson(e as Map<String, dynamic>))
      .toList();
});

// ---------------------------------------------------------------------------
// Approvals list page
// ---------------------------------------------------------------------------

class ApprovalsPage extends ConsumerWidget {
  const ApprovalsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(approvalsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pending Approvals'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: () => ref.invalidate(approvalsProvider),
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
                onPressed: () => ref.invalidate(approvalsProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (items) {
          if (items.isEmpty) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.check_circle_outline,
                      size: 48, color: Colors.green.shade300),
                  const SizedBox(height: 12),
                  Text(
                    'All caught up!',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'No pending approvals.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(approvalsProvider),
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: items.length,
              separatorBuilder: (context, index) =>
                  const Divider(height: 1, indent: 16, endIndent: 16),
              itemBuilder: (context, i) =>
                  _ApprovalTile(item: items[i]),
            ),
          );
        },
      ),
    );
  }
}

class _ApprovalTile extends StatelessWidget {
  const _ApprovalTile({required this.item});
  final ApprovalItem item;

  @override
  Widget build(BuildContext context) {
    final isUrgent = item.deadline != null &&
        item.deadline!.difference(DateTime.now()).inDays <= 2;

    return ListTile(
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: CircleAvatar(
        backgroundColor: isUrgent
            ? Colors.red.shade100
            : Theme.of(context).colorScheme.primaryContainer,
        child: Icon(
          Icons.approval,
          size: 20,
          color: isUrgent
              ? Colors.red
              : Theme.of(context).colorScheme.primary,
        ),
      ),
      title: Text(
        item.agreementTitle,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontWeight: FontWeight.w600),
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'By ${item.requestedByName} · Level ${item.level}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (item.deadline != null)
            Text(
              'Due ${_fmt(item.deadline!)}',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: isUrgent ? Colors.red : Colors.orange,
                    fontWeight: FontWeight.w500,
                  ),
            ),
        ],
      ),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ApprovalDetailPage(item: item),
        ),
      ),
    );
  }

  String _fmt(DateTime dt) =>
      '${dt.day}/${dt.month}/${dt.year}';
}

// ---------------------------------------------------------------------------
// Approval detail + decision page
// ---------------------------------------------------------------------------

class ApprovalDetailPage extends ConsumerStatefulWidget {
  const ApprovalDetailPage({super.key, required this.item});
  final ApprovalItem item;

  @override
  ConsumerState<ApprovalDetailPage> createState() => _ApprovalDetailPageState();
}

class _ApprovalDetailPageState extends ConsumerState<ApprovalDetailPage> {
  final _notesController = TextEditingController();
  bool _submitting = false;

  @override
  void dispose() {
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _decide(String action) async {
    final store = ref.read(secureTokenStoreProvider);
    final token = await store.readAccessToken();
    if (token == null || token.isEmpty) return;
    setState(() => _submitting = true);
    try {
      final client = ref.read(apiClientProvider);
      await client.dio.post<void>(
        '/api/v1/approvals/${widget.item.id}/$action',
        data: {'notes': _notesController.text.trim().isEmpty
            ? null
            : _notesController.text.trim()},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      if (!mounted) return;
      ref.invalidate(approvalsProvider);
      Navigator.of(context).pop();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            action == 'approve' ? 'Approved successfully.' : 'Rejected.',
          ),
          backgroundColor:
              action == 'approve' ? Colors.green : Colors.red,
        ),
      );
    } on DioException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
              'Error: ${e.response?.data?['detail'] ?? e.message}'),
          backgroundColor: Colors.red,
        ),
      );
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    return Scaffold(
      appBar: AppBar(title: const Text('Approval Request')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Agreement info card
            Card(
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.agreementTitle,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 8),
                    _InfoRow('Requested by', item.requestedByName),
                    _InfoRow(
                      'Requested at',
                      '${item.requestedAt.day}/${item.requestedAt.month}/${item.requestedAt.year}',
                    ),
                    _InfoRow('Level', 'Level ${item.level}'),
                    if (item.deadline != null)
                      _InfoRow(
                        'Deadline',
                        '${item.deadline!.day}/${item.deadline!.month}/${item.deadline!.year}',
                      ),
                    if (item.notes != null && item.notes!.isNotEmpty)
                      _InfoRow('Notes', item.notes!),
                  ],
                ),
              ),
            ),

            const SizedBox(height: 20),

            // Notes input
            Text(
              'Decision notes (optional)',
              style: Theme.of(context).textTheme.labelLarge,
            ),
            const SizedBox(height: 6),
            TextField(
              controller: _notesController,
              maxLines: 3,
              decoration: InputDecoration(
                hintText: 'Add a comment…',
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
                contentPadding: const EdgeInsets.symmetric(
                    horizontal: 12, vertical: 10),
              ),
            ),

            const SizedBox(height: 24),

            // Action buttons
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    icon: _submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.close, color: Colors.red),
                    label: const Text(
                      'Reject',
                      style: TextStyle(color: Colors.red),
                    ),
                    style: OutlinedButton.styleFrom(
                      side: const BorderSide(color: Colors.red),
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                    onPressed:
                        _submitting ? null : () => _decide('reject'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton.icon(
                    icon: _submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white))
                        : const Icon(Icons.check),
                    label: const Text('Approve'),
                    style: FilledButton.styleFrom(
                      backgroundColor: Colors.green,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                    onPressed:
                        _submitting ? null : () => _decide('approve'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 110,
              child: Text(
                '$label:',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Colors.grey.shade600,
                    ),
              ),
            ),
            Expanded(
              child: Text(
                value,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          ],
        ),
      );
}
