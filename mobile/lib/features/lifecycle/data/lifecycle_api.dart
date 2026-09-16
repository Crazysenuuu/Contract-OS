import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:contractos_mobile/core/network/api_client.dart';

// ---------------------------------------------------------------------------
// Models (mirrors of GET /agreements/{id}/renewal and /terminations)
// ---------------------------------------------------------------------------

class ContractRenewal {
  const ContractRenewal({
    required this.isRenewable,
    required this.autoRenew,
    required this.renewalTermMonths,
    required this.currentRenewalCount,
    required this.status,
    this.currentExpiryDate,
    this.nextRenewalDate,
    this.noticePeriodDays,
    this.noticeGiven,
    this.renewalHistory = const [],
  });

  final bool isRenewable;
  final bool autoRenew;
  final int renewalTermMonths;
  final int currentRenewalCount;
  final String status;
  final DateTime? currentExpiryDate;
  final DateTime? nextRenewalDate;
  final int? noticePeriodDays;
  final bool? noticeGiven;
  final List<Map<String, dynamic>> renewalHistory;

  factory ContractRenewal.fromJson(Map<String, dynamic> j) =>
      ContractRenewal(
        isRenewable: j['is_renewable'] as bool? ?? false,
        autoRenew: j['auto_renew'] as bool? ?? false,
        renewalTermMonths: (j['renewal_term_months'] as num?)?.toInt() ?? 0,
        currentRenewalCount:
            (j['current_renewal_count'] as num?)?.toInt() ?? 0,
        status: j['status'] as String? ?? 'active',
        currentExpiryDate: _date(j['current_expiry_date']),
        nextRenewalDate: _date(j['next_renewal_date']),
        noticePeriodDays: (j['notice_period_days'] as num?)?.toInt(),
        noticeGiven: j['notice_given'] as bool?,
        renewalHistory:
            (j['renewal_history'] as List?)?.cast<Map<String, dynamic>>() ??
                const [],
      );

  static DateTime? _date(Object? raw) =>
      raw == null ? null : DateTime.tryParse(raw.toString());

  Map<String, dynamic> toJson() => {
        'is_renewable': isRenewable,
        'auto_renew': autoRenew,
        'renewal_term_months': renewalTermMonths,
        'current_renewal_count': currentRenewalCount,
        'status': status,
        if (currentExpiryDate != null)
          'current_expiry_date': currentExpiryDate!.toIso8601String(),
        if (nextRenewalDate != null)
          'next_renewal_date': nextRenewalDate!.toIso8601String(),
        if (noticePeriodDays != null) 'notice_period_days': noticePeriodDays,
        if (noticeGiven != null) 'notice_given': noticeGiven,
        'renewal_history': renewalHistory,
      };
}

class AgreementTermination {
  const AgreementTermination({
    required this.id,
    required this.reasonCode,
    required this.status,
    required this.initiatedAt,
    this.reasonDetail,
    this.noticeDate,
    this.noticeServed,
    this.cureRequired,
    this.cured,
    this.effectiveDate,
  });

  final String id;
  final String reasonCode;
  final String status;
  final DateTime initiatedAt;
  final String? reasonDetail;
  final DateTime? noticeDate;
  final bool? noticeServed;
  final bool? cureRequired;
  final bool? cured;
  final DateTime? effectiveDate;

  factory AgreementTermination.fromJson(Map<String, dynamic> j) =>
      AgreementTermination(
        id: j['id'] as String,
        reasonCode: j['reason_code'] as String? ?? 'unknown',
        status: j['status'] as String? ?? 'initiated',
        initiatedAt: DateTime.tryParse(j['initiated_at'] as String? ?? '') ??
            DateTime.now(),
        reasonDetail: j['reason_detail'] as String?,
        noticeDate: _date(j['notice_date']),
        noticeServed: j['notice_served'] as bool?,
        cureRequired: j['cure_required'] as bool?,
        cured: j['cured'] as bool?,
        effectiveDate: _date(j['effective_date']),
      );

  static DateTime? _date(Object? raw) =>
      raw == null ? null : DateTime.tryParse(raw.toString());
}

// ---------------------------------------------------------------------------
// Repository (paths mirror the backend routers: renewal lives under
// /agreements/{id}/renewal, terminations under /agreements/{id}/terminations)
// ---------------------------------------------------------------------------

class RenewalRepository {
  RenewalRepository(this._dio);

  final Dio _dio;

  Future<ContractRenewal> get(String agreementId) async {
    final resp = await _dio.get('/agreements/$agreementId/renewal');
    return ContractRenewal.fromJson(resp.data as Map<String, dynamic>);
  }

  /// Serves a non-renewal notice (spec 1.18.8-14). The backend endpoint
  /// takes no body: it marks notice as given as of today.
  Future<ContractRenewal> serveNonRenewalNotice(String agreementId) async {
    final resp = await _dio.post(
      '/agreements/$agreementId/renewal/notice',
    );
    return ContractRenewal.fromJson(resp.data as Map<String, dynamic>);
  }
}

class TerminationsRepository {
  TerminationsRepository(this._dio);

  final Dio _dio;

  Future<List<AgreementTermination>> list(String agreementId) async {
    final resp = await _dio.get('/agreements/$agreementId/terminations');
    final items = (resp.data as List?) ?? const [];
    return items
        .cast<Map<String, dynamic>>()
        .map(AgreementTermination.fromJson)
        .toList();
  }

  /// Initiates a termination (spec 1.18.15-20). Returns the created record.
  Future<AgreementTermination> initiate({
    required String agreementId,
    required String reasonCode,
    String? reasonDetail,
    int? noticePeriodDays,
    bool cureRequired = false,
    int? curePeriodDays,
  }) async {
    final resp = await _dio.post(
      '/agreements/$agreementId/terminations',
      data: {
        'reason_code': reasonCode,
        'reason_detail': ?reasonDetail,
        'notice_period_days': ?noticePeriodDays,
        'cure_required': cureRequired,
        'cure_period_days': ?curePeriodDays,
      },
    );
    return AgreementTermination.fromJson(resp.data as Map<String, dynamic>);
  }

  /// Serves the termination notice.
  Future<AgreementTermination> serveNotice(
    String agreementId,
    String terminationId,
  ) async {
    final resp = await _dio.post(
      '/agreements/$agreementId/terminations/$terminationId/notice',
      data: const {},
    );
    return AgreementTermination.fromJson(resp.data as Map<String, dynamic>);
  }

  /// Completes a termination once notice has been served.
  Future<AgreementTermination> complete(
    String agreementId,
    String terminationId, {
    DateTime? effectiveDate,
  }) async {
    final resp = await _dio.post(
      '/agreements/$agreementId/terminations/$terminationId/complete',
      data: {
        if (effectiveDate != null)
          'effective_date': effectiveDate.toIso8601String().substring(0, 10),
      },
    );
    return AgreementTermination.fromJson(resp.data as Map<String, dynamic>);
  }
}

final renewalRepositoryProvider = Provider<RenewalRepository>((ref) {
  return RenewalRepository(ref.watch(apiClientProvider).dio);
});

final terminationsRepositoryProvider = Provider<TerminationsRepository>((ref) {
  return TerminationsRepository(ref.watch(apiClientProvider).dio);
});
