import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/panel_type.dart';
import '../../models/trace_event.dart';
import '../../screens/full_screen_panel_view.dart';
import 'gantt_chart_painter.dart';
import 'trace_details_dialog.dart';

class TraceWidget extends StatefulWidget {
  final bool isFullScreen;
  const TraceWidget({super.key, this.isFullScreen = false});

  @override
  State<TraceWidget> createState() => _TraceWidgetState();
}

class _TraceWidgetState extends State<TraceWidget> {
  Timer? _updateTimer;
  final Map<String, ScrollController> _scrollControllers = {};
  final Map<String, double> _maxCanvasWidths = {};
  int _previousEventCount = 0;
  final Map<String, Map<String, Rect>> _traceEventPositions = {};

  @override
  void initState() {
    super.initState();
    // Start a timer to update in-progress operations every 16ms (approx 60fps)
    _updateTimer = Timer.periodic(const Duration(milliseconds: 16), (_) {
      if (mounted) {
        setState(() {
          // This will trigger a rebuild to update in-progress operation durations
        });
      }
    });
  }

  @override
  void dispose() {
    _updateTimer?.cancel();
    for (final controller in _scrollControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }
  // ... (keeping existing methods) ...

  void _scrollToEnd(String traceId) {
    final controller = _scrollControllers[traceId];
    bool shouldScroll = true;

    // Check if we should scroll based on current position (before layout update)
    if (controller != null && controller.hasClients) {
      final currentScroll = controller.offset;
      const tolerance = 20.0;

      // With reverse: true, 0.0 is the rightmost edge (latest events).
      // We sticky scroll if we are close to 0.0.
      shouldScroll = currentScroll <= tolerance;
    }

    if (shouldScroll) {
      // Schedule scroll to end after build completes
      WidgetsBinding.instance.addPostFrameCallback((_) {
        final controller = _scrollControllers[traceId];
        if (controller != null && controller.hasClients) {
          // Jump to 0.0 which is the rightmost edge
          controller.jumpTo(0.0);
        }
      });
    }
  }

  List<ProcessedTraceEvent> _processTraceEvents(
    List<Map<String, dynamic>> rawEvents,
  ) {
    final processedEvents = <ProcessedTraceEvent>[];
    final eventMap = <String, Map<String, dynamic>>{};

    // First pass: create a map of all events by span_id
    for (final event in rawEvents) {
      final spanId = event['span_id'] as String;
      eventMap[spanId] = event;
    }

    // Second pass: process each event
    for (final event in rawEvents) {
      if ((event['event_type'] as String).startsWith('BEFORE_')) {
        final spanId = event['span_id'] as String;
        final eventType = (event['event_type'] as String).substring(7);

        // Check if a corresponding AFTER event exists
        final afterEvent = rawEvents.firstWhere(
          (e) =>
              e['span_id'] == spanId &&
              ((e['event_type'] as String).startsWith('AFTER_') ||
                  (e['event_type'] as String).endsWith('_ERROR')),
          orElse: () => <String, dynamic>{},
        );

        final endTime = afterEvent.isNotEmpty
            ? DateTime.parse(afterEvent['timestamp'] as String)
            : DateTime.now().toUtc();

        final details = <String, dynamic>{};
        if (event['details'] != null) {
          details.addAll(event['details'] as Map<String, dynamic>);
        }
        if (afterEvent.isNotEmpty && afterEvent['details'] != null) {
          details.addAll(afterEvent['details'] as Map<String, dynamic>);
        }

        processedEvents.add(
          ProcessedTraceEvent(
            traceId: event['trace_id'] as String,
            spanId: spanId,
            parentSpanId: event['parent_span_id'] as String?,
            operationName: event['operation_name'] as String,
            startTime: DateTime.parse(event['timestamp'] as String),
            endTime: endTime,
            level: _calculateLevel(event, eventMap),
            eventType: eventType,
            details: details.isEmpty ? null : details,
            isInProgress: afterEvent.isEmpty,
          ),
        );
      }
    }

    // Remove duplicates by converting to a map and back to a list
    final uniqueEvents = <String, ProcessedTraceEvent>{};
    for (final event in processedEvents) {
      uniqueEvents[event.spanId] = event;
    }

    return uniqueEvents.values.toList();
  }

  int _calculateLevel(
    Map<String, dynamic> event,
    Map<String, Map<String, dynamic>> eventMap,
  ) {
    int level = 0;
    String? parentId = event['parent_span_id'] as String?;
    while (parentId != null) {
      level++;
      // Find the parent event in the complete map
      final parentEvent = eventMap[parentId];
      if (parentEvent == null) {
        break;
      }
      // Move to the next parent
      parentId = parentEvent['parent_span_id'] as String?;
    }
    return level;
  }

  bool _hasInProgressEvents(List<Map<String, dynamic>> rawEvents) {
    // Check if there are any BEFORE events without matching AFTER events
    for (final event in rawEvents) {
      if (event['event_type'].toString().startsWith('BEFORE_')) {
        final eventType = event['event_type'].toString().substring(7);
        final hasAfterEvent = rawEvents.any(
          (e) =>
              e['span_id'] == event['span_id'] &&
              e['event_type'] == 'AFTER_$eventType',
        );
        if (!hasAfterEvent) {
          return true;
        }
      }
    }
    return false;
  }

  double _calculateConstrainedCanvasWidth(
    String traceId,
    List<ProcessedTraceEvent> events,
  ) {
    final calculatedWidth = _calculateCanvasWidth(events);

    if (events.isEmpty) {
      _maxCanvasWidths.remove(traceId);
      return 0.0;
    }

    final currentMax = _maxCanvasWidths[traceId] ?? 0.0;
    if (calculatedWidth > currentMax) {
      _maxCanvasWidths[traceId] = calculatedWidth;
      return calculatedWidth;
    }

    return currentMax;
  }

  double _calculateCanvasWidth(List<ProcessedTraceEvent> events) {
    if (events.isEmpty) return 0.0;

    // Group events by root span (events with no parent)
    final rootSpanGroups = <String, List<ProcessedTraceEvent>>{};

    // First, identify all root spans
    for (final event in events) {
      if (event.parentSpanId == null) {
        rootSpanGroups[event.spanId] = [];
      }
    }

    // Then, assign all events to their root span group
    for (final event in events) {
      String rootSpanId = event.spanId;
      String? currentId = event.spanId;

      // Traverse up to find the root span
      while (currentId != null) {
        final parentEvent = events.firstWhere(
          (e) => e.spanId == currentId,
          orElse: () => event,
        );

        if (parentEvent.parentSpanId == null) {
          rootSpanId = parentEvent.spanId;
          break;
        }

        // Find the parent
        final parent = events.firstWhere(
          (e) => e.spanId == parentEvent.parentSpanId,
          orElse: () => parentEvent,
        );

        if (parent.spanId == parentEvent.spanId) {
          // No parent found, this is root
          rootSpanId = parentEvent.spanId;
          break;
        }

        currentId = parent.spanId;
      }

      if (rootSpanGroups.containsKey(rootSpanId)) {
        rootSpanGroups[rootSpanId]!.add(event);
      }
    }

    // Calculate width for each root span group
    const double pixelsPerMs = 0.05; // 0.05 pixels per millisecond
    const double segmentPadding = 50.0; // Padding between segments

    double totalWidth = 0.0;

    for (final group in rootSpanGroups.values) {
      if (group.isEmpty) continue;

      // Find the earliest start time in this group
      final groupStart = group
          .map((e) => e.startTime)
          .reduce((a, b) => a.isBefore(b) ? a : b);

      // Calculate the max visual width needed for this group
      // This accounts for both the time duration AND the indentation AND the text label
      double maxVisualRight = 0.0;

      for (final event in group) {
        final startOffset =
            event.startTime.difference(groupStart).inMilliseconds * pixelsPerMs;
        final durationWidth = event.duration.inMilliseconds * pixelsPerMs;
        final indentation = event.level * 20.0;

        // The visual right edge is start + duration + indentation
        // We ensure minimum width of 5.0 for visibility
        final barWidth = durationWidth < 5.0 ? 5.0 : durationWidth;
        final barRight = startOffset + indentation + barWidth;

        // Calculate text width
        final labelText = _getLabelText(event);
        final textPainter = TextPainter(
          text: TextSpan(
            text: labelText,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
          ),
          textDirection: TextDirection.ltr,
        );
        textPainter.layout();

        // Text is either inside the bar or outside to the right
        // If it fits inside, visual right is barRight
        // If it's outside, visual right is barRight + padding + textWidth
        const double textPadding = 5.0;
        double visualRight = barRight;

        if (textPainter.width + (textPadding * 2) > barWidth) {
          visualRight = barRight + textPadding + textPainter.width;
        }

        if (visualRight > maxVisualRight) {
          maxVisualRight = visualRight;
        }
      }

      // Ensure minimum segment width
      final groupWidth = maxVisualRight < 200.0 ? 200.0 : maxVisualRight;

      totalWidth += groupWidth + segmentPadding;
    }

    // Add extra buffer for text drawn outside bars
    // We subtract segmentPadding because the loop adds it after every group,
    // but we don't need that much space after the last group.
    return totalWidth - segmentPadding + 20.0;
  }

  Map<String, int> _calculateEventRows(List<ProcessedTraceEvent> events) {
    final rows = <String, int>{};
    final rowMaxPixel = <int, double>{}; // Row -> Max X pixel used
    final lastSiblingRow = <String, int>{}; // ParentId -> Last assigned row
    const double pixelsPerMs = 0.05;
    const double textPadding = 5.0;

    // Sort by start time
    final sortedEvents = List<ProcessedTraceEvent>.from(events)
      ..sort((a, b) => a.startTime.compareTo(b.startTime));

    if (sortedEvents.isEmpty) return rows;

    final traceStart = sortedEvents.first.startTime;

    for (final event in sortedEvents) {
      final startPixel =
          event.startTime.difference(traceStart).inMilliseconds * pixelsPerMs;
      final durationPixel = event.duration.inMilliseconds * pixelsPerMs;
      final barWidth = durationPixel < 5.0 ? 5.0 : durationPixel;

      // Calculate text width
      final labelText = _getLabelText(event);
      final textPainter = TextPainter(
        text: TextSpan(
          text: labelText,
          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
        ),
        textDirection: TextDirection.ltr,
      );
      textPainter.layout();

      // Calculate visual end pixel (including text)
      // Indentation is added later in painter, but we need to account for it here?
      // Wait, painter adds indentation: left = currentX + indentation + eventStart.
      // But currentX depends on group.
      // Assuming single group for simplicity of row collision (or that collision only matters within group).
      // Actually, if we just track "visual end" relative to trace start, it works globally.
      // Indentation: event.level * 20.0.
      final indentation = event.level * 20.0;
      double endPixel = startPixel + indentation + barWidth;

      if (textPainter.width + (textPadding * 2) > barWidth) {
        endPixel =
            startPixel +
            indentation +
            barWidth +
            textPadding +
            textPainter.width;
      }

      // Add a small buffer between items
      endPixel += 10.0;

      int row;
      if (event.eventType == 'AGENT') {
        // Agents are strictly aligned to hierarchy level
        row = event.level;
      } else {
        // Tools are sequenced and collision-avoidant
        final parentId = event.parentSpanId ?? 'root';

        // Start at least at level
        int minRow = event.level;

        // Enforce sequencing (staircase) under parent
        if (lastSiblingRow.containsKey(parentId)) {
          minRow = lastSiblingRow[parentId]! + 1;
        }

        // Find first available row >= minRow
        row = minRow;
        while (true) {
          final maxPixel = rowMaxPixel[row] ?? -1.0;
          if (maxPixel < startPixel + indentation) {
            // Found a free row
            break;
          }
          row++;
        }

        lastSiblingRow[parentId] = row;
      }

      rows[event.spanId] = row;

      // Update max pixel for this row
      final currentMax = rowMaxPixel[row] ?? -1.0;
      if (endPixel > currentMax) {
        rowMaxPixel[row] = endPixel;
      }
    }
    return rows;
  }

  double _calculateCanvasHeight(
    List<ProcessedTraceEvent> events,
    Map<String, int> rowIndices,
  ) {
    if (events.isEmpty) return 40.0;

    int maxRow = 0;
    for (final row in rowIndices.values) {
      if (row > maxRow) {
        maxRow = row;
      }
    }

    return (maxRow + 1) * 40.0;
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

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context);
    final rawTraceEvents = appState.traceEvents;
    print(
      'TraceWidget: isFullScreen=${widget.isFullScreen}, rawEvents=${rawTraceEvents.length}',
    );
    final processedEvents = _processTraceEvents(rawTraceEvents);
    print('TraceWidget: processedEvents=${processedEvents.length}');

    final groupedTraces = <String, List<ProcessedTraceEvent>>{};
    for (final event in processedEvents) {
      if (groupedTraces[event.traceId] == null) {
        groupedTraces[event.traceId] = [];
      }
      groupedTraces[event.traceId]!.add(event);
    }
    print('TraceWidget: groupedTraces=${groupedTraces.length}');

    // Check if events were added or updated
    final currentEventCount = rawTraceEvents.length;
    final eventsChanged = currentEventCount != _previousEventCount;
    _previousEventCount = currentEventCount;

    // Trigger scroll to end for all traces if events changed or if there are in-progress events
    for (final traceId in groupedTraces.keys) {
      final events = groupedTraces[traceId]!;
      final hasInProgress = events.any((e) => e.isInProgress);

      if (eventsChanged || hasInProgress) {
        _scrollToEnd(traceId);
      }
    }

    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Center(
                  child: Text(
                    'Agent Traces',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                      color: const Color(0xFF0D47A1), // Dark blue text
                    ),
                  ),
                ),
              ),
              IconButton(
                icon: Icon(
                  widget.isFullScreen
                      ? Icons.fullscreen_exit
                      : Icons.fullscreen,
                  color: const Color(0xFF0D47A1),
                ),
                tooltip: widget.isFullScreen
                    ? 'Exit full screen'
                    : 'Expand to full screen',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  if (widget.isFullScreen) {
                    Navigator.of(context).pop();
                  } else {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (context) => const FullScreenPanelView(
                          panelType: PanelType.trace,
                        ),
                      ),
                    );
                  }
                },
              ),
              IconButton(
                icon: const Icon(
                  Icons.delete_forever,
                  color: Color(0xFF0D47A1),
                ),
                tooltip: 'Clear all traces',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  setState(() {
                    // Clear all traces from app state and update the UI
                    appState.clearTraces();
                    _maxCanvasWidths.clear();
                  });
                },
              ),
            ],
          ),
        ),
        // Legend showing event type colors
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(vertical: 8.0, horizontal: 16.0),
          margin: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 4.0),
          decoration: BoxDecoration(
            color: Colors.grey[100],
            borderRadius: BorderRadius.circular(8.0),
          ),
          child: Wrap(
            spacing: 16.0,
            runSpacing: 8.0,
            alignment: WrapAlignment.center,
            children: [
              _buildLegendItem('Agent', const Color(0xFF2196F3)),
              _buildLegendItem('Tool', const Color(0xFF4CAF50)),
              _buildLegendItem('Model', const Color(0xFFFF9800)),
            ],
          ),
        ),
        Expanded(
          child: groupedTraces.isEmpty
              ? const Center(child: Text('No traces available'))
              : ListView.builder(
                  itemCount: groupedTraces.keys.length,
                  itemBuilder: (context, index) {
                    final traceId = groupedTraces.keys.elementAt(index);
                    final events = groupedTraces[traceId]!;
                    events.sort((a, b) => a.startTime.compareTo(b.startTime));
                    final traceStartTime = events.first.startTime;
                    final traceEndTime = events.fold<DateTime>(
                      events.first.startTime,
                      (max, e) => e.endTime.isAfter(max) ? e.endTime : max,
                    );
                    final traceDuration = traceEndTime.difference(
                      traceStartTime,
                    );

                    // Ensure controller exists
                    final controller = _scrollControllers.putIfAbsent(
                      traceId,
                      () => ScrollController(),
                    );

                    return Card(
                      margin: const EdgeInsets.symmetric(
                        horizontal: 8.0,
                        vertical: 4.0,
                      ),
                      elevation: 2,
                      child: ExpansionTile(
                        title: Row(
                          children: [
                            const Icon(
                              Icons.timeline,
                              color: Colors.blueGrey,
                              size: 20,
                            ),
                            const SizedBox(width: 8),
                            const Text(
                              'Trace ID: ',
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                color: Colors.blueGrey,
                                fontSize: 14,
                              ),
                            ),
                            Expanded(
                              child: Tooltip(
                                message: traceId,
                                child: Text(
                                  traceId,
                                  style: const TextStyle(
                                    fontFamily: 'monospace',
                                    fontWeight: FontWeight.w500,
                                    fontSize: 12,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ),
                          ],
                        ),
                        initiallyExpanded: true,
                        tilePadding: const EdgeInsets.symmetric(
                          horizontal: 16.0,
                        ),
                        childrenPadding: EdgeInsets.zero,
                        shape: const Border(),
                        collapsedShape: const Border(),
                        children: [
                          Padding(
                            padding: const EdgeInsets.all(16.0),
                            child: SizedBox(
                              height: _calculateCanvasHeight(
                                events,
                                _calculateEventRows(events),
                              ),
                              child: Scrollbar(
                                controller: controller,
                                thumbVisibility: true,
                                child: SingleChildScrollView(
                                  scrollDirection: Axis.horizontal,
                                  reverse: true,
                                  controller: controller,
                                  child: GestureDetector(
                                    onTapUp: (details) {
                                      final localPosition =
                                          details.localPosition;
                                      final positions =
                                          _traceEventPositions[traceId];
                                      if (positions != null) {
                                        for (final entry in positions.entries) {
                                          if (entry.value.contains(
                                            localPosition,
                                          )) {
                                            final event = events.firstWhere(
                                              (e) => e.spanId == entry.key,
                                            );
                                            showDialog(
                                              context: context,
                                              builder: (context) =>
                                                  TraceDetailsDialog(
                                                    event: event,
                                                  ),
                                            );
                                            break;
                                          }
                                        }
                                      }
                                    },
                                    child: CustomPaint(
                                      size: Size(
                                        _calculateConstrainedCanvasWidth(
                                          traceId,
                                          events,
                                        ),
                                        _calculateCanvasHeight(
                                          events,
                                          _calculateEventRows(events),
                                        ),
                                      ),
                                      painter: GanttChartPainter(
                                        events: events,
                                        traceStartTime: traceStartTime,
                                        traceDuration: traceDuration,
                                        rowIndices: _calculateEventRows(events),
                                        onLayout: (positions) {
                                          _traceEventPositions[traceId] =
                                              positions;
                                        },
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildLegendItem(String label, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 16,
          height: 16,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(3),
          ),
        ),
        const SizedBox(width: 6),
        Text(
          label,
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.bold,
            color: color,
          ),
        ),
      ],
    );
  }
}
