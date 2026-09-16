import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:contractos_mobile/features/lifecycle/data/lifecycle_api.dart';

/// Renewal & termination management (spec 2.02 §41-47): shows the renewal
/// status for an agreement, allows serving a non-renewal notice, and lets
/// an authorised user initiate / progress a termination.
class LifecyclePage extends ConsumerWidget {
  const LifecyclePage({super.key, required this.agreementId});

  final String agreementId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final renewal = ref.watch(renewalProvider(agreementId));
    final terminations = ref.watch(terminationsProvider(agreementId));

    return Scaffold(
      appBar: AppBar(title: const Text('Renewal & Termination')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          renewal.when(
            loading: () => const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: CircularProgressIndicator(),
              ),
            ),
            error: (e, _) => ListTile(
              leading: const Icon(Icons.error_outline, color: Colors.red),
              title: Text('Could not load renewal: $e'),
            ),
            data: (r) => _RenewalCard(renewal: r, agreementId: agreementId),
          ),
          const SizedBox(height: 16),
          Text('Terminations', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          terminations.when(
            loading: () => const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: CircularProgressIndicator(),
              ),
            ),
            error: (e, _) => ListTile(
              leading: const Icon(Icons.error_outline, color: Colors.red),
              title: Text('Could not load terminations: $e'),
            ),
            data: (terms) {
              if (terms.isEmpty) {
                return const ListTile(
                  leading: Icon(Icons.inventory_2_outlined),
                  title: Text('No terminations initiated'),
                );
              }
              return Column(
                children: [
                  for (final t in terms)
                    _TerminationCard(
                        termination: t, agreementId: agreementId),
                ],
              );
            },
          ),
          const SizedBox(height: 16),
          FilledButton.tonalIcon(
            onPressed: () => _initiateTermination(context, ref),
            icon: const Icon(Icons.gavel_outlined),
            label: const Text('Initiate termination'),
          ),
        ],
      ),
    );
  }

  Future<void> _initiateTermination(BuildContext context, WidgetRef ref) async {
    final reason = await showDialog<_TerminationDraft>(
      context: context,
      builder: (_) => const _InitiateTerminationDialog(),
    );
    if (reason == null) return;
    try {
      await ref.read(terminationsRepositoryProvider).initiate(
            agreementId: agreementId,
            reasonCode: reason.code,
            reasonDetail: reason.detail,
            noticePeriodDays: reason.noticePeriodDays,
          );
      ref.invalidate(terminationsProvider(agreementId));
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed: $e')),
        );
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Renewal
// ---------------------------------------------------------------------------

class _RenewalCard extends ConsumerWidget {
  const _RenewalCard({required this.renewal, required this.agreementId});

  final ContractRenewal renewal;
  final String agreementId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final expiry = renewal.currentExpiryDate;
    final daysLeft = expiry?.difference(DateTime.now()).inDays;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  renewal.autoRenew
                      ? Icons.autorenew
                      : Icons.sync_disabled_outlined,
                  color: renewal.autoRenew ? Colors.green : Colors.grey,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    renewal.autoRenew
                        ? 'Auto-renews'
                        : renewal.isRenewable
                            ? 'Manual renewal'
                            : 'Not renewable',
                    style: theme.textTheme.titleMedium,
                  ),
                ),
                if (daysLeft != null)
                  Chip(
                    label: Text(
                      daysLeft < 0
                          ? 'Expired'
                          : daysLeft < 30
                              ? 'Expiring in ${daysLeft}d'
                              : '${daysLeft}d left',
                      style: TextStyle(
                        fontSize: 11,
                        color: daysLeft < 30 ? Colors.deepOrange : null,
                      ),
                    ),
                  ),
              ],
            ),
            if (expiry != null) ...[
              const SizedBox(height: 8),
              Text('Current expiry: ${_fmt(expiry)}'),
            ],
            if (renewal.noticeGiven == true) ...[
              const SizedBox(height: 4),
              const Text(
                'Non-renewal notice served',
                style: TextStyle(color: Colors.deepOrange),
              ),
            ],
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: renewal.noticeGiven == true
                  ? null
                  : () => _serveNotice(context, ref),
              icon: const Icon(Icons.outgoing_mail),
              label: const Text('Serve non-renewal notice'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _serveNotice(BuildContext context, WidgetRef ref) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Serve non-renewal notice?'),
        content: const Text(
          'This records a formal non-renewal notice against the agreement '
          'as of today. It cannot be undone from the app.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Serve notice'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await ref
          .read(renewalRepositoryProvider)
          .serveNonRenewalNotice(agreementId);
      ref.invalidate(renewalProvider(agreementId));
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed: $e')),
        );
      }
    }
  }

  String _fmt(DateTime d) => '${d.day}/${d.month}/${d.year}';
}

