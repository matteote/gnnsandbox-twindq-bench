import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../models/trace_event.dart';
import 'collapsible_json_viewer.dart';

class TraceDetailsDialog extends StatelessWidget {
  final ProcessedTraceEvent event;

  const TraceDetailsDialog({super.key, required this.event});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(
        event.operationName,
        style: const TextStyle(fontWeight: FontWeight.bold),
      ),
      content: SizedBox(
        width: double.maxFinite,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              _buildSectionTitle('Metadata'),
              _buildMetadataGrid(),
              const SizedBox(height: 16),
              if (event.details != null && event.details!.isNotEmpty) ...[
                _buildSectionTitle('Details'),
                Container(
                  decoration: BoxDecoration(
                    border: Border.all(color: Colors.grey.shade300),
                    borderRadius: BorderRadius.circular(8.0),
                  ),
                  padding: const EdgeInsets.all(8.0),
                  child: CollapsibleJsonViewer(json: event.details),
                ),
              ],
            ],
          ),
        ),
      ),
      actions: <Widget>[
        TextButton(
          child: const Text('Close'),
          onPressed: () {
            Navigator.of(context).pop();
          },
        ),
      ],
    );
  }

  Widget _buildSectionTitle(String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8.0),
      child: Text(
        title,
        style: const TextStyle(
          fontSize: 18,
          fontWeight: FontWeight.w600,
          color: Colors.blueGrey,
        ),
      ),
    );
  }

  Widget _buildMetadataGrid() {
    final dateFormat = DateFormat('yyyy-MM-dd HH:mm:ss.SSS');
    return Card(
      elevation: 0,
      color: Colors.grey.shade50,
      shape: RoundedRectangleBorder(
        side: BorderSide(color: Colors.grey.shade300),
        borderRadius: BorderRadius.circular(8.0),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12.0),
        child: Column(
          children: [
            _buildMetadataRow('Event Type', event.eventType),
            _buildMetadataRow('Trace ID', event.traceId),
            _buildMetadataRow('Span ID', event.spanId),
            _buildMetadataRow('Parent Span ID', event.parentSpanId ?? 'N/A'),
            _buildMetadataRow('Start Time', dateFormat.format(event.startTime)),
            _buildMetadataRow('End Time', dateFormat.format(event.endTime)),
            _buildMetadataRow('Duration', '${event.duration.inMilliseconds}ms'),
            _buildMetadataRow('Status', event.isInProgress ? 'In Progress' : 'Completed'),
          ],
        ),
      ),
    );
  }

  Widget _buildMetadataRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 120,
            child: Text(
              '$label:',
              style: const TextStyle(
                fontWeight: FontWeight.w600,
                color: Colors.black87,
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontFamily: 'Monospace'),
            ),
          ),
        ],
      ),
    );
  }
}
