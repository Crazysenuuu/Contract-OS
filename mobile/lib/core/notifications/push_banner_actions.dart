import 'package:flutter/material.dart';

import '../../features/signing/presentation/signing_entry_page.dart';
import 'push_banner_controller.dart';

/// Root navigator key: lets banner actions navigate without a BuildContext
/// (banners live above the Navigator, inside MaterialApp.builder).
final rootNavigatorKey = GlobalKey<NavigatorState>();

/// Opens the signing flow for a verified signing-request banner.
///
/// The banner's biometric step-up has already succeeded when this runs
/// (the overlay enforces it); here we only translate the payload into
/// navigation:
///   - `data.token` (or `data.signing_token`) -> SigningEntryPage(token)
///     — the one-time signer token from spec 2.06.27-28.
///   - no token -> SigningEntryPage without one (shows its "link required"
///     state) rather than dropping the user somewhere opaque.
class NavigatorPushBannerActionHandler implements PushBannerActionHandler {
  @override
  void openSigningRequest(PushBanner banner) {
    final navigator = rootNavigatorKey.currentState;
    if (navigator == null) return;

    final raw = banner.data['token'] ??
        banner.data['signing_token'] ??
        banner.route; // '/signing/<token>' style deep links
    final token = raw?.toString();
    final signingToken =
        (token == null || token.isEmpty) ? null : token.split('/').last;

    navigator.push(
      MaterialPageRoute<void>(
        builder: (_) => SigningEntryPage(token: signingToken),
      ),
    );
  }
}
