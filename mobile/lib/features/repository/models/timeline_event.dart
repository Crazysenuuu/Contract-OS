/// A lifecycle/timeline event (spec 2.07.39 timeline screen).
class TimelineEvent {
  const TimelineEvent({
    required this.type,
    required this.timestamp,
    required this.description,
  });

  final String type;
  final DateTime? timestamp;
  final String description;

  factory TimelineEvent.fromJson(Map<String, dynamic> json) => TimelineEvent(
        type: json['type'] as String? ?? '',
        timestamp: DateTime.tryParse(json['timestamp'] as String? ?? ''),
        description: json['description'] as String? ?? '',
      );
}