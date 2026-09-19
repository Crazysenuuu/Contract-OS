import 'package:flutter_test/flutter_test.dart';

import 'package:contractos_mobile/features/agreements/search_query_parser.dart';

void main() {
  final parser = SearchQueryParser(now: () => DateTime(2027, 3, 12));

  group('SearchQueryParser (spec §27 mobile search)', () {
    test('"show all contracts expiring this month" → expiry window', () {
      final p = parser.parse('Show all contracts expiring this month');
      expect(p.expiryFrom, DateTime(2027, 3, 12));
      expect(p.expiryTo, DateTime(2027, 3, 31));
      expect(p.status, isNull);
      expect(p.text, isNull, reason: 'filler words are stripped');
      final q = p.toQueryParameters();
      expect(q['expiry_from'], '2027-03-12');
      expect(q['expiry_to'], '2027-03-31');
      expect(q.containsKey('q'), isFalse);
    });

    test('status phrases map to canonical backend statuses', () {
      expect(parser.parse('pending signature').status, 'signing');
      expect(parser.parse('agreements pending approval').status, 'pending_approval');
      expect(parser.parse('partially signed').status, 'partially_signed');
      expect(parser.parse('active').status, 'active');
    });

    test('longest phrase wins over its sub-word', () {
      // "pending signature" must not be read as status "signed".
      expect(parser.parse('pending signature with acme').status, 'signing');
    });

    test('counterparty is extracted and title-cased', () {
      final p = parser.parse('active NDAs with acme corp');
      expect(p.status, 'active');
      expect(p.party, 'Acme Corp');
      expect(p.text, 'ndas');
    });

    test('numeric windows: expiring within 30 days', () {
      final p = parser.parse('expiring within 30 days');
      expect(p.expiryFrom, DateTime(2027, 3, 12));
      expect(p.expiryTo, DateTime(2027, 4, 11));
    });

    test('next month window', () {
      final p = parser.parse('contracts ending next month');
      expect(p.expiryFrom, DateTime(2027, 4, 1));
      expect(p.expiryTo, DateTime(2027, 4, 30));
    });

    test('plain text query stays as full-text search', () {
      final p = parser.parse('software development');
      expect(p.hasFilters, isFalse);
      expect(p.text, 'software development');
      expect(p.toQueryParameters()['q'], 'software development');
    });

    test('never invents filters from empty input', () {
      final p = parser.parse('   ');
      expect(p.isEmpty, isTrue);
    });

    test('describe() explains the interpretation to the user', () {
      final chips = parser.parse('active with Acme expiring this month').describe();
      expect(chips, contains('status: active'));
      expect(chips, contains('counterparty: Acme'));
      expect(chips.any((c) => c.startsWith('expires 2027-03-12')), isTrue);
    });
  });
}