// ---------------------------------------------------------------------------
// Terminations
// ---------------------------------------------------------------------------

class _TerminationCard extends ConsumerWidget {
  const _TerminationCard({required this.termination, required this.agreementId});

  final AgreementTermination termination;
  final String agreementId;

  Color get _statusColor => switch (termination.status) {
        'completed' => Colors.red,
        'notice_served' => Colors.deepOrange,
        'cure_pending' => Colors.orange,
        'cancelled' => Colors.grey,
        _ => Colors.blue,
      };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Card(
      child: ListTile(
        leading: Icon(Icons.gavel_outlined, color: _statusColor),
        title: Text(
          termination.reasonCode.replaceAll('_', ' '),
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Status: ${termination.status.replaceAll('_', ' ')}'),
            if (termination.reasonDetail != null)
              Text(termination.reasonDetail!, maxLines: 2),
            if (termination.cureRequired == true)
              Text(
                'Cure ${termination.cured == true ? 'recorded' : 'required'}',
                style: TextStyle(
                  color: termination.cured == true ? Colors.green : Colors.orange,
                ),
              ),
          ],
        ),
        trailing: _TerminationActions(
          termination: termination,
          agreementId: agreementId,
        ),
      ),
    );
  }
}

class _TerminationActions extends ConsumerWidget {
  const _TerminationActions({required this.termination, required this.agreementId});

  final AgreementTermination termination;
  final String agreementId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = termination.status != 'completed' &&
        termination.status != 'cancelled';
    if (!active) return const SizedBox.shrink();

    if (termination.noticeServed != true) {
      return IconButton(
        tooltip: 'Serve notice',
        icon: const Icon(Icons.outgoing_mail),
        onPressed: () => _run(
          context,
          ref,
          () => ref
              .read(terminationsRepositoryProvider)
              .serveNotice(agreementId, termination.id),
        ),
      );
    }
    return IconButton(
      tooltip: 'Complete',
      icon: const Icon(Icons.task_alt_outlined),
      onPressed: () => _run(
        context,
        ref,
        () => ref
            .read(terminationsRepositoryProvider)
            .complete(agreementId, termination.id),
      ),
    );
  }

  Future<void> _run(
    BuildContext context,
    WidgetRef ref,
    Future<void> Function() action,
  ) async {
    try {
      await action();
      ref.invalidate(terminationsProvider(agreementId));
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed: $e')),
        );
      }
    }
  }
}

class _TerminationDraft {
  const _TerminationDraft({
    required this.code,
    this.detail,
    this.noticePeriodDays,
  });

  final String code;
  final String? detail;
  final int? noticePeriodDays;
}

class _InitiateTerminationDialog extends StatefulWidget {
  const _InitiateTerminationDialog();

  @override
  State<_InitiateTerminationDialog> createState() =>
      _InitiateTerminationDialogState();
}

class _InitiateTerminationDialogState
    extends State<_InitiateTerminationDialog> {
  static const _reasons = <String, String>{
    'breach': 'Material breach',
    'non_performance': 'Non-performance',
    'convenience': 'Termination for convenience',
    'insolvency': 'Insolvency',
    'mutual_agreement': 'Mutual agreement',
  };

  String _code = 'breach';
  final _detail = TextEditingController();
  final _noticeDays = TextEditingController();

  @override
  void dispose() {
    _detail.dispose();
    _noticeDays.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Initiate termination'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            DropdownButtonFormField<String>(
              initialValue: _code,
              decoration: const InputDecoration(labelText: 'Reason'),
              items: _reasons.entries
                  .map((e) => DropdownMenuItem(
                        value: e.key,
                        child: Text(e.value),
                      ))
                  .toList(),
              onChanged: (v) => setState(() => _code = v ?? 'breach'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _detail,
              decoration: const InputDecoration(
                labelText: 'Detail (optional)',
              ),
              maxLines: 3,
            ),
            TextField(
              controller: _noticeDays,
              decoration: const InputDecoration(
                labelText: 'Notice period (days, optional)',
              ),
              keyboardType: TextInputType.number,
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_TerminationDraft(
            code: _code,
            detail: _detail.text.trim().isEmpty ? null : _detail.text.trim(),
            noticePeriodDays: int.tryParse(_noticeDays.text.trim()),
          )),
          child: const Text('Initiate'),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final renewalProvider = FutureProvider.autoDispose
    .family<ContractRenewal, String>((ref, agreementId) {
  return ref.watch(renewalRepositoryProvider).get(agreementId);
});

final terminationsProvider = FutureProvider.autoDispose
    .family<List<AgreementTermination>, String>((ref, agreementId) {
  return ref.watch(terminationsRepositoryProvider).list(agreementId);
});
