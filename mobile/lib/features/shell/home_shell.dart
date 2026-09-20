import 'package:contractos_mobile/features/agreements/agreements_page.dart';
import '../ai_assistant/ai_assistant_page.dart';
import '../approvals/approvals_page.dart';
import 'package:contractos_mobile/features/contracts/contracts_page.dart';
import 'package:contractos_mobile/features/notifications/notifications_page.dart';
import 'package:contractos_mobile/features/profile/profile_page.dart';
import 'package:contractos_mobile/features/repository/presentation/repository_page.dart';
import 'package:contractos_mobile/features/shell/live_connection_indicator.dart';
import 'package:contractos_mobile/features/task_hub/task_hub_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/deeplink/deep_link_service.dart';
import '../../core/offline/connectivity.dart';

/// The authenticated mobile shell (spec 2.02 focused app):
/// Tasks / Contracts / Agreements / Signing / Approvals / AI / Alerts / Profile.
class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> {
  int _index = 0;

  static final _pages = <Widget>[
    const TaskHubPage(),
    const ContractsPage(),
    const AgreementsPage(),
    const RepositoryPage(),
    const ApprovalsPage(),
    const AiAssistantPage(),
    const NotificationsPage(),
    const ProfilePage(),
  ];

  @override
  Widget build(BuildContext context) {
    // Deep links start once the shell (and thus the navigator) exists.
    ref.watch(deepLinkStartProvider);
    final online = ref.watch(isOnlineProvider).value ?? true;

    return Scaffold(
      // Offline notice (spec 2.02 §34): cached views remain browsable.
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
          const LiveConnectionIndicator(),
          Expanded(
            child: MediaQuery.removePadding(
              context: context,
              removeTop: true,
              child: IndexedStack(index: _index, children: _pages),
            ),
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.task_alt_outlined),
            selectedIcon: Icon(Icons.task_alt),
            label: 'Tasks',
          ),
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            selectedIcon: Icon(Icons.folder),
            label: 'Contracts',
          ),
          NavigationDestination(
            icon: Icon(Icons.description_outlined),
            selectedIcon: Icon(Icons.description),
            label: 'Agreements',
          ),
          NavigationDestination(
            icon: Icon(Icons.pending_actions_outlined),
            selectedIcon: Icon(Icons.pending_actions),
            label: 'Signing',
          ),
          NavigationDestination(
            icon: Icon(Icons.approval_outlined),
            selectedIcon: Icon(Icons.approval),
            label: 'Approvals',
          ),
          NavigationDestination(
            icon: Icon(Icons.auto_awesome_outlined),
            selectedIcon: Icon(Icons.auto_awesome),
            label: 'AI',
          ),
          NavigationDestination(
            icon: Icon(Icons.notifications_outlined),
            selectedIcon: Icon(Icons.notifications),
            label: 'Alerts',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: 'Profile',
          ),
        ],
      ),
    );
  }
}

