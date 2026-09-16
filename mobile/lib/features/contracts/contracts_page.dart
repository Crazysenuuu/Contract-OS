import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_client.dart';
import 'package:dio/dio.dart';

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

enum ContractStatus {
  draft,
  pendingSignature,
  pendingApproval,
  negotiation,
  active,
  expiring,
  expired,
  executed,
}

extension ContractStatusX on ContractStatus {
  String get label => switch (this) {
        ContractStatus.draft => 'Draft',
        ContractStatus.pendingSignature => 'Pending Signature',
        ContractStatus.pendingApproval => 'Pending Approval',
        ContractStatus.negotiation => 'In Negotiation',
        ContractStatus.active => 'Active',
        ContractStatus.expiring => 'Expiring Soon',
        ContractStatus.expired => 'Expired',
        ContractStatus.executed => 'Executed',
      };

  Color get color => switch (this) {
        ContractStatus.pendingSignature => Colors.orange,
        ContractStatus.pendingApproval => Colors.blue,
        ContractStatus.negotiation => Colors.purple,
        ContractStatus.active => Colors.green,
        ContractStatus.expiring => Colors.deepOrange,
        ContractStatus.expired => Colors.red,
        ContractStatus.executed => Colors.teal,
        ContractStatus.draft => Colors.grey,
      };

  IconData get icon => switch (this) {
        ContractStatus.pendingSignature => Icons.draw_outlined,
        ContractStatus.pendingApproval => Icons.approval_outlined,
        ContractStatus.negotiation => Icons.handshake_outlined,
        ContractStatus.active => Icons.check_circle_outline,
        ContractStatus.expiring => Icons.timer_outlined,
        ContractStatus.expired => Icons.cancel_outlined,
        ContractStatus.executed => Icons.verified_outlined,
        ContractStatus.draft => Icons.edit_outlined,
      };
}

class ContractSummary {
  const ContractSummary({
    required this.id,
    required this.title,
    required this.status,
    required this.counterparty,
    required this.agreementType,
    this.expiresAt,
    this.value,
    this.currency,
  });

  final String id;
  final String title;
  final ContractStatus status;
  final String counterparty;
  final String agreementType;
  final DateTime? expiresAt;
  final double? value;
  final String? currency;

  factory ContractSummary.fromJson(Map<String, dynamic> j) {
    ContractStatus parseStatus(String s) {
      return switch (s.toLowerCase()) {
        'pending_signature' || 'signing' => ContractStatus.pendingSignature,
        'pending_approval' || 'approval' => ContractStatus.pendingApproval,
        'negotiation' => ContractStatus.negotiation,
        'active' => ContractStatus.active,
        'expiring' => ContractStatus.expiring,
        'expired' => ContractStatus.expired,
        'executed' => ContractStatus.executed,
        _ => ContractStatus.draft,
      };
    }

    return ContractSummary(
      id: j['id'] as String,
      title: j['title'] as String? ?? 'Untitled',
      status: parseStatus(j['status'] as String? ?? 'draft'),
      counterparty: (j['counterparty'] as String?) ?? 'Unknown Party',
      agreementType: (j['agreement_type_name'] as String?) ?? '',
      expiresAt: j['expires_at'] != null
          ? DateTime.tryParse(j['expires_at'] as String)
          : null,
      value: (j['contract_value'] as num?)?.toDouble(),
      currency: j['currency'] as String?,
    );
  }
}

// ---------------------------------------------------------------------------
// Repository
// ---------------------------------------------------------------------------

class ContractsRepository {
  ContractsRepository(this._dio);
  final Dio _dio;

