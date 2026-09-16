import 'package:contractos_mobile/core/security/biometric_auth.dart';
import 'package:contractos_mobile/features/agreements/biometric_gate.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeBiometricAuth implements BiometricAuth {
  _FakeBiometricAuth({this.available = true, this.result = true});

  bool available;
  bool result;
  int authenticateCalls = 0;
  String? lastReason;

  @override
  Future<bool> isAvailable() async => available;

  @override
  Future<bool> authenticate(String reason) async {
    authenticateCalls++;
    lastReason = reason;
    return result;
  }
}

Future<void> _pumpGate(
  WidgetTester tester,
  _FakeBiometricAuth auth,
) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [biometricAuthProvider.overrideWithValue(auth)],
      child: const MaterialApp(
        home: Scaffold(
          body: BiometricGate(
            child: Text('SECRET CONTRACT CONTENT'),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
}

void main() {
  testWidgets('content is hidden until availability check completes',
      (tester) async {
    final auth = _FakeBiometricAuth();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [biometricAuthProvider.overrideWithValue(auth)],
        child: const MaterialApp(
          home: Scaffold(body: BiometricGate(child: Text('SECRET'))),
        ),
      ),
    );
    // First pump: availability future not yet resolved.
    expect(find.text('SECRET'), findsNothing);

    await tester.pumpAndSettle();
    // Available -> locked, so still hidden.
    expect(find.text('SECRET'), findsNothing);
    expect(find.text('Agreements are locked'), findsOneWidget);
  });

  testWidgets('no biometrics available renders content unlocked',
      (tester) async {
    final auth = _FakeBiometricAuth(available: false);
    await _pumpGate(tester, auth);

    expect(find.text('SECRET CONTRACT CONTENT'), findsOneWidget);
    expect(find.text('Agreements are locked'), findsNothing);
    expect(auth.authenticateCalls, 0);
  });

  testWidgets('unlock button runs the prompt and reveals content on success',
      (tester) async {
    final auth = _FakeBiometricAuth();
    await _pumpGate(tester, auth);

    await tester.tap(find.text('Unlock'));
    await tester.pumpAndSettle();

    expect(auth.authenticateCalls, 1);
    expect(auth.lastReason, contains('agreements'));
    expect(find.text('SECRET CONTRACT CONTENT'), findsOneWidget);
  });

  testWidgets('failed authentication keeps content locked', (tester) async {
    final auth = _FakeBiometricAuth(result: false);
    await _pumpGate(tester, auth);

    await tester.tap(find.text('Unlock'));
    await tester.pumpAndSettle();

    expect(auth.authenticateCalls, 1);
    expect(find.text('SECRET CONTRACT CONTENT'), findsNothing);
    expect(find.text('Agreements are locked'), findsOneWidget);
    // Retry is offered.
    expect(find.text('Unlock'), findsOneWidget);
  });

  testWidgets('backgrounding the app re-locks revealed content',
      (tester) async {
    final auth = _FakeBiometricAuth();
    await _pumpGate(tester, auth);

    await tester.tap(find.text('Unlock'));
    await tester.pumpAndSettle();
    expect(find.text('SECRET CONTRACT CONTENT'), findsOneWidget);

    // Simulate leaving the app (recents switch / background) with the
    // real state sequence the OS delivers.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pumpAndSettle();

    expect(find.text('SECRET CONTRACT CONTENT'), findsNothing);
    expect(find.text('Agreements are locked'), findsOneWidget);
  });

  testWidgets('resuming after background triggers the biometric prompt',
      (tester) async {
    final auth = _FakeBiometricAuth();
    await _pumpGate(tester, auth);

    await tester.tap(find.text('Unlock'));
    await tester.pumpAndSettle();

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();

    // The resume itself re-ran the prompt (2 prompts total) and revealed.
    expect(auth.authenticateCalls, 2);
    expect(find.text('SECRET CONTRACT CONTENT'), findsOneWidget);
  });
}
