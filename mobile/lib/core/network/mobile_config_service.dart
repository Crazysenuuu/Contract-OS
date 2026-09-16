import 'package:contractos_mobile/core/config/app_config.dart';
import 'package:contractos_mobile/core/network/api_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Mirror of backend GET /api/v1/mobile/config (spec M2.02 / 55-56).
class MobileConfig {
  const MobileConfig({
    required this.minimumSupportedVersion,
    required this.recommendedVersion,
    required this.maintenance,
  });

  final String minimumSupportedVersion;
  final String recommendedVersion;
  final bool maintenance;

  factory MobileConfig.fromJson(Map<String, dynamic> json) => MobileConfig(
        minimumSupportedVersion: json['minimum_supported_version'] as String? ??
            AppConfig.appVersion,
        recommendedVersion:
            json['recommended_version'] as String? ?? AppConfig.appVersion,
        maintenance: json['maintenance'] as bool? ?? false,
      );
}

class MobileConfigService {
  MobileConfigService(this._client);
  final ApiClient _client;

  Future<MobileConfig> fetch() async {
    final resp = await _client.dio.get('/mobile/config');
    return MobileConfig.fromJson(resp.data as Map<String, dynamic>);
  }
}

final mobileConfigServiceProvider = Provider<MobileConfigService>((ref) {
  return MobileConfigService(ref.watch(apiClientProvider));
});

final mobileConfigProvider = FutureProvider<MobileConfig>((ref) async {
  return ref.watch(mobileConfigServiceProvider).fetch();
});