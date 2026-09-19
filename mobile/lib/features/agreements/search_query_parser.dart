/// Natural-language search query parser (spec §27 — mobile "Search").
///
/// Turns phrases like
///   "Show all contracts expiring this month"
///   "active NDAs with Acme"
///   "pending signature"
/// into the structured filters accepted by `GET /api/v1/search/agreements`
/// (`q`, `status`, `expiry_from`, `expiry_to`, `party`). Anything that is not
/// recognised as a filter stays in `q` for full-text matching.
///
/// Deliberately deterministic and dependency-free so it is trivially
/// unit-tested and works offline; it never invents a filter it did not
/// see in the text.
class ParsedSearchQuery {
  const ParsedSearchQuery({
    this.text,
    this.status,
    this.party,
    this.expiryFrom,
    this.expiryTo,
  });

  final String? text;
  final String? status;
  final String? party;
  final DateTime? expiryFrom;
  final DateTime? expiryTo;

  bool get hasFilters =>
      status != null || party != null || expiryFrom != null || expiryTo != null;

  bool get isEmpty => !hasFilters && (text == null || text!.isEmpty);

  /// Query parameters for the search endpoint (dates as ISO-8601 `YYYY-MM-DD`).
  Map<String, dynamic> toQueryParameters({int limit = 20}) {
    final params = <String, dynamic>{'limit': limit};
    if (text != null && text!.isNotEmpty) params['q'] = text;
    if (status != null) params['status'] = status;
    if (party != null) params['party'] = party;
    if (expiryFrom != null) params['expiry_from'] = _iso(expiryFrom!);
    if (expiryTo != null) params['expiry_to'] = _iso(expiryTo!);
    return params;
  }

  /// Human-readable chips describing the interpretation shown to the user.
  List<String> describe() {
    final out = <String>[];
    if (status != null) out.add('status: ${status!.replaceAll('_', ' ')}');
    if (party != null) out.add('counterparty: $party');
    if (expiryFrom != null && expiryTo != null) {
      out.add('expires ${_iso(expiryFrom!)} → ${_iso(expiryTo!)}');
    }
    if (text != null && text!.isNotEmpty) out.add('matches "$text"');
    return out;
  }

  static String _iso(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';
}

class SearchQueryParser {
  SearchQueryParser({DateTime Function()? now}) : _now = now ?? DateTime.now;

  final DateTime Function() _now;

  // Status vocabulary → canonical backend status (see
  // backend/app/domain/agreement_states.py).
  static const _statusPhrases = <String, String>{
    'pending signature': 'signing',
    'awaiting signature': 'signing',
    'in signing': 'signing',
    'partially signed': 'partially_signed',
    'ready for signature': 'ready_for_signature',
    'pending approval': 'pending_approval',
    'awaiting approval': 'pending_approval',
    'internal review': 'internal_review',
    'under review': 'internal_review',
    'in negotiation': 'negotiating',
    'negotiating': 'negotiating',
    'approved': 'approved',
    'executed': 'executed',
    'signed': 'executed',
    'expiring': 'expiring',
    'expired': 'expired',
    'terminated': 'terminated',
    'renewed': 'renewed',
    'cancelled': 'cancelled',
    'canceled': 'cancelled',
    'drafts': 'draft',
    'draft': 'draft',
    'active': 'active',
    'sent': 'sent',
  };

  // Filler words removed from the residual full-text query.
  static const _stopWords = <String>{
    'show', 'me', 'all', 'list', 'find', 'contracts', 'contract',
    'agreements', 'agreement', 'the', 'a', 'an', 'that', 'are', 'is',
    'which', 'please', 'my', 'our', 'of', 'and',
  };

  static final _expiryVerb = r'(?:expir\w*|end\w*|due|renew\w*)';

