import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:contractos_mobile/core/network/api_client.dart';

// ---------------------------------------------------------------------------
// Models (mirror of GET /agreements/{id}/obligations — ObligationResponse)
// ---------------------------------------------------------------------------

enum ObligationStatus {
  upcoming,
  due,
  overdue,
  completed,
  waived,
  disputed;

  static ObligationStatus tryParse(String? raw) => switch (raw) {
        'due' => ObligationStatus.due,
        'overdue' => ObligationStatus.overdue,
        'completed' => ObligationStatus.completed,
        'waived' => ObligationStatus.waived,
        'disputed' => ObligationStatus.disputed,
        _ => ObligationStatus.upcoming,
      };

  String get label => switch (this) {
        ObligationStatus.upcoming => 'Upcoming',
        ObligationStatus.due => 'Due',
        ObligationStatus.overdue => 'Overdue',
        ObligationStatus.completed => 'Completed',
        ObligationStatus.waived => 'Waived',
        ObligationStatus.disputed => 'Disputed',
      };
}

class Obligation {
  const Obligation({
    required this.id,
    required this.description,
    required this.ownerParty,
    required this.status,
    this.dueDate,
    this.amount,
    this.frequency,
    this.clauseIdentifier,
  });

  final String id;
  final String description;
  final String ownerParty;
  final ObligationStatus status;
  final DateTime? dueDate;
  final String? amount;
  final String? frequency;
  final String? clauseIdentifier;

  factory Obligation.fromJson(Map<String, dynamic> j) => Obligation(
        id: j['id'] as String,
        description: j['description'] as String? ?? '',
        ownerParty: j['owner_party'] as String? ?? 'Unknown',
        status: ObligationStatus.tryParse(j['status'] as String?),
        dueDate: j['due_date'] != null
            ? DateTime.tryParse(j['due_date'] as String)
            : null,
        amount: j['amount'] as String?,
        frequency: j['frequency'] as String?,
        clauseIdentifier: j['clause_identifier'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'description': description,
        'owner_party': ownerParty,
        'status': status.name,
        if (dueDate != null) 'due_date': dueDate!.toIso8601String(),
        if (amount != null) 'amount': amount,
        if (frequency != null) 'frequency': frequency,
        if (clauseIdentifier != null) 'clause_identifier': clauseIdentifier,
      };
}

class ObligationStats {
  const ObligationStats({
    required this.total,
    required this.upcoming,
    required this.due,
    required this.completed,
    required this.overdue,
    required this.dueWithin30Days,
  });

  final int total;
  final int upcoming;
  final int due;
  final int completed;
  final int overdue;
  final int dueWithin30Days;

  factory ObligationStats.fromJson(Map<String, dynamic> j) => ObligationStats(
        total: (j['total'] as num?)?.toInt() ?? 0,
        upcoming: (j['upcoming'] as num?)?.toInt() ?? 0,
        due: (j['due'] as num?)?.toInt() ?? 0,
        completed: (j['completed'] as num?)?.toInt() ?? 0,
        overdue: (j['overdue'] as num?)?.toInt() ?? 0,
        dueWithin30Days: (j['due_within_30_days'] as num?)?.toInt() ?? 0,
      );
}

// ---------------------------------------------------------------------------
// Repository
// ---------------------------------------------------------------------------

class ObligationsRepository {
  ObligationsRepository(this._dio);

  final Dio _dio;

  Future<List<Obligation>> list(String agreementId, {String? status}) async {
    final resp = await _dio.get(
      '/agreements/$agreementId/obligations',
      queryParameters: {'obligation_status': ?status},
    );
    final items = (resp.data as List?) ?? const [];
    return items
        .cast<Map<String, dynamic>>()
        .map(Obligation.fromJson)
        .toList();
  }

  Future<Obligation> updateStatus(
    String agreementId,
    String obligationId,
    ObligationStatus status,
  ) async {
    final resp = await _dio.patch(
      '/agreements/$agreementId/obligations/$obligationId/status',
      data: {'status': status.name},
    );
    return Obligation.fromJson(resp.data as Map<String, dynamic>);
  }

  Future<ObligationStats> stats(String agreementId) async {
    final resp =
        await _dio.get('/agreements/$agreementId/obligations/stats');
    return ObligationStats.fromJson(resp.data as Map<String, dynamic>);
  }
}

final obligationsRepositoryProvider = Provider<ObligationsRepository>((ref) {
  return ObligationsRepository(ref.watch(apiClientProvider).dio);
});
