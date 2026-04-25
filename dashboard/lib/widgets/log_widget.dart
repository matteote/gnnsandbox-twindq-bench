import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/log_entry.dart';
import '../models/panel_type.dart';
import '../screens/full_screen_panel_view.dart';

class LogWidget extends StatelessWidget {
  final List<LogEntry> logs;
  final socket;
  final bool isLoading;
  final bool isFullScreen;

  const LogWidget({
    super.key,
    required this.socket,
    required this.logs,
    this.isLoading = false,
    this.isFullScreen = false,
  });

  void _resetLogs(BuildContext context) {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.resetLogs();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40, // Reduced height from 56 to 40
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0), // Reduced vertical padding
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Centered Title
              Center(
                child: Text(
                  'System Logs (${logs.length})',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0D47A1), // Dark blue text
                  ),
                ),
              ),
 
              // Expand/Collapse button (positioned on the right, before reset button)
              Positioned(
                right: 48, // Leave space for the reset button
                child: IconButton(
                  icon: Icon(
                    isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen, 
                    color: Color(0xFF0D47A1)
                  ),
                  tooltip: isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                  onPressed: () {
                    if (isFullScreen) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (context) => FullScreenPanelView(
                            panelType: PanelType.logs,
                            socket: socket,
                            logs: logs,
                            isLoading: isLoading,
                          ),
                        ),
                      );
                    }
                  },
                ),
              ),

              // Reset logs button (positioned on the right)
              Positioned(
                right: 0,
                child:
                  IconButton(
                    icon: const Icon(Icons.delete_forever, color: Color(0xFF0D47A1)),
                    tooltip: 'Delete logs',
                    onPressed: () {
                      _resetLogs(context);
                    },
                  ),
              ),
            ],
          ),
        ),
        Expanded(
          child: isLoading
              ? const Center(child: CircularProgressIndicator())
              : logs.isEmpty
                  ? Center(
                      child: Text(
                        'No logs available',
                        style: TextStyle(
                          color: Colors.grey[700],
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    )
                  : _buildLogTable(context),
        ),
      ],
    );
  }

  Widget _buildLogTable(BuildContext context) {
    return SingleChildScrollView(
      // Handles vertical scrolling for the entire table structure
      scrollDirection: Axis.vertical,
      child: LayoutBuilder( // Gets the available width from the parent
        builder: (context, constraints) {
          return SingleChildScrollView( // Handles horizontal scrolling if DataTable content is wider than constraints.maxWidth
            scrollDirection: Axis.horizontal,
            child: ConstrainedBox( // Ensures the DataTable is rendered in an area at least as wide as constraints.maxWidth
              constraints: BoxConstraints(minWidth: constraints.maxWidth),
              child: DataTable(
                headingRowColor: WidgetStateProperty.all(const Color(0xFFE3F2FD)),
                headingRowHeight: 36, // Added reduced heading row height
                dataRowMinHeight: 32, // Reduced from 48 to 32
                dataRowMaxHeight: 48, // Reduced from 64 to 48
                columns: const [
                  DataColumn(
                    label: Text(
                      'Timestamp',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Severity',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Source',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                  DataColumn(
                    label: Text(
                      'Message',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                ],
                rows: logs.map((log) {
                  return DataRow(
                    cells: [
                      DataCell(Text(_formatTimestamp(log.timestamp))),
                      DataCell(_buildSeverityIndicator(log.severity)),
                      DataCell(Text(log.source)),
                      DataCell(
                        Container(
                          alignment: Alignment.centerLeft, // Align text to the left within the cell
                          child: SelectableText(
                            log.message,
                          ),
                        ),
                      ),
                    ],
                  );
                }).toList(),
              ),
            ),
          );
        },
      ),
    );
  }

  String _formatTimestamp(String timestamp) {
    try {
      final dateTime = DateTime.parse(timestamp);
      return '${dateTime.hour.toString().padLeft(2, '0')}:${dateTime.minute.toString().padLeft(2, '0')}:${dateTime.second.toString().padLeft(2, '0')}';
    } catch (e) {
      return timestamp;
    }
  }

  Widget _buildSeverityIndicator(String severity) {
    Color color;
    switch (severity.toUpperCase()) {
      case 'CRITICAL':
        color = Colors.red;
        break;
      case 'ERROR':
        color = Colors.orange;
        break;
      case 'WARNING':
        color = Colors.yellow;
        break;
      case 'INFO':
        color = Colors.green;
        break;
      case 'DEBUG':
        color = Colors.blue;
        break;
      default:
        color = Colors.grey;
    }

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: color,
          ),
        ),
        const SizedBox(width: 8),
        Text(severity.toUpperCase()),
      ],
    );
  }

}
