import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/config/app_config.dart';
import 'core/deeplink/deep_link_service.dart';
import 'core/network/mobile_config_service.dart';
import 'core/notifications/push_banner_actions.dart';
import 'core/notifications/push_banner_overlay.dart';
import 'features/onboarding/auth_gate.dart';
import 'features/signing/presentation/signature_page.dart';
import 'features/signing/presentation/signing_entry_page.dart';

class ContractOSApp extends ConsumerWidget {
  const ContractOSApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: 'ContractOS',
      navigatorKey: rootNavigatorKey,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1B5E8F)),
        useMaterial3: true,
      ),
      // Foreground push messages surface here (spec 2.02 §25): the overlay
      // wraps every route via builder so banners show on any screen.
      builder: (context, child) => Stack(
        children: [
          ?child,
          const Positioned(top: 0, left: 0, right: 0, child: PushBannerOverlay()),
        ],
      ),
      // Deep links (spec 2.02 §23): starts app_links listeners as soon as
      // the navigator exists so cold-start links are not dropped.
      home: const _DeepLinkBoot(child: _VersionGate()),
      // Named routes (spec 2.06 signing flow). ReviewPage carries the
      // signing session id as route arguments; a bare arrive lands on the
      // token-exchange entry for push/deep-link arrivals.
      onGenerateRoute: (settings) {
        if (settings.name == '/signing/auth') {
          final arg = settings.arguments;
          final Widget page = (arg is String && arg.isNotEmpty)
              ? SignaturePage(sessionId: arg)
              : const SigningEntryPage();
          return MaterialPageRoute<void>(
            settings: settings,
            builder: (_) => page,
          );
        }
        return null;
      },
    );
  }
}

/// Mounts [deepLinkStartProvider] above the app so links are handled from
/// the first frame.
class _DeepLinkBoot extends ConsumerWidget {
  const _DeepLinkBoot({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(deepLinkStartProvider);
    return child;
  }
}

/// Checks the minimum supported client version (spec 2.02 §55-56) before
/// handing over to the auth gate.
class _VersionGate extends ConsumerWidget {
  const _VersionGate();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final config = ref.watch(mobileConfigProvider);

    return config.when(
      loading: () => const _Blocked('Contacting ContractOS…', spinner: true),
      error: (err, _) => _Blocked('Unable to reach the service.\n$err'),
      data: (cfg) {
        if (cfg.maintenance) {
          return const _Blocked(
            'Scheduled maintenance in progress.\nPlease try again shortly.',
          );
        }
        if (_isObsolete(cfg.minimumSupportedVersion)) {
          return const _Blocked(
            'This app version is no longer supported.\nPlease update from your app store.',
          );
        }
        return const AuthGate();
      },
    );
  }

  /// Compare dotted versions — simple lexical check replaced by a semver
  /// package in the production build.
  bool _isObsolete(String minimum) {
    final parts = (AppConfig.appVersion.split('.').map(num.parse)).toList();
    final minParts = (minimum.split('.').map(num.parse)).toList();
    for (var i = 0; i < minParts.length && i < parts.length; i++) {
      if (parts[i] < minParts[i]) return true;
      if (parts[i] > minParts[i]) return false;
    }
    return parts.length < minParts.length;
  }
}

class _Blocked extends StatelessWidget {
  const _Blocked(this.message, {this.spinner = false});

  final String message;
  final bool spinner;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (spinner) ...[
                const CircularProgressIndicator(),
                const SizedBox(height: 24),
              ],
              Text(message, textAlign: TextAlign.center),
            ],
          ),
        ),
      ),
    );
  }
}