  Future<List<ContractSummary>> listContracts({
    String? status,
    int limit = 50,
    int offset = 0,
  }) async {
    final params = <String, dynamic>{'limit': limit, 'offset': offset};
    if (status != null) params['status'] = status;
    final resp = await _dio.get('/api/v1/agreements', queryParameters: params);
    final items = (resp.data['items'] as List?) ?? [];
    return items
        .cast<Map<String, dynamic>>()
        .map(ContractSummary.fromJson)
        .toList();
  }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final contractsRepositoryProvider = Provider<ContractsRepository>((ref) {
  return ContractsRepository(ref.watch(apiClientProvider).dio);
});

final contractsProvider = FutureProvider.autoDispose
    .family<List<ContractSummary>, String?>((ref, status) async {
  return ref.watch(contractsRepositoryProvider).listContracts(status: status);
});

final dashboardCountsProvider =
    FutureProvider.autoDispose<Map<ContractStatus, int>>((ref) async {
  final all = await ref.watch(contractsRepositoryProvider).listContracts();
  final counts = <ContractStatus, int>{};
  for (final c in all) {
    counts[c.status] = (counts[c.status] ?? 0) + 1;
  }
  return counts;
});

// ---------------------------------------------------------------------------
// Main contracts list page
// ---------------------------------------------------------------------------

class ContractsPage extends ConsumerStatefulWidget {
  const ContractsPage({super.key});

