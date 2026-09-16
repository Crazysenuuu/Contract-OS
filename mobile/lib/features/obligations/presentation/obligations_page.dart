import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';

import 'package:contractos_mobile/core/offline/connectivity.dart';
import 'package:contractos_mobile/core/offline/offline_cache.dart';
import 'package:contractos_mobile/features/obligations/data/obligations_api.dart';

/// Obligations workspace (spec 2.02 §36-40): browse an agreement's
/// obligations, track what's due/overdue, and mark items complete. The list
/// falls back to the offline cache when the network is unavailable; cached
/// views are labelled so stale data is never mistaken for live data.
class ObligationsPage extends ConsumerWidget {
  const ObligationsPage({super.key, required this.agreementId});

  final String agreementId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final online = ref.watch(isOnlineProvider).value ?? true;
    final async = ref.watch(obligationsProvider(agreementId));

    return Scaffold(
      appBar: AppBar(title: const Text('Obligations')),
      body: Column(
        children: [
          if (!online)
            const Material(
              color: Colors.deepOrange,
              child: ListTile(
                dense: true,
                leading: Icon(Icons.cloud_off, color: Colors.white),
                title: Text(
                  'Offline — showing saved data',
                  style: TextStyle(color: Colors.white),
                ),
              ),
            ),
          Expanded(
            child: async.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (err, _) => _ErrorView(
                message: err.toString(),
                onRetry: () => ref.invalidate(obligationsProvider(agreementId)),
              ),
              data: (state) => _ObligationsList(
                agreementId: agreementId,
                state: state,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// State: live list + cached fallback
// ---------------------------------------------------------------------------

@immutable
class ObligationsViewState {
  const ObligationsViewState({
    required this.obligations,
    required this.fromCache,
  });

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ObligationsViewState &&
          other.fromCache == fromCache &&
          _listEquals(other.obligations, obligations);

  @override
  int get hashCode => Object.hash(
        fromCache,
        Object.hashAll(obligations.map((o) => o.id).toList()),
      );

  final List<Obligation> obligations;
  final bool fromCache;
}

final obligationsProvider = FutureProvider.autoDispose
    .family<ObligationsViewState, String>((ref, agreementId) async {
  final repo = ref.watch(obligationsRepositoryProvider);
  final cache = ref.watch(offlineCacheProvider);

  final key = OfflineCache.obligationsKey(agreementId);
  try {
    final items = await repo.list(agreementId);
    // Refresh the cache in the background; failure is non-fatal.
    unawaited(cache.putObligations(
      key,
      items.map((o) => o.toJson()).toList(),
    ));
    return ObligationsViewState(obligations: items, fromCache: false);
  } catch (_) {
    final cached = await cache.getObligations(key);
    if (cached == null) rethrow;
    return ObligationsViewState(
      obligations: cached.map(Obligation.fromJson).toList(),
      fromCache: true,
    );
  }
});

class _ObligationsList extends ConsumerWidget {
  const _ObligationsList({required this.agreementId, required this.state});

  final String agreementId;
  final ObligationsViewState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.obligations.isEmpty) {
      return const Center(child: Text('No obligations recorded'));
    }

    final open = state.obligations
        .where((o) =>
            o.status != ObligationStatus.completed &&
            o.status != ObligationStatus.waived)
        .toList();
    final done = state.obligations
        .where((o) =>
            o.status == ObligationStatus.completed ||
            o.status == ObligationStatus.waived)
        .toList();

    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        if (state.fromCache)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              'Saved copy — may be out of date',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ...open.map((o) => _ObligationCard(
              obligation: o,
              agreementId: agreementId,
            )),
        if (done.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Completed',
              style: Theme.of(context).textTheme.titleSmall),
          ...done.map((o) => _ObligationCard(
                obligation: o,
                agreementId: agreementId,
              )),
        ],
      ],
    );
  }
}

class _ObligationCard extends ConsumerWidget {
  const _ObligationCard({required this.obligation, required this.agreementId});

  final Obligation obligation;
  final String agreementId;

  Color get _statusColor => switch (obligation.status) {
        ObligationStatus.overdue => Colors.red,
        ObligationStatus.due => Colors.orange,
        ObligationStatus.completed => Colors.green,
        ObligationStatus.waived => Colors.blueGrey,
        ObligationStatus.disputed => Colors.purple,
        ObligationStatus.upcoming => Colors.blue,
      };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final busy = ref.watch(_updatingProvider(obligation.id));

    return Card(
      clipBehavior: Clip.antiAlias,
      child: ListTile(
        leading: obligation.status == ObligationStatus.completed
            ? const Icon(Icons.check_circle, color: Colors.green)
            : Icon(Icons.radio_button_unchecked, color: _statusColor),
        title: Text(obligation.description),
        subtitle: Wrap(
          spacing: 12,
          children: [
            Text(obligation.ownerParty),
            if (obligation.dueDate != null)
              Text(
                'Due ${_fmt(obligation.dueDate!)}',
                style: TextStyle(
                  color: obligation.status == ObligationStatus.overdue
                      ? Colors.red
                      : null,
                ),
              ),
            if (obligation.amount != null) Text(obligation.amount!),
          ],
        ),
        trailing: busy
            ? const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : Chip(
                label: Text(
                  obligation.status.label,
                  style: TextStyle(fontSize: 11, color: _statusColor),
                ),
                backgroundColor: _statusColor.withValues(alpha: 0.1),
              ),
        onTap: obligation.status == ObligationStatus.completed || busy
            ? null
            : () => _markCompleted(context, ref),
      ),
    );
  }

  Future<void> _markCompleted(BuildContext context, WidgetRef ref) async {
    final notifier =
        ref.read(_updatingProvider(obligation.id).notifier);
    notifier.state = true;
    try {
      await ref.read(obligationsRepositoryProvider).updateStatus(
            agreementId,
            obligation.id,
            ObligationStatus.completed,
          );
      ref.invalidate(obligationsProvider(agreementId));
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not update: $e')),
        );
      }
    } finally {
      notifier.state = false;
    }
  }

  String _fmt(DateTime d) =>
      '${d.day}/${d.month}/${d.year}';
}

final _updatingProvider =
    StateProvider.family<bool, String>((ref, _) => false);

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, size: 48, color: Colors.red),
            const SizedBox(height: 16),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}

bool _listEquals(List<Obligation> a, List<Obligation> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i].id != b[i].id || a[i].status != b[i].status) return false;
  }
  return true;
}
