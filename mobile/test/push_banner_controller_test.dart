import 'package:contractos_mobile/core/notifications/push_banner_controller.dart';
import 'package:fake_async/fake_async.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late ProviderContainer container;

  setUp(() {
    PushBannerController.autoDismissAfter = const Duration(seconds: 5);
    container = ProviderContainer();
    addTearDown(container.dispose);
  });

  PushBannerController controller() =>
      container.read(pushBannerControllerProvider.notifier);

  RemoteMessage remote(
    String title, {
    String? body,
    Map<String, dynamic> data = const {},
    String? messageId,
  }) {
    final msg = RemoteMessage(
      senderId: 'test-sender',
      messageId: messageId ?? 'msg-$title',
      data: data,
      notification: const RemoteNotification(title: '', body: ''),
    );
    // RemoteNotification/RemoteMessage are immutable; rebuild with content.
    return RemoteMessage(
      senderId: msg.senderId,
      messageId: messageId,
      data: {'title': title, 'body': body ?? '', ...data},
      notification: RemoteNotification(title: title, body: body ?? ''),
    );
  }

  group('PushBannerController', () {
    test('starts empty', () {
      expect(container.read(pushBannerControllerProvider), isEmpty);
    });

    test('show adds banner and newest appears first', () {
      final c = controller();
      c.show(const PushBanner(id: 'a', title: 'A', body: 'x'));
      c.show(const PushBanner(id: 'b', title: 'B', body: 'y'));
      expect(
        container.read(pushBannerControllerProvider).map((b) => b.id),
        ['b', 'a'],
      );
    });

    test('same id replaces (moves to front) instead of duplicating', () {
      final c = controller();
      c.show(const PushBanner(id: 'a', title: 'A1', body: ''));
      c.show(const PushBanner(id: 'b', title: 'B', body: ''));
      c.show(const PushBanner(id: 'a', title: 'A2', body: ''));
      final ids = container
          .read(pushBannerControllerProvider)
          .map((b) => b.id)
          .toList();
      expect(ids, ['a', 'b']);
      expect(
        container.read(pushBannerControllerProvider).first.title,
        'A2',
      );
    });

    test('queue is bounded to 3 visible banners', () {
      final c = controller();
      for (var i = 0; i < 5; i++) {
        c.show(PushBanner(id: 'b$i', title: 'B$i', body: ''));
      }
      final ids = container
          .read(pushBannerControllerProvider)
          .map((b) => b.id)
          .toList();
      expect(ids, ['b4', 'b3', 'b2']);
    });

    test('dismiss removes and cancels the pending auto-dismiss', () {
      fakeAsync((async) {
        final c = controller();
        c.show(const PushBanner(id: 'a', title: 'A', body: ''));
        c.dismiss('a');
        expect(container.read(pushBannerControllerProvider), isEmpty);

        // If the timer were still armed, elapsing it must not throw.
        async.elapse(const Duration(seconds: 6));
        expect(container.read(pushBannerControllerProvider), isEmpty);
      });
    });

    test('auto-dismisses after the configured delay', () {
      fakeAsync((async) {
        final c = controller();
        c.show(const PushBanner(id: 'a', title: 'A', body: ''));
        c.show(const PushBanner(id: 'b', title: 'B', body: ''));
        expect(container.read(pushBannerControllerProvider), hasLength(2));

        async.elapse(const Duration(seconds: 5));
        expect(container.read(pushBannerControllerProvider), isEmpty);
      });
    });

    test('re-show resets the auto-dismiss window', () {
      fakeAsync((async) {
        final c = controller();
        c.show(const PushBanner(id: 'a', title: 'A', body: ''));
        async.elapse(const Duration(seconds: 3));
        c.show(const PushBanner(id: 'a', title: 'A2', body: ''));
        async.elapse(const Duration(seconds: 3)); // old window would expire
        expect(container.read(pushBannerControllerProvider), hasLength(1));
        async.elapse(const Duration(seconds: 2));
        expect(container.read(pushBannerControllerProvider), isEmpty);
      });
    });

    test('clearAll empties the queue and cancels timers', () {
      fakeAsync((async) {
        final c = controller();
        c.show(const PushBanner(id: 'a', title: 'A', body: ''));
        c.clearAll();
        expect(container.read(pushBannerControllerProvider), isEmpty);

        async.elapse(const Duration(seconds: 10));
        expect(container.read(pushBannerControllerProvider), isEmpty);
      });
    });

    test('showFromRemoteMessage maps notification + data payload', () {
      final c = controller();
      c.showFromRemoteMessage(
        remote('Approval needed', body: 'MSA v2 awaits signature'),
      );
      final banners = container.read(pushBannerControllerProvider);
      expect(banners, hasLength(1));
      expect(banners.first.title, 'Approval needed');
      expect(banners.first.body, 'MSA v2 awaits signature');
    });

    test('message id (or data notification_id) becomes the banner id',
        () {
      final c = controller();
      c.showFromRemoteMessage(
        remote('T1', data: {'notification_id': 'db-42'}),
      );
      expect(container.read(pushBannerControllerProvider).first.id, 'db-42');

      c.showFromRemoteMessage(remote('T2', messageId: 'fcm-abc'));
      expect(container.read(pushBannerControllerProvider).first.id,
          'fcm-abc');
    });
  });
}
