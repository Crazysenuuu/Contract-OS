import 'package:contractos_mobile/core/config/app_config.dart';
import 'package:contractos_mobile/core/network/mobile_config_service.dart';
import 'package:contractos_mobile/features/shell/home_shell.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Startup gate (spec 55-56): enforces the minimum supported client version
/// returned by GET /api/v1/mobile/config. The mobile client checks this
/// before allowing a signer to continue.
class SplashPage extends ConsumerWidget {
  const SplashPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final config = ref.watch(mobileConfigProvider);

    return config.when(
      loading: () => const _Splash('Contacting ContractOS…'),
      error: (err, _) => _Splash('Unable to reach the service.\n$err'),
      data: (cfg) {
        if (cfg.maintenance) {
          return const _Blocked('Scheduled maintenance in progress.\nPlease try again shortly.');
        }
        if (_isObsolete(cfg.minimumSupportedVersion)) {
          return const _Blocked(
            'This app version is no longer supported.\nPlease update from your app store.',
          );
        }
        return const HomeShell();
      },
    );
  }

  /// Compare dotted versions — this is a simple lexical check that is
  /// replaced by a semver package in the production build.
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
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Text(message, textAlign: TextAlign.center),
            ),
          ],
        ),
      ),
    );
  }
}

class _Blocked extends StatelessWidget {
  const _Blocked(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(message, textAlign: TextAlign.center),
        ),
      ),
    );
  }
}