  ParsedSearchQuery parse(String raw) {
    var text = ' ${raw.trim().toLowerCase().replaceAll(RegExp(r'\s+'), ' ')} ';
    if (text.trim().isEmpty) return const ParsedSearchQuery();

    String? status;
    String? party;
    DateTime? expiryFrom;
    DateTime? expiryTo;

    final now = _now();
    final today = DateTime(now.year, now.month, now.day);

    // --- time windows -----------------------------------------------------
    final windows = <RegExp, (DateTime, DateTime) Function(RegExpMatch)>{
      RegExp('\\b$_expiryVerb\\s+(?:this|in the current)\\s+month\\b'): (_) =>
          (today, DateTime(now.year, now.month + 1, 0)),
      RegExp('\\b$_expiryVerb\\s+next\\s+month\\b'): (_) => (
            DateTime(now.year, now.month + 1, 1),
            DateTime(now.year, now.month + 2, 0),
          ),
      RegExp('\\b$_expiryVerb\\s+this\\s+week\\b'): (_) =>
          (today, today.add(Duration(days: 7 - today.weekday))),
      RegExp('\\b$_expiryVerb\\s+(?:this|in the current)\\s+year\\b'): (_) =>
          (today, DateTime(now.year, 12, 31)),
      RegExp('\\b$_expiryVerb\\s+(?:in|within)\\s+(?:the\\s+)?(?:next\\s+)?(\\d+)\\s+days?\\b'):
          (m) => (today, today.add(Duration(days: int.parse(m.group(1)!)))),
      RegExp('\\b$_expiryVerb\\s+(?:in|within)\\s+(?:the\\s+)?(?:next\\s+)?(\\d+)\\s+months?\\b'):
          (m) => (
                today,
                DateTime(now.year, now.month + int.parse(m.group(1)!), now.day),
              ),
    };

    for (final entry in windows.entries) {
      final match = entry.key.firstMatch(text);
      if (match == null) continue;
      final (from, to) = entry.value(match);
      expiryFrom = from;
      expiryTo = to;
      text = text.replaceFirst(match.group(0)!, ' ');
      break;
    }

    // --- status ----------------------------------------------------------
    // Longest phrases first so "pending signature" wins over "signed".
    final phrases = _statusPhrases.keys.toList()
      ..sort((a, b) => b.length.compareTo(a.length));
    for (final phrase in phrases) {
      final re = RegExp('\\b${RegExp.escape(phrase)}\\b');
      if (re.hasMatch(text)) {
        status = _statusPhrases[phrase];
        text = text.replaceFirst(re, ' ');
        break;
      }
    }

    // --- counterparty ----------------------------------------------------
    final withMatch = RegExp(
      r'\b(?:with|from|for|by)\s+([a-z0-9][a-z0-9&.\-]*(?:\s+[a-z0-9&.\-]+){0,4}?)\s*(?=$|\s(?:expir|that|which|and|in|due)\b)',
    ).firstMatch(text.trimRight());
    if (withMatch != null) {
      final candidate = withMatch.group(1)!.trim();
      if (candidate.isNotEmpty && !_stopWords.contains(candidate)) {
        party = _titleCase(candidate);
        text = text.replaceFirst(withMatch.group(0)!, ' ');
      }
    }

    // --- residual full-text query ---------------------------------------
    final residual = text
        .split(' ')
        .where((w) => w.isNotEmpty && !_stopWords.contains(w))
        .join(' ')
        .trim();

    return ParsedSearchQuery(
      text: residual.isEmpty ? null : residual,
      status: status,
      party: party,
      expiryFrom: expiryFrom,
      expiryTo: expiryTo,
    );
  }

  static String _titleCase(String s) => s
      .split(' ')
      .where((w) => w.isNotEmpty)
      .map((w) => w.length <= 3 && RegExp(r'^[a-z]+$').hasMatch(w)
          ? w.toUpperCase()
          : '${w[0].toUpperCase()}${w.substring(1)}')
      .join(' ');
}
