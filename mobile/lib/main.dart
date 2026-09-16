import 'package:contractos_mobile/app.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:contractos_mobile/core/notifications/push_banner_actions.dart';
import 'package:contractos_mobile/core/notifications/push_banner_controller.dart';
import 'package:contractos_mobile/core/offline/offline_cache.dart';
import 'package:contractos_mobile/core/offline/sqflite_cache_store.dart';
import 'package:contractos_mobile/core/security/certificate_pinning.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Certificate pinning (spec 2.02 §57): validated on the shared client
  // when API_CERT_PINS is provided for this build.
  final client = ApiClient().withBaseUrl();
  attachCertificatePinning(client.dio);

  // Offline cache (spec 2.02 §34-35): bind the on-device sqflite store;
  // a platform failure degrades gracefully to the in-memory store.
  CacheStore cacheStore;
  try {
    cacheStore = await SqfliteCacheStore.open();
  } catch (_) {
    cacheStore = MemoryCacheStore();
  }

  runApp(
    ProviderScope(
      overrides: [
        // Banner actions navigate via the root navigator key defined in
        // push_banner_actions.dart (registered on MaterialApp below).
        pushBannerActionHandlerProvider
            .overrideWithValue(NavigatorPushBannerActionHandler()),
        offlineCacheStoreProvider.overrideWithValue(cacheStore),
      ],
      child: const ContractOSApp(),
    ),
  );
}
