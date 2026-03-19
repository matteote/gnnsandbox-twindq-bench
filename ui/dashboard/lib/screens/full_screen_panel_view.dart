import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import '../models/panel_type.dart';
import '../models/log_entry.dart';
import '../widgets/agui_chat_panel.dart';
import '../widgets/log_widget.dart';
import '../widgets/performance/performance_graph_widget.dart';
import '../widgets/trace/trace_widget.dart';
import '../widgets/anomaly_panel.dart';
import '../appstate.dart';

class FullScreenPanelView extends StatelessWidget {
  final PanelType panelType;
  final io.Socket? socket;
  final List<LogEntry>? logs;
  final bool? isLoading;

  const FullScreenPanelView({
    super.key,
    required this.panelType,
    this.socket,
    this.logs,
    this.isLoading,
  });

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        return Scaffold(
          appBar: AppBar(
            backgroundColor: const Color(0xFF0D47A1), // Dark blue
            foregroundColor: Colors.white,
            centerTitle: true, // Center the title
            leading: IconButton(
              icon: const Icon(Icons.arrow_back),
              onPressed: () {
                Navigator.of(context).pop();
              },
              tooltip: 'Back to Dashboard',
            ),
            title: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Google logo
                ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.asset(
                    'assets/images/google.png',
                    width: 24,
                    height: 24,
                    fit: BoxFit.cover,
                  ),
                ),
                const SizedBox(width: 12),
                Text(
                  panelType.displayName,
                  style: const TextStyle(
                    fontWeight: FontWeight.bold, // Make the title bold
                  ),
                ),
                const SizedBox(width: 8),
                // Connection status indicator
                Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: appState.isConnected ? Colors.green : Colors.red,
                  ),
                ),
              ],
            ),
            actions: [
              // Connection status text
              Center(
                child: Padding(
                  padding: const EdgeInsets.only(right: 16.0),
                  child: Text(
                    appState.isConnected ? 'Connected' : 'Disconnected',
                    style: TextStyle(
                      fontSize: 12,
                      color: appState.isConnected ? Colors.green[100] : Colors.red[100],
                    ),
                  ),
                ),
              ),
            ],
          ),
          body: _buildPanelContent(),
        );
      },
    );
  }

  Widget _buildPanelContent() {
    switch (panelType) {
      case PanelType.chat:
        return AGUIChatPanel(socket: socket!, isFullScreen: true);
      case PanelType.logs:
        return Consumer<Appstate>(
          builder: (context, appState, child) {
            return LogWidget(
              logs: logs ?? appState.logs,
              socket: socket!,
              isLoading: isLoading ?? appState.isLoadingLogs,
              isFullScreen: true,
            );
          },
        );
      case PanelType.performance:
        return Consumer<Appstate>(
          builder: (context, appState, child) {
            return PerformanceGraphWidget(
              socket: socket!,
              isLoading: isLoading ?? appState.isLoadingMetrics,
              isFullScreen: true,
            );
          },
        );
      case PanelType.trace:
        return const TraceWidget(isFullScreen: true);
      case PanelType.anomaly:
        return const AnomalyPanel(isFullScreen: true);
    }
  }
}
