import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Device push-token registration (spec M2.02 section 26):
///   app start/resume → retrieve provider token → send to authenticated
///   backend → upsert device record (no duplicates).
class DeviceRegistrationService {
  DeviceRegistrationService(this._client);

  final ApiClient _client;

  Future<void> register({
    required String deviceId,
    String? pushToken,
    String? appVersion,
  }) async {
    await _client.dio.post('/mobile/devices', data: {
      'device_id': deviceId,
      'platform': _platform,
      'push_token': pushToken,
      'app_version': appVersion,
    });
  }
}

String get _platform {
  if (const bool.fromEnvironment('dart.library.io')) {
    if (const bool.hasEnvironment('FLUTTER_TARGET') &&
        const String.fromEnvironment('FLUTTER_TARGET') == 'android') {
      return 'android';
    }
    return 'ios';
  }
  return 'web';
}

final deviceRegistrationServiceProvider = Provider<DeviceRegistrationService>(
  (ref) => DeviceRegistrationService(ref.watch(apiClientProvider)),
);

/// Stable per-install device id, persisted across launches (spec 26).
final deviceIdProvider = FutureProvider<String>((ref) async {
  const key = 'contractos_device_id';
  final prefs = await SharedPreferences.getInstance();
  final existing = prefs.getString(key);
  if (existing != null && existing.isNotEmpty) return existing;
  final uuid = DateTime.now().microsecondsSinceEpoch.toRadixString(36);
  await prefs.setString(key, uuid);
  return uuid;
});