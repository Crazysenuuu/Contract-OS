import 'dart:async';
import 'dart:typed_data';

import 'package:contractos_mobile/core/auth/auth_interceptor.dart';
import 'package:contractos_mobile/core/auth/auth_api.dart';
import 'package:contractos_mobile/core/auth/secure_token_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

class _MockTokenStore extends Mock implements SecureTokenStore {}

class _MockAuthApi extends Mock implements AuthApi {}

/// Records how the interceptor terminated an error: resolved (retry
/// succeeded) or nexted (passed through / session expired).
class _ErrHandler extends ErrorInterceptorHandler {
  final _done = Completer<void>();
  Response<dynamic>? resolved;
  DioException? nexted;

  void _finish() {
    if (!_done.isCompleted) _done.complete();
  }

  @override
  void resolve(Response response) {
    resolved = response;
    _finish();
  }

  @override
  void next(DioException err) {
    nexted = err;
    _finish();
  }

  Future<void> get done => _done.future;
}

RequestOptions _opts(String path) =>
    RequestOptions(path: path, baseUrl: 'http://localhost:8000/api/v1');

DioException _unauthorized(String path) => DioException(
      requestOptions: _opts(path),
      response: Response(requestOptions: _opts(path), statusCode: 401),
    );

void main() {
  late _MockTokenStore store;
  late _MockAuthApi api;
  late Dio dio;
  late AuthInterceptor interceptor;

  setUp(() {
    store = _MockTokenStore();
    api = _MockAuthApi();

    when(() => store.readAccessToken()).thenAnswer((_) async => 'token-v1');
    when(() => store.clear()).thenAnswer((_) async {});

    dio = Dio(BaseOptions(baseUrl: 'http://localhost:8000/api/v1'))
      ..httpClientAdapter = _FakeAdapter();
    interceptor = AuthInterceptor(dio, store, api);
    dio.interceptors.add(interceptor);
  });

  group('single-flight refresh', () {
    test('concurrent 401s trigger exactly one /auth/refresh', () async {
      var refreshCalls = 0;
      final refreshGate = Completer<void>();
      when(() => api.refresh()).thenAnswer((_) async {
        refreshCalls++;
        await refreshGate.future;
        return 'token-v2';
      });

      // Fire three concurrent 401s; onError is async-void, so completion
      // is observed through each handler.
      final handlers = List.generate(3, (_) => _ErrHandler());
      for (var i = 0; i < 3; i++) {
        interceptor.onError(_unauthorized('/agreements/$i'), handlers[i]);
      }

      // Let all three reach the refresh gate, then release it.
      await Future<void>.delayed(const Duration(milliseconds: 10));
      refreshGate.complete();
      await Future.wait(handlers.map((h) => h.done));

      expect(refreshCalls, 1, reason: 'only one refresh must be issued');
      for (final h in handlers) {
        expect(h.resolved, isNotNull, reason: 'retried with the new token');
        expect(h.nexted, isNull);
      }
      verifyNever(() => store.clear());
    });

    test('failed refresh clears credentials and reports session expiry',
        () async {
      when(() => api.refresh()).thenThrow(SessionExpiredException());
      var expired = false;
      interceptor.onSessionExpired = () => expired = true;

      final handler = _ErrHandler();
      interceptor.onError(_unauthorized('/agreements'), handler);
      await handler.done;

      expect(expired, isTrue);
      expect(handler.nexted, isNotNull);
      verify(() => store.clear()).called(1);
    });

    test('401 from /auth/refresh is not refreshed again', () async {
      final handler = _ErrHandler();
      interceptor.onError(_unauthorized('/auth/refresh'), handler);
      await handler.done;

      verifyNever(() => api.refresh());
      verifyNever(() => store.clear());
    });

    test('non-401 errors pass through untouched', () async {
      final handler = _ErrHandler();
      final err = DioException(
        requestOptions: _opts('/agreements'),
        response: Response(
          requestOptions: _opts('/agreements'),
          statusCode: 403,
        ),
      );
      interceptor.onError(err, handler);
      await handler.done;

      verifyNever(() => api.refresh());
      verifyNever(() => store.clear());
      expect(handler.nexted, same(err));
    });

    test('already-retried 401 ends the session instead of looping', () async {
      when(() => api.refresh()).thenAnswer((_) async => 'token-v2');
      var expired = false;
      interceptor.onSessionExpired = () => expired = true;

      final options = _opts('/agreements')..extra['__auth_retried__'] = true;
      final handler = _ErrHandler();
      interceptor.onError(
        DioException(
          requestOptions: options,
          response: Response(requestOptions: options, statusCode: 401),
        ),
        handler,
      );
      await handler.done;

      expect(expired, isTrue);
      verify(() => store.clear()).called(1);
      verifyNever(() => api.refresh());
    });

    test('refresh failure while another refresh is in flight joins it',
        () async {
      // First call fails; a second 401 arriving during the same refresh
      // window must observe the same failure without a second call.
      var refreshCalls = 0;
      final gate = Completer<void>();
      when(() => api.refresh()).thenAnswer((_) async {
        refreshCalls++;
        await gate.future;
        throw SessionExpiredException();
      });

      final h1 = _ErrHandler();
      final h2 = _ErrHandler();
      interceptor.onError(_unauthorized('/a'), h1);
      await Future<void>.delayed(const Duration(milliseconds: 10));
      interceptor.onError(_unauthorized('/b'), h2);
      await Future<void>.delayed(const Duration(milliseconds: 10));
      gate.complete();
      await Future.wait([h1.done, h2.done]);

      expect(refreshCalls, 1);
      verify(() => store.clear()).called(2);
    });
  });
}

/// Adapter that never performs I/O — retries resolve with a dummy response.
class _FakeAdapter implements HttpClientAdapter {
  @override
  void close({bool force = false}) {}

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    return ResponseBody.fromString('{"ok": true}', 200);
  }
}
