class LogEntry {
  final String timestamp;
  final String severity;
  final String message;
  final String source;
  final Map<String, dynamic> details;

  LogEntry({
    required this.timestamp,
    required this.severity,
    required this.message,
    required this.source,
    this.details = const {},
  });

  factory LogEntry.fromJson(Map<String, dynamic> json) {
    return LogEntry(
      timestamp: json['timestamp'] ?? DateTime.now().toIso8601String(),
      // Use 'severity' but fall back to 'level' for backward compatibility.
      severity: json['severity'] ?? json['level'] ?? 'UNKNOWN',
      message: json['message'] ?? '',
      source: json['source'] ?? 'Unknown',
      details: json['details'] ?? {},
    );
  }
}
