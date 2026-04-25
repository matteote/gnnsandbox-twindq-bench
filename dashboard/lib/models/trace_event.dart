import 'package:flutter/material.dart';

class ProcessedTraceEvent {
  final String traceId;
  final String spanId;
  final String? parentSpanId;
  final String operationName;
  final DateTime startTime;
  final DateTime endTime;
  final Duration duration;
  final int level;
  final String eventType;
  final bool isInProgress;
  final Map<String, dynamic>? details;

  ProcessedTraceEvent({
    required this.traceId,
    required this.spanId,
    this.details,
    this.parentSpanId,
    required this.operationName,
    required this.startTime,
    required this.endTime,
    required this.level,
    required this.eventType,
    this.isInProgress = false,
  }) : duration = endTime.difference(startTime);

  Color get eventColor {
    if (eventType.contains('AGENT')) {
      return const Color(0xFF2196F3); // Blue - Agent calls
    } else if (eventType.contains('TOOL')) {
      return const Color(0xFF4CAF50); // Green - Tool calls
    } else if (eventType.contains('MODEL')) {
      return const Color(0xFFFF9800); // Orange - Model calls
    } else if (eventType.contains('ERROR')) {
      return const Color(0xFFF44336); // Red - Errors
    }
    return const Color(0xFF9E9E9E); // Grey - Other
  }
  
  String get eventTypeLabel {
    if (eventType.contains('AGENT')) {
      return '🤖 Agent';
    } else if (eventType.contains('TOOL')) {
      return '🔧 Tool';
    } else if (eventType.contains('MODEL')) {
      return '🧠 Model';
    } else if (eventType.contains('ERROR')) {
      return '❌ Error';
    }
    return '📋 Other';
  }
}
