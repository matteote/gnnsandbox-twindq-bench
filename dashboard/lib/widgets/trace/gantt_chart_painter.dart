import 'package:flutter/material.dart';
import '../../models/trace_event.dart';

class GanttChartPainter extends CustomPainter {
  final List<ProcessedTraceEvent> events;
  final DateTime traceStartTime;
  final Duration traceDuration;
  final Map<String, int> rowIndices;
  final Function(Map<String, Rect>) onLayout;

  GanttChartPainter({
    required this.events,
    required this.traceStartTime,
    required this.traceDuration,
    required this.rowIndices,
    required this.onLayout,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint();
    final double rowHeight = 40.0;
    final double barHeight = 20.0;
    final eventPositions = <String, Rect>{};

    // Group events by root span
    final rootSpanGroups = <String, List<ProcessedTraceEvent>>{};
    final rootSpanOrder = <String>[];

    // Identify root spans
    for (final event in events) {
      if (event.parentSpanId == null) {
        rootSpanGroups[event.spanId] = [];
        rootSpanOrder.add(event.spanId);
      }
    }

    // Assign events to root span groups
    for (final event in events) {
      String rootSpanId = _findRootSpanId(event, events);
      if (rootSpanGroups.containsKey(rootSpanId)) {
        rootSpanGroups[rootSpanId]!.add(event);
      }
    }

    // Calculate positions for each root span group
    const double pixelsPerMs = 0.05;
    const double segmentPadding = 50.0;
    double currentX = 0.0;

    for (final rootSpanId in rootSpanOrder) {
      final group = rootSpanGroups[rootSpanId]!;
      if (group.isEmpty) continue;

      // Find group boundaries
      final groupStart = group
          .map((e) => e.startTime)
          .reduce((a, b) => a.isBefore(b) ? a : b);

      // Sort events hierarchically (DFS pre-order)
      final sortedGroup = _sortEventsHierarchically(group);

      // Calculate the max visual width needed for this group to update currentX correctly
      double maxVisualRight = 0.0;

      // Position events within this group
      for (int i = 0; i < sortedGroup.length; i++) {
        final event = sortedGroup[i];

        // Indent based on hierarchy level
        final double indentation = event.level * 20.0;

        // Calculate position relative to this group's start
        final double eventStart =
            event.startTime.difference(groupStart).inMilliseconds * pixelsPerMs;
        final double eventDuration =
            event.endTime.difference(event.startTime).inMilliseconds *
            pixelsPerMs;

        final double left = currentX + indentation + eventStart;
        final double width = eventDuration.clamp(
          5.0,
          double.infinity,
        ); // Minimum 5px width

        // Vertical position is based on pre-calculated row index
        final int rowIndex = rowIndices[event.spanId] ?? 0;
        final double top = rowIndex * rowHeight + (rowHeight - barHeight) / 2;

        final rect = Rect.fromLTWH(left, top, width, barHeight);
        eventPositions[event.spanId] = rect;

        // Track max visual width
        // Calculate text width to ensure next group doesn't overlap
        final labelText = _getLabelText(event);
        final textPainter = TextPainter(
          text: TextSpan(
            text: labelText,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
          ),
          textDirection: TextDirection.ltr,
        );
        textPainter.layout();

        const double textPadding = 5.0;
        double visualRight = indentation + eventStart + width;

        // If text doesn't fit in bar, it extends to the right
        if (textPainter.width + (textPadding * 2) > width) {
          visualRight =
              indentation +
              eventStart +
              width +
              textPadding +
              textPainter.width;
        }

        if (visualRight > maxVisualRight) {
          maxVisualRight = visualRight;
        }
      }

      // Move X position for next root span group
      final groupWidth = maxVisualRight < 200.0 ? 200.0 : maxVisualRight;
      currentX += groupWidth + segmentPadding;
    }
    onLayout(eventPositions);
    // Draw all bars
    for (int i = 0; i < events.length; i++) {
      final event = events[i];
      final rect = eventPositions[event.spanId];
      if (rect == null) continue;

      // Draw the bar with rounded corners
      final rRect = RRect.fromRectAndRadius(rect, const Radius.circular(4.0));

      if (event.isInProgress) {
        // For in-progress events, use semi-transparent fill with border
        paint.style = PaintingStyle.fill;
        paint.color = event.eventColor.withOpacity(0.3);
        canvas.drawRRect(rRect, paint);

        // Draw border to make it stand out
        paint.style = PaintingStyle.stroke;
        paint.strokeWidth = 2.0;
        paint.color = event.eventColor;
        canvas.drawRRect(rRect, paint);

        // Reset paint style
        paint.style = PaintingStyle.fill;
      } else {
        // For completed events, draw solid color
        paint.color = event.eventColor;
        canvas.drawRRect(rRect, paint);
      }

      // Draw the operation label inside the bar
      const double textPadding = 5.0;
      final labelText = _getLabelText(event);

      final textPainter = TextPainter(
        text: TextSpan(
          text: labelText,
          style: TextStyle(
            color: event.isInProgress ? event.eventColor : Colors.white,
            fontSize: 12,
            fontWeight: FontWeight.bold,
          ),
        ),
        textDirection: TextDirection.ltr,
      );

      // Layout without max width first to check if it fits naturally
      textPainter.layout();

      // Center text vertically in the bar with padding
      final textX = rect.left + textPadding;
      final textY = rect.top + (barHeight - textPainter.height) / 2;

      // Only paint text if it fits within the bar
      if (textPainter.width + (textPadding * 2) <= rect.width) {
        textPainter.paint(canvas, Offset(textX, textY));
      } else {
        // If text doesn't fit, draw it outside to the right
        final outsideTextPainter = TextPainter(
          text: TextSpan(
            text: labelText,
            style: TextStyle(
              color: event
                  .eventColor, // Use event color since it's on white background
              fontSize: 12,
              fontWeight: FontWeight.bold,
            ),
          ),
          textDirection: TextDirection.ltr,
        );

        outsideTextPainter.layout();

        final outsideX = rect.right + textPadding;
        final outsideY = rect.top + (barHeight - outsideTextPainter.height) / 2;

        outsideTextPainter.paint(canvas, Offset(outsideX, outsideY));
      }
    }
  }

  List<ProcessedTraceEvent> _sortEventsHierarchically(
    List<ProcessedTraceEvent> events,
  ) {
    if (events.isEmpty) return [];

    // Build adjacency list
    final childrenMap = <String, List<ProcessedTraceEvent>>{};
    final allSpanIds = events.map((e) => e.spanId).toSet();

    for (final event in events) {
      if (event.parentSpanId != null) {
        final parentId = event.parentSpanId!;
        if (!childrenMap.containsKey(parentId)) {
          childrenMap[parentId] = [];
        }
        childrenMap[parentId]!.add(event);
      }
    }

    // Find roots (events with no parent or parent not in this group)
    final roots = events
        .where(
          (e) => e.parentSpanId == null || !allSpanIds.contains(e.parentSpanId),
        )
        .toList();

    // Sort roots by start time
    roots.sort((a, b) => a.startTime.compareTo(b.startTime));

    final sorted = <ProcessedTraceEvent>[];

    void traverse(ProcessedTraceEvent current) {
      sorted.add(current);

      if (childrenMap.containsKey(current.spanId)) {
        final children = childrenMap[current.spanId]!;
        children.sort((a, b) => a.startTime.compareTo(b.startTime));
        for (final child in children) {
          traverse(child);
        }
      }
    }

    for (final r in roots) {
      traverse(r);
    }

    return sorted;
  }

  String _getLabelText(ProcessedTraceEvent event) {
    String baseText = event.operationName;

    if (event.details != null) {
      if (event.eventType.contains('MODEL')) {
        if (event.details!.containsKey('model_version')) {
          baseText = event.details!['model_version'];
        }
      } else if (event.eventType.contains('TOOL')) {
        if (event.details!.containsKey('tool_name')) {
          baseText = event.details!['tool_name'];
        }
      } else if (event.eventType.contains('AGENT')) {
        if (event.details!.containsKey('agent_name')) {
          baseText = event.details!['agent_name'];
        }
      }
    }

    return event.isInProgress
        ? '$baseText (in progress...)'
        : '$baseText (${event.duration.inMilliseconds}ms)';
  }

  String _findRootSpanId(
    ProcessedTraceEvent event,
    List<ProcessedTraceEvent> allEvents,
  ) {
    String rootSpanId = event.spanId;
    String? currentId = event.spanId;

    // Traverse up to find the root span
    while (currentId != null) {
      final currentEvent = allEvents.firstWhere(
        (e) => e.spanId == currentId,
        orElse: () => event,
      );

      if (currentEvent.parentSpanId == null) {
        rootSpanId = currentEvent.spanId;
        break;
      }

      // Move to parent
      currentId = currentEvent.parentSpanId;

      // Find the parent event
      final parentExists = allEvents.any((e) => e.spanId == currentId);
      if (!parentExists) {
        // Parent not found, current is root
        rootSpanId = currentEvent.spanId;
        break;
      }
    }

    return rootSpanId;
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) {
    return true;
  }
}
