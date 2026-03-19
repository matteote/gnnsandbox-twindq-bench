import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../widgets/notifications/agent_request_notifications_widget.dart';
import '../widgets/notifications/incident_notifications_widget.dart';
import '../widgets/trace/trace_widget.dart';

class NotificationScreen extends StatefulWidget {
  const NotificationScreen({super.key});

  @override
  State<NotificationScreen> createState() => _NotificationScreenState();
}

class _NotificationScreenState extends State<NotificationScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;

  // Trace widget state
  bool _showTrace = false;
  double _tracePanelRatio = 0.3;
  static const double _minHorizontalSplitRatio = 0.2;
  static const double _maxHorizontalSplitRatio = 0.8;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this, initialIndex: 1);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white, // Ensure consistent white background
      appBar: AppBar(
        backgroundColor: const Color(0xFF0D47A1), // Dark blue
        foregroundColor: Colors.white,
        title: Center(
          child: Row(
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
              const Text(
                'Notifications',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ],
          ),
        ),
        actions: [
          // Trace toggle button
          IconButton(
            icon: Icon(
              Icons.analytics,
              color: _showTrace ? Colors.amber : Colors.white,
            ),
            onPressed: () {
              final appState = Provider.of<Appstate>(context, listen: false);
              setState(() {
                _showTrace = !_showTrace;
                appState.toggleTraces(_showTrace);
              });
            },
            tooltip: 'Toggle Trace',
          ),
          // Clear all notifications button (only for agent requests tab)
          Consumer<Appstate>(
            builder: (context, appState, child) {
              return IconButton(
                icon: const Icon(Icons.clear_all, color: Colors.white),
                onPressed: _tabController.index == 0
                    ? () {
                        appState.clearAllNotifications();
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            content: Text('All agent requests cleared'),
                          ),
                        );
                      }
                    : null,
                tooltip: _tabController.index == 0
                    ? 'Clear All Agent Requests'
                    : null,
              );
            },
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: Colors.white,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white70,
          tabs: [
            Consumer<Appstate>(
              builder: (context, appState, child) {
                final requestCount = appState.pushNotifications.length;
                return Tab(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.notifications_active),
                      const SizedBox(width: 8),
                      const Text('Agent Requests'),
                      if (requestCount > 0) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: Colors.red,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            requestCount.toString(),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                );
              },
            ),
            Consumer<Appstate>(
              builder: (context, appState, child) {
                final incidentCount = appState.incidents.length;
                return Tab(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.warning),
                      const SizedBox(width: 8),
                      const Text('Incidents'),
                      if (incidentCount > 0) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: Colors.orange,
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            incidentCount.toString(),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                );
              },
            ),
          ],
        ),
      ),
      body: Row(
        children: [
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: const [
                AgentRequestNotificationsWidget(),
                IncidentNotificationsWidget(),
              ],
            ),
          ),

          // Right panel - Trace (only shown if _showTrace is true)
          if (_showTrace) ...[
            // Horizontal resizable divider for trace panel
            MouseRegion(
              cursor: SystemMouseCursors.resizeColumn,
              child: GestureDetector(
                behavior: HitTestBehavior.translucent,
                onHorizontalDragUpdate: (details) {
                  setState(() {
                    _tracePanelRatio -=
                        details.delta.dx / MediaQuery.of(context).size.width;
                    _tracePanelRatio = _tracePanelRatio.clamp(
                      _minHorizontalSplitRatio,
                      _maxHorizontalSplitRatio,
                    );
                  });
                },
                child: Container(
                  width: 8,
                  color: const Color(0xFFE3F2FD), // Light blue background
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        width: 2,
                        height: 30,
                        color: const Color(
                          0xFF90CAF9,
                        ), // Slightly darker blue for the handle
                      ),
                    ],
                  ),
                ),
              ),
            ),

            Container(
              width: MediaQuery.of(context).size.width * _tracePanelRatio,
              color: Colors.white,
              child: const TraceWidget(),
            ),
          ],
        ],
      ),
    );
  }
}
