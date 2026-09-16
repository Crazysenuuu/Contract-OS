/// App-level static configuration (non-secret, mirror of backend
/// /api/v1/mobile/config defaults).
library;

class AppConfig {
  AppConfig._();

  /// Current running build. Bumped on release; the backend gate lives in
  /// MOBILE_MINIMUM_VERSION / MOBILE_RECOMMENDED_VERSION env vars.
  static const String appVersion = '1.0.0';

  static const String defaultApiBaseUrl = 'http://localhost:8000/api/v1';
}