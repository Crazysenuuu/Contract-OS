import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/live/live_update_providers.dart';
import '../../core/live/live_update_service.dart';

/// Live WebSocket status strip (spec 2.02 §30), pinned to the top of the
/// authenticated shell so the connection state is visible on every tab.
///
/// Always rendered (no layout jump between states): tinted green and quiet
/// ("Live") when healthy; amber/blue while connecting; loud error styling
/// while reconnecting or offline — those are the states the user must know
/// about, because live approvals and signature requests aren't arriving.
class LiveConnectionIndicator extends ConsumerWidget {
  const LiveConnectionIndicator({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(liveConnectionStateProvider).value ??
        LiveConnectionState.disconnected;
    final scheme = Theme.of(context).colorScheme;

    final (Color color, String label, IconData icon) = switch (state) {
      LiveConnectionState.connected => (
          const Color(0xFF2E7D32),
          'Live',
          Icons.circle,
        ),
      LiveConnectionState.connecting => (
          scheme.primary,
          'Connecting…',
          Icons.cloud_sync_outlined,
        ),
      LiveConnectionState.reconnecting => (
          scheme.error,
          'Reconnecting…',
          Icons.cloud_off_outlined,
        ),
      LiveConnectionState.disconnected => (
          scheme.outline,
          'Offline',
          Icons.cloud_off_outlined,
        ),
    };

    return Material(
      color: color.withValues(alpha: 0.10),
      child: SafeArea(
        top: true,
        bottom: false,
        child: SizedBox(
          height: 24,
          child: Row(
            children: [
              const SizedBox(width: 12),
              Icon(icon, size: 10, color: color),
              const SizedBox(width: 6),
              Text(
                label,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: color,
                    ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
