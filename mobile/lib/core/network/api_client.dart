import 'package:contractos_mobile/core/config/app_config.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Shared Dio client for all API calls (spec M2.02 / 2.06.27).
class ApiClient {
  ApiClient({Dio? dio}) : dio = dio ?? Dio();

  final Dio dio;

  static const _timeout = Duration(seconds: 20);

  ApiClient withBaseUrl() {
    dio.options.baseUrl = AppConfig.defaultApiBaseUrl;
    dio.options.connectTimeout = _timeout;
    dio.options.receiveTimeout = _timeout;
    dio.options.headers['Accept'] = 'application/json';
    return this;
  }

  /// Attach a bearer token for authenticated calls.
  void setAuthToken(String? token) {
    if (token == null) {
      dio.options.headers.remove('Authorization');
    } else {
      dio.options.headers['Authorization'] = 'Bearer $token';
    }
  }
}

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient().withBaseUrl();
});