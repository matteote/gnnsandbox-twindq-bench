import 'package:flutter/foundation.dart';

/// Represents an incident from the network monitoring system
class Incident {
  /// Unique identifier for the incident
  final String id;
  
  /// Timestamp when the incident was recorded
  final DateTime recordedTimestamp;
  
  /// Agent task ID associated with the incident
  final String agentTaskId;
  
  /// Issue details as JSON
  final Map<String, dynamic> issue;
  
  /// Cause details as JSON (optional)
  final Map<String, dynamic>? cause;
  
  /// Resolution details as String (optional) - matches Spanner String(MAX) type
  final String? resolution;
  
  /// Timestamp when the incident was resolved (optional)
  final DateTime? resolvedTimestamp;
  
  /// Investigation strategy details (optional)
  final Map<String, dynamic>? strategy;
  
  /// Root cause analysis details as String (optional) - matches Spanner String(MAX) type
  final String? rootCause;
  
  /// Last progress update timestamp
  final DateTime? lastProgressUpdate;

  /// Creates a new incident
  const Incident({
    required this.id,
    required this.recordedTimestamp,
    required this.agentTaskId,
    required this.issue,
    this.cause,
    this.resolution,
    this.resolvedTimestamp,
    this.strategy,
    this.rootCause,
    this.lastProgressUpdate,
  });

  /// Creates a copy of this incident with the given fields replaced with new values
  Incident copyWith({
    String? id,
    DateTime? recordedTimestamp,
    String? agentTaskId,
    Map<String, dynamic>? issue,
    Map<String, dynamic>? cause,
    String? resolution,
    DateTime? resolvedTimestamp,
    Map<String, dynamic>? strategy,
    String? rootCause,
    DateTime? lastProgressUpdate,
  }) {
    return Incident(
      id: id ?? this.id,
      recordedTimestamp: recordedTimestamp ?? this.recordedTimestamp,
      agentTaskId: agentTaskId ?? this.agentTaskId,
      issue: issue ?? this.issue,
      cause: cause ?? this.cause,
      resolution: resolution ?? this.resolution,
      resolvedTimestamp: resolvedTimestamp ?? this.resolvedTimestamp,
      strategy: strategy ?? this.strategy,
      rootCause: rootCause ?? this.rootCause,
      lastProgressUpdate: lastProgressUpdate ?? this.lastProgressUpdate,
    );
  }

  /// Creates an incident from a JSON object
  factory Incident.fromJson(Map<String, dynamic> json) {
    return Incident(
      id: json['id'] ?? DateTime.now().millisecondsSinceEpoch.toString(),
      recordedTimestamp: json['recordedTimestamp'] != null
          ? DateTime.fromMillisecondsSinceEpoch(json['recordedTimestamp'])
          : DateTime.now(),
      agentTaskId: json['agentTaskId'] ?? '',
      issue: json['issue'] != null
          ? Map<String, dynamic>.from(json['issue'])
          : {},
      cause: json['cause'] != null
          ? Map<String, dynamic>.from(json['cause'])
          : null,
      resolution: json['resolution'] as String?,
      resolvedTimestamp: json['resolvedTimestamp'] != null
          ? DateTime.fromMillisecondsSinceEpoch(json['resolvedTimestamp'])
          : null,
      strategy: json['strategy'] != null
          ? Map<String, dynamic>.from(json['strategy'])
          : null,
      rootCause: json['rootCause'] as String?,
      lastProgressUpdate: json['lastProgressUpdate'] != null
          ? DateTime.fromMillisecondsSinceEpoch(json['lastProgressUpdate'])
          : null,
    );
  }

  /// Converts this incident to a JSON object
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'recordedTimestamp': recordedTimestamp.millisecondsSinceEpoch,
      'agentTaskId': agentTaskId,
      'issue': issue,
      'cause': cause,
      'resolution': resolution,
      'resolvedTimestamp': resolvedTimestamp?.millisecondsSinceEpoch,
      'strategy': strategy,
      'rootCause': rootCause,
      'lastProgressUpdate': lastProgressUpdate?.millisecondsSinceEpoch,
    };
  }

  // Helper getters to extract common fields from the issue JSON
  String get title => issue['node'] ?? issue['hostname'] ?? 'Unknown Node';
  String get description => issue['error'] ?? issue['error_text'] ?? issue['message'] ?? issue['description'] ?? 'No error details available';
  String get state {
    if (resolution != null) return 'resolved';
    if (rootCause != null) return 'analyzing';
    if (strategy != null) return 'investigating';
    return 'open';
  }
  String get severity => issue['severity'] ?? 'medium';
  String? get affectedNode => issue['affected_node'];
  DateTime get createdAt => recordedTimestamp;
  DateTime get updatedAt => lastProgressUpdate ?? resolvedTimestamp ?? recordedTimestamp;
  String? get assignedAgent => issue['assigned_agent'];
  
  // Progress tracking getters
  bool get hasStrategy => strategy != null;
  bool get hasRootCause => rootCause != null;
  bool get hasResolution => resolution != null;
  
  String? get strategyDescription => strategy?['description'] ?? strategy?['strategy'];
  String? get rootCauseDescription => rootCause;
  String? get resolutionDescription => resolution;
  
  double get progressPercentage {
    if (hasResolution) return 1.0;
    if (hasRootCause) return 0.75;
    if (hasStrategy) return 0.5;
    return 0.25;
  }
  
  String get progressStage {
    if (hasResolution) return 'Resolved';
    if (hasRootCause) return 'Root Cause Identified';
    if (hasStrategy) return 'Investigating';
    return 'Initial Assessment';
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is Incident &&
        other.id == id &&
        other.recordedTimestamp == recordedTimestamp &&
        other.agentTaskId == agentTaskId &&
        mapEquals(other.issue, issue) &&
        mapEquals(other.cause, cause) &&
        other.resolution == resolution &&
        other.rootCause == rootCause &&
        other.resolvedTimestamp == resolvedTimestamp;
  }

  @override
  int get hashCode {
    return Object.hash(
      id,
      recordedTimestamp,
      agentTaskId,
      issue,
      cause,
      resolution,
      resolvedTimestamp,
    );
  }

  @override
  String toString() {
    return 'Incident(id: $id, title: $title, state: $state, severity: $severity, recordedTimestamp: $recordedTimestamp, agentTaskId: $agentTaskId)';
  }
}
