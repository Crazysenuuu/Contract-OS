import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../security/biometric_auth.dart';
import 'push_banner_controller.dart';

/// Stack of in-app banners for foreground push messages, rendered inside
/// the MaterialApp [MaterialApp.banner] slot so it overlays every screen
/// without a Navigator overlay or per-scaffold wiring.
class PushBannerOverlay extends ConsumerWidget {
  const PushBannerOverlay({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final banners = ref.watch(pushBannerControllerProvider);
    if (banners.isEmpty) return const SizedBox.shrink();

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (final banner in banners)
          MaterialBanner(
            content: _BannerContent(banner: banner),
            leading: Icon(
              banner.isSigningRequest
                  ? Icons.draw_outlined
                  : Icons.notifications_active_outlined,
            ),
            actions: [
              // Signing requests demand biometric step-up (spec 2.02 §23)
              // before the signing flow can open.
              if (banner.isSigningRequest)
                _SigningOpenButton(banner: banner),
              TextButton(
                onPressed: () => ref
                    .read(pushBannerControllerProvider.notifier)
                    .dismiss(banner.id),
                child: const Text('Dismiss'),
              ),
            ],
          ),
      ],
    );
  }
}

/// Open action on a signing-request banner: fingerprint/device-PIN first,
/// then navigate. Failure (cancel, lockout, no match) keeps the banner so
/// the user can retry.
class _SigningOpenButton extends ConsumerStatefulWidget {
  const _SigningOpenButton({required this.banner});

  final PushBanner banner;

  @override
  ConsumerState<_SigningOpenButton> createState() =>
      _SigningOpenButtonState();
}

class _SigningOpenButtonState extends ConsumerState<_SigningOpenButton> {
  bool _checking = false;

  Future<void> _open() async {
    if (_checking) return;
    setState(() => _checking = true);
    try {
      final ok = await ref
          .read(biometricAuthProvider)
          .authenticate('Verify your identity to open the signing request');
      if (!mounted) return;
      if (!ok) return; // keep the banner for a retry

      ref
          .read(pushBannerControllerProvider.notifier)
          .dismiss(widget.banner.id);
      ref
          .read(pushBannerActionHandlerProvider)?.openSigningRequest(
                widget.banner,
              );
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: _checking ? null : _open,
      icon: _checking
          ? const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.fingerprint, size: 18),
      label: Text(_checking ? 'Verifying…' : 'Open'),
    );
  }
}

class _BannerContent extends StatelessWidget {
  const _BannerContent({required this.banner});

  final PushBanner banner;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          banner.title,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        if (banner.body.isNotEmpty)
          Text(
            banner.body,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 12),
          ),
      ],
    );
  }
}
