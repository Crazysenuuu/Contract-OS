import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Connectivity tracker (spec 2.02 §34): the UI shows an offline banner and
/// cached-data notices while [isOnlineProvider] reports false. Tests
/// override this provider.
final isOnlineProvider = StreamProvider<bool>((ref) {
  return Connectivity()
      .onConnectivityChanged
      .map((results) => results.any((c) => c != ConnectivityResult.none));
});
