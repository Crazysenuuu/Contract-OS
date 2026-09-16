import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_controller.dart';
import '../../core/live/live_update_providers.dart';
import '../../core/notifications/push_notification_service.dart';
import '../shell/home_shell.dart';
import 'login_page.dart';

/// Routes between splash, login and the authenticated shell, and starts
/// the push pipeline once the session is live (spec 2.02 §24).
class AuthGate extends ConsumerStatefulWidget {
  const AuthGate({super.key});

  @override
  ConsumerState<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends ConsumerState<AuthGate> {
  bool _pushStarted = false;

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authControllerProvider);

    // WebSocket live updates follow the session (spec 2.02 §30): started
    // while authenticated, stopped on sign-out.
    ref.watch(liveUpdateLifecycleProvider);

    ref.listen<AuthState>(authControllerProvider, (previous, next) {
      if (next.status == AuthStatus.authenticated &&
          previous?.status != AuthStatus.authenticated &&
          !_pushStarted) {
        _pushStarted = true;
        ref.read(pushNotificationServiceProvider).initialize();
      }
      if (next.status == AuthStatus.unauthenticated && _pushStarted) {
        _pushStarted = false;
        ref.read(pushNotificationServiceProvider).tearDown();
      }
    });

    switch (auth.status) {
      case AuthStatus.unknown:
        return const _Splash('Signing in…');
      case AuthStatus.authenticated:
        return const HomeShell();
      case AuthStatus.unauthenticated:
      case AuthStatus.mfaRequired:
        return const LoginPage();
    }
  }
}

class _Splash extends StatelessWidget {
  const _Splash(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const CircularProgressIndicator(),
            const SizedBox(height: 24),
            Text(message),
          ],
        ),
      ),
    );
  }
}
