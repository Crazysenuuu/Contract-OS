import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';

import '../../core/network/api_client.dart';

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

enum NotifPriority { urgent, normal, low }

class MobileNotification {
  const MobileNotification({
    required this.id,
    required this.title,
    required this.body,
    required this.createdAt,
    required this.priority,
    this.isRead = false,
    this.agreementId,
    this.actionUrl,
  });

  final String id;
  final String title;
  final String body;
  final DateTime createdAt;
  final NotifPriority priority;
  final bool isRead;
  final String? agreementId;
  final String? actionUrl;

  factory MobileNotification.fromJson(Map<String, dynamic> j) {
    NotifPriority parsePriority(String? p) => switch (p) {
          'urgent' || 'critical' => NotifPriority.urgent,
          'low' => NotifPriority.low,
          _ => NotifPriority.normal,
        };

    return MobileNotification(
      id: j['id'] as String,
      title: j['title'] as String? ?? 'Notification',
      body: j['body'] as String? ?? '',
      createdAt: DateTime.tryParse(j['created_at'] as String? ?? '') ??
          DateTime.now(),
      priority: parsePriority(j['priority'] as String?),
      isRead: (j['is_read'] as bool?) ?? false,
      agreementId: j['agreement_id'] as String?,
      actionUrl: j['action_url'] as String?,
    );
  }

  MobileNotification copyWith({bool? isRead}) => MobileNotification(
        id: id,
        title: title,
        body: body,
        createdAt: createdAt,
        priority: priority,
        isRead: isRead ?? this.isRead,
        agreementId: agreementId,
        actionUrl: actionUrl,
      );
}

// ---------------------------------------------------------------------------
// Repository
// ---------------------------------------------------------------------------

class NotificationsRepository {
  NotificationsRepository(this._dio);
  final Dio _dio;

  Future<List<MobileNotification>> list({bool unreadOnly = false}) async {
    final params = <String, dynamic>{'limit': 50};
    if (unreadOnly) params['unread_only'] = true;
    final resp =
        await _dio.get('/api/v1/notifications', queryParameters: params);
    final items = (resp.data['items'] as List?) ??
        (resp.data as List?) ??
        <dynamic>[];
    return items
        .cast<Map<String, dynamic>>()
        .map(MobileNotification.fromJson)
        .toList();
  }

  Future<void> markRead(String id) async {
    await _dio.patch('/api/v1/notifications/$id/read');
  }

  Future<void> markAllRead() async {
    await _dio.post('/api/v1/notifications/mark-all-read');
  }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final notificationsRepositoryProvider =
    Provider<NotificationsRepository>((ref) {
  return NotificationsRepository(ref.watch(apiClientProvider).dio);
});

class NotificationsNotifier
    extends AsyncNotifier<List<MobileNotification>> {
  @override
  Future<List<MobileNotification>> build() async {
    return ref.watch(notificationsRepositoryProvider).list();
  }

  Future<void> markRead(String id) async {
    await ref.read(notificationsRepositoryProvider).markRead(id);
    final current = state.value ?? [];
    state = AsyncData(
      current
          .map((n) => n.id == id ? n.copyWith(isRead: true) : n)
          .toList(),
    );
  }

  Future<void> markAllRead() async {
    await ref.read(notificationsRepositoryProvider).markAllRead();
    final current = state.value ?? [];
    state = AsyncData(
      current.map((n) => n.copyWith(isRead: true)).toList(),
    );
  }
}

final notificationsProvider =
    AsyncNotifierProvider.autoDispose<NotificationsNotifier,
        List<MobileNotification>>(NotificationsNotifier.new);

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

class NotificationsPage extends ConsumerWidget {
  const NotificationsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(notificationsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          if (async.value?.any((n) => !n.isRead) == true)
            TextButton(
              onPressed: () =>
                  ref.read(notificationsProvider.notifier).markAllRead(),
              child: const Text('Mark all read'),
            ),
        ],
      ),
      body: async.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) => _ErrorView(
          err.toString(),
          onRetry: () => ref.invalidate(notificationsProvider),
        ),
        data: (notifs) {
          if (notifs.isEmpty) {
            return const _EmptyView();
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(notificationsProvider),
            child: ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: notifs.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, i) => _NotifTile(notif: notifs[i]),
            ),
          );
        },
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Notification tile
// ---------------------------------------------------------------------------

class _NotifTile extends ConsumerWidget {
  const _NotifTile({required this.notif});
  final MobileNotification notif;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    Color priorityColor() => switch (notif.priority) {
          NotifPriority.urgent => Colors.red,
          NotifPriority.low => Colors.grey,
          NotifPriority.normal => theme.colorScheme.primary,
        };

    IconData priorityIcon() => switch (notif.priority) {
          NotifPriority.urgent => Icons.warning_amber_rounded,
          NotifPriority.low => Icons.info_outline,
          NotifPriority.normal => Icons.notifications_outlined,
        };

    return ListTile(
      leading: CircleAvatar(
        backgroundColor: priorityColor().withValues(alpha: 0.12),
        child: Icon(priorityIcon(), color: priorityColor(), size: 20),
      ),
      title: Text(
        notif.title,
        style: TextStyle(
          fontWeight: notif.isRead ? FontWeight.normal : FontWeight.w600,
        ),
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(notif.body, maxLines: 2, overflow: TextOverflow.ellipsis),
          Text(
            _timeAgo(notif.createdAt),
            style: theme.textTheme.labelSmall
                ?.copyWith(color: Colors.grey.shade500),
          ),
        ],
      ),
      isThreeLine: true,
      tileColor: notif.isRead ? null : theme.colorScheme.primary.withValues(alpha: 0.04),
      trailing: notif.isRead
          ? null
          : Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: theme.colorScheme.primary,
                shape: BoxShape.circle,
              ),
            ),
      onTap: () {
        if (!notif.isRead) {
          ref.read(notificationsProvider.notifier).markRead(notif.id);
        }
        // Navigate to agreement if linked
        if (notif.agreementId != null) {
          // Navigation wiring is handled by the push_banner_actions.dart deep-link handler
        }
      },
    );
  }

  String _timeAgo(DateTime dt) {
    final diff = DateTime.now().difference(dt);
    if (diff.inDays > 0) return '${diff.inDays}d ago';
    if (diff.inHours > 0) return '${diff.inHours}h ago';
    if (diff.inMinutes > 0) return '${diff.inMinutes}m ago';
    return 'just now';
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
          const Icon(Icons.notifications_none, size: 64, color: Colors.grey),
          const SizedBox(height: 16),
          Text(
            'You\'re all caught up!',
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
  const _ErrorView(this.message, {required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.cloud_off, size: 48, color: Colors.grey),
          const SizedBox(height: 16),
          Text(message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.grey)),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('Retry'),
          ),
        ],
      ),
    );
  }
}