  @override
  ConsumerState<ContractsPage> createState() => _ContractsPageState();
}

class _ContractsPageState extends ConsumerState<ContractsPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  static const _statuses = <String?, String>{
    null: 'All',
    'pending_signature': 'Sign',
    'pending_approval': 'Approve',
    'negotiation': 'Negotiate',
    'active': 'Active',
    'expiring': 'Expiring',
  };

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: _statuses.length, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Contracts'),
        bottom: TabBar(
          controller: _tabs,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          tabs: _statuses.values.map((l) => Tab(text: l)).toList(),
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: _statuses.keys
            .map((status) => _ContractsList(status: status))
            .toList(),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Tab content
// ---------------------------------------------------------------------------

class _ContractsList extends ConsumerWidget {
  const _ContractsList({required this.status});
  final String? status;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(contractsProvider(status));

    return async.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (err, _) => _ErrorView(
        message: err.toString(),
        onRetry: () => ref.invalidate(contractsProvider(status)),
      ),
      data: (contracts) {
        if (contracts.isEmpty) {
          return const _EmptyView();
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(contractsProvider(status)),
          child: ListView.separated(
            padding: const EdgeInsets.all(12),
            itemCount: contracts.length,
            separatorBuilder: (_, _) => const SizedBox(height: 8),
            itemBuilder: (context, i) =>
                _ContractCard(contract: contracts[i]),
          ),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Contract card
// ---------------------------------------------------------------------------

class _ContractCard extends StatelessWidget {
  const _ContractCard({required this.contract});
  final ContractSummary contract;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colorScheme = theme.colorScheme;

    return Card(
      elevation: 1,
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => _openDetail(context),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      contract.title,
                      style: theme.textTheme.titleMedium
                          ?.copyWith(fontWeight: FontWeight.w600),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  _StatusChip(contract.status),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                contract.counterparty,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: colorScheme.onSurfaceVariant),
              ),
              if (contract.agreementType.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  contract.agreementType,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: colorScheme.primary),
                ),
              ],
              if (contract.expiresAt != null || contract.value != null) ...[
                const SizedBox(height: 8),
                Wrap(
                  spacing: 12,
                  children: [
                    if (contract.value != null)
                      _InfoChip(
                        icon: Icons.attach_money,
                        label:
                            '${contract.currency ?? ''} ${contract.value!.toStringAsFixed(0)}',
                      ),
                    if (contract.expiresAt != null)
                      _InfoChip(
                        icon: Icons.calendar_today_outlined,
                        label: _formatDate(contract.expiresAt!),
                        danger:
                            contract.expiresAt!.difference(DateTime.now()).inDays < 30,
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  void _openDetail(BuildContext context) {
    Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => ContractDetailPage(contractId: contract.id),
      ),
    );
  }

  String _formatDate(DateTime d) {
    return '${d.day}/${d.month}/${d.year}';
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip(this.status);
  final ContractStatus status;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: status.color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(status.icon, size: 12, color: status.color),
          const SizedBox(width: 4),
          Text(
            status.label,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: status.color,
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoChip extends StatelessWidget {
  const _InfoChip({required this.icon, required this.label, this.danger = false});
  final IconData icon;
  final String label;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final color = danger ? Colors.deepOrange : Colors.grey.shade600;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 12, color: color),
        const SizedBox(width: 4),
        Text(label, style: TextStyle(fontSize: 11, color: color)),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Contract detail page
// ---------------------------------------------------------------------------

class ContractDetailPage extends ConsumerWidget {
  const ContractDetailPage({super.key, required this.contractId});
  final String contractId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(
      FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
        final resp = await ref
            .watch(apiClientProvider)
            .dio
            .get('/api/v1/agreements/$contractId');
        return resp.data as Map<String, dynamic>;
      }),
    );

    return Scaffold(
      appBar: AppBar(
        title: const Text('Agreement'),
        actions: [
          IconButton(
            icon: const Icon(Icons.open_in_browser_outlined),
            tooltip: 'Open in browser',
            onPressed: () {},
          ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) =>
            Center(child: Text('Error loading agreement: $err')),
        data: (data) => _ContractDetailBody(data: data),
      ),
    );
  }
}

class _ContractDetailBody extends StatelessWidget {
  const _ContractDetailBody({required this.data});
  final Map<String, dynamic> data;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final status = data['status'] as String? ?? '';
    final title = data['title'] as String? ?? 'Untitled';
    final value = data['contract_value'];
    final currency = data['currency'] as String? ?? '';
    final expiresAt = data['expires_at'] as String?;
    final parties = (data['parties'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final obligations = (data['obligations'] as List?)?.cast<Map<String, dynamic>>() ?? [];

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(title,
            style: theme.textTheme.headlineSmall
                ?.copyWith(fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        Chip(
          avatar: Icon(ContractStatus.active.icon, size: 16),
          label: Text(status.replaceAll('_', ' ').toUpperCase()),
          backgroundColor: Colors.blue.shade50,
        ),
        const SizedBox(height: 16),

        // Key metrics
        _SectionHeader('Key Details'),
        _DetailRow('Contract Value',
            value != null ? '$currency ${value.toString()}' : 'N/A'),
        _DetailRow('Expires',
            expiresAt != null ? expiresAt.substring(0, 10) : 'N/A'),
        _DetailRow('Governing Law', data['governing_law'] as String? ?? 'N/A'),

        if (parties.isNotEmpty) ...[
          const SizedBox(height: 16),
          _SectionHeader('Parties'),
          ...parties.map((p) => _DetailRow(
                p['party_role'] as String? ?? 'Party',
                p['legal_name'] as String? ?? 'Unknown',
              )),
        ],

        if (obligations.isNotEmpty) ...[
          const SizedBox(height: 16),
          _SectionHeader('Obligations (${obligations.length})'),
          ...obligations.take(5).map((o) => Card(
                child: ListTile(
                  leading: Icon(
                    Icons.task_alt_outlined,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  title: Text(
                    o['title'] as String? ?? 'Obligation',
                    style: theme.textTheme.bodyMedium,
                  ),
                  subtitle: o['due_date'] != null
                      ? Text('Due: ${(o['due_date'] as String).substring(0, 10)}')
                      : null,
                ),
              )),
          if (obligations.length > 5)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '+ ${obligations.length - 5} more obligations',
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.primary),
              ),
            ),
        ],
      ],
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);
  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(
        title,
        style: Theme.of(context).textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
              color: Theme.of(context).colorScheme.primary,
            ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: Colors.grey.shade600),
            ),
          ),
          Expanded(
            child: Text(value,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(fontWeight: FontWeight.w500)),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Empty / error states
// ---------------------------------------------------------------------------

class _EmptyView extends StatelessWidget {
  const _EmptyView();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.folder_open_outlined, size: 64, color: Colors.grey),
          const SizedBox(height: 16),
          Text(
            'No agreements found',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(color: Colors.grey.shade600),
          ),
        ],
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        // Scrollable so long error messages can't overflow small viewports.
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.cloud_off, size: 64, color: Colors.grey),
              const SizedBox(height: 16),
              Text(
                message,
                textAlign: TextAlign.center,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: Colors.grey.shade600),
              ),
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}