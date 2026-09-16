import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../core/auth/auth_controller.dart';
import '../../core/network/api_client.dart';
import '../contracts/contracts_page.dart';

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

class DashboardSummary {
  const DashboardSummary({
    required this.active,
    required this.expiringIn30Days,
    required this.pendingSignature,
    required this.pendingApproval,
    required this.highRisk,
    required this.renewalsIn90Days,
  });

  final int active;
  final int expiringIn30Days;
  final int pendingSignature;
  final int pendingApproval;
  final int highRisk;
  final int renewalsIn90Days;

  factory DashboardSummary.fromJson(Map<String, dynamic> j) =>
      DashboardSummary(
        active: (j['active'] as int?) ?? 0,
        expiringIn30Days: (j['expiring_30_days'] as int?) ?? 0,
        pendingSignature: (j['pending_signature'] as int?) ?? 0,
        pendingApproval: (j['pending_approval'] as int?) ?? 0,
        highRisk: (j['high_risk'] as int?) ?? 0,
        renewalsIn90Days: (j['renewals_90_days'] as int?) ?? 0,
      );
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final dashboardSummaryProvider =
    FutureProvider.autoDispose<DashboardSummary>((ref) async {
  final resp = await ref
      .watch(apiClientProvider)
      .dio
      .get('/api/v1/dashboard/summary');
  return DashboardSummary.fromJson(resp.data as Map<String, dynamic>);
});

final recentActivityProvider =
    FutureProvider.autoDispose<List<ContractSummary>>((ref) async {
  final resp = await ref.watch(apiClientProvider).dio.get(
        '/api/v1/agreements',
        queryParameters: {'limit': 5, 'sort': '-updated_at'},
      );
  final items = (resp.data['items'] as List?) ?? [];
  return items
      .cast<Map<String, dynamic>>()
      .map(ContractSummary.fromJson)
      .toList();
});

final aiInsightProvider = FutureProvider.autoDispose<String?>((ref) async {
  try {
    final resp = await ref
        .watch(apiClientProvider)
        .dio
        .get('/api/v1/intelligence/portfolio-summary');
    return resp.data['summary'] as String?;
  } on DioException {
    return null; // AI insights are best-effort
  }
});

// ---------------------------------------------------------------------------
// Agreements / Dashboard page
// ---------------------------------------------------------------------------

class AgreementsPage extends ConsumerWidget {
  const AgreementsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final user = auth.user;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Dashboard'),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            tooltip: 'Search contracts',
            onPressed: () => _showSearch(context, ref),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(dashboardSummaryProvider);
          ref.invalidate(recentActivityProvider);
          ref.invalidate(aiInsightProvider);
        },
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // Greeting
            if (user != null) ...[
              Text(
                'Hello, ${user.name.isEmpty ? user.email : user.name}',
                style: Theme.of(context)
                    .textTheme
                    .titleLarge
                    ?.copyWith(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 4),
              Text(
                'Here is your contract overview',
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: Colors.grey.shade600),
              ),
              const SizedBox(height: 20),
            ],

            // Summary cards
            _SummarySection(),
            const SizedBox(height: 20),

            // AI Insight
            _AiInsightCard(),
            const SizedBox(height: 20),

            // Action items (needs my attention)
            _ActionItemsSection(),
            const SizedBox(height: 20),

            // Recent activity
            _RecentActivitySection(),
          ],
        ),
      ),
    );
  }

  void _showSearch(BuildContext context, WidgetRef ref) {
    showSearch(
      context: context,
      delegate: _ContractSearchDelegate(ref),
    );
  }
}

// ---------------------------------------------------------------------------
// Summary section — Spec §26 dashboard counts
// ---------------------------------------------------------------------------

class _SummarySection extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(dashboardSummaryProvider);

    return async.when(
      loading: () => const _SummarySkeletons(),
      error: (err, _) => const SizedBox.shrink(),
      data: (summary) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Overview',
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            crossAxisSpacing: 12,
            mainAxisSpacing: 12,
            childAspectRatio: 1.6,
            children: [
              _SummaryCard(
                label: 'Active',
                value: summary.active,
                icon: Icons.check_circle_outline,
                color: Colors.green,
              ),
              _SummaryCard(
                label: 'Sign Now',
                value: summary.pendingSignature,
                icon: Icons.draw_outlined,
                color: Colors.orange,
                urgent: summary.pendingSignature > 0,
              ),
              _SummaryCard(
                label: 'Approve',
                value: summary.pendingApproval,
                icon: Icons.approval_outlined,
                color: Colors.blue,
              ),
              _SummaryCard(
                label: 'Expiring 30d',
                value: summary.expiringIn30Days,
                icon: Icons.timer_outlined,
                color: Colors.deepOrange,
                urgent: summary.expiringIn30Days > 0,
              ),
              _SummaryCard(
                label: 'High Risk',
                value: summary.highRisk,
                icon: Icons.warning_amber_outlined,
                color: Colors.red,
                urgent: summary.highRisk > 0,
              ),
              _SummaryCard(
                label: 'Renewals 90d',
                value: summary.renewalsIn90Days,
                icon: Icons.autorenew_outlined,
                color: Colors.purple,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
    this.urgent = false,
  });

  final String label;
  final int value;
  final IconData icon;
  final Color color;
  final bool urgent;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      decoration: BoxDecoration(
        color: urgent
            ? color.withValues(alpha: 0.08)
            : Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
        border: urgent
            ? Border.all(color: color.withValues(alpha: 0.4))
            : null,
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Icon(icon, color: color, size: 20),
              if (urgent) ...[
                const SizedBox(width: 4),
                Container(
                  width: 6,
                  height: 6,
                  decoration:
                      BoxDecoration(color: color, shape: BoxShape.circle),
                ),
              ],
            ],
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                value.toString(),
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.bold,
                      color: urgent ? color : null,
                    ),
              ),
              Text(
                label,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: Colors.grey.shade600,
                    ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SummarySkeletons extends StatelessWidget {
  const _SummarySkeletons();

  @override
  Widget build(BuildContext context) {
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      crossAxisSpacing: 12,
      mainAxisSpacing: 12,
      childAspectRatio: 1.6,
      children: List.generate(
        6,
        (_) => Container(
          decoration: BoxDecoration(
            color: Colors.grey.shade200,
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// AI Insight card — spec §36 AI Contract Copilot
// ---------------------------------------------------------------------------

class _AiInsightCard extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(aiInsightProvider);

    return async.when(
      loading: () => const SizedBox.shrink(),
      error: (_, _) => const SizedBox.shrink(),
      data: (insight) {
        if (insight == null || insight.isEmpty) return const SizedBox.shrink();
        return Container(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                Theme.of(context).colorScheme.primary.withValues(alpha: 0.1),
                Theme.of(context).colorScheme.secondary.withValues(alpha: 0.05),
              ],
            ),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color:
                  Theme.of(context).colorScheme.primary.withValues(alpha: 0.2),
            ),
          ),
          padding: const EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                Icons.auto_awesome,
                color: Theme.of(context).colorScheme.primary,
                size: 20,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'AI Portfolio Insight',
                      style: Theme.of(context).textTheme.labelMedium?.copyWith(
                            fontWeight: FontWeight.w600,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                    ),
                    const SizedBox(height: 4),
                    Text(insight,
                        style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Action items — high priority items needing attention
// ---------------------------------------------------------------------------

class _ActionItemsSection extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Show pending-signature contracts specifically since those are urgent
    final async = ref.watch(contractsProvider('pending_signature'));

    return async.when(
      loading: () => const SizedBox.shrink(),
      error: (_, _) => const SizedBox.shrink(),
      data: (contracts) {
        if (contracts.isEmpty) return const SizedBox.shrink();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.priority_high, color: Colors.orange, size: 18),
                const SizedBox(width: 6),
                Text(
                  'Needs Your Signature (${contracts.length})',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ...contracts.take(3).map(
                  (c) => _ActionCard(contract: c),
                ),
          ],
        );
      },
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({required this.contract});
  final ContractSummary contract;

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 2,
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: Colors.orange.shade100,
          child: const Icon(Icons.draw_outlined, color: Colors.orange),
        ),
        title: Text(contract.title,
            maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(contract.counterparty),
        trailing: FilledButton.tonal(
          onPressed: () => Navigator.of(context).push<void>(
            MaterialPageRoute(
              builder: (_) => ContractDetailPage(contractId: contract.id),
            ),
          ),
          child: const Text('Sign'),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Recent activity
// ---------------------------------------------------------------------------

class _RecentActivitySection extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(recentActivityProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Recent Activity',
          style: Theme.of(context)
              .textTheme
              .titleMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 10),
        async.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (_, _) =>
              const Text('Could not load recent activity.'),
          data: (contracts) => contracts.isEmpty
              ? const Text('No recent activity.')
              : Column(
                  children: contracts
                      .map((c) => ListTile(
                            contentPadding: EdgeInsets.zero,
                            leading: Icon(c.status.icon,
                                color: c.status.color),
                            title: Text(c.title,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis),
                            subtitle: Text(c.status.label),
                            onTap: () => Navigator.of(context).push<void>(
                              MaterialPageRoute(
                                builder: (_) =>
                                    ContractDetailPage(contractId: c.id),
                              ),
                            ),
                          ))
                      .toList(),
                ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Search delegate
// ---------------------------------------------------------------------------

class _ContractSearchDelegate extends SearchDelegate<String?> {
  _ContractSearchDelegate(this._ref);
  final WidgetRef _ref;

  @override
  List<Widget> buildActions(BuildContext context) => [
        IconButton(
          icon: const Icon(Icons.clear),
          onPressed: () => query = '',
        ),
      ];

  @override
  Widget buildLeading(BuildContext context) => IconButton(
        icon: const Icon(Icons.arrow_back),
        onPressed: () => close(context, null),
      );

  @override
  Widget buildResults(BuildContext context) => _buildList();

  @override
  Widget buildSuggestions(BuildContext context) => _buildList();

  Widget _buildList() {
    if (query.length < 2) {
      return const Center(child: Text('Type at least 2 characters to search'));
    }
    return FutureBuilder<Response>(
      future: _ref.read(apiClientProvider).dio.get(
            '/api/v1/agreements',
            queryParameters: {'search': query, 'limit': 20},
          ),
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return Center(child: Text('Error: ${snap.error}'));
        }
        final items =
            ((snap.data?.data['items'] as List?) ?? [])
                .cast<Map<String, dynamic>>()
                .map(ContractSummary.fromJson)
                .toList();
        if (items.isEmpty) {
          return const Center(child: Text('No results'));
        }
        return ListView.separated(
          itemCount: items.length,
          separatorBuilder: (_, _) => const Divider(height: 1),
          itemBuilder: (context, i) {
            final c = items[i];
            return ListTile(
              title: Text(c.title),
              subtitle: Text(c.counterparty),
              trailing: Icon(c.status.icon, color: c.status.color),
              onTap: () {
                close(context, c.id);
                Navigator.of(context).push<void>(
                  MaterialPageRoute(
                    builder: (_) => ContractDetailPage(contractId: c.id),
                  ),
                );
              },
            );
          },
        );
      },
    );
  }
}