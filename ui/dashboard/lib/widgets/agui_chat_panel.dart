import 'package:flutter/material.dart';
import 'package:flutter_ai_toolkit/flutter_ai_toolkit.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'package:flutter_markdown/flutter_markdown.dart';
import '../appstate.dart';
import '../agui/socket_provider.dart';
import '../models/panel_type.dart';
import '../screens/full_screen_panel_view.dart';
import 'charts/time_series_chart_widget.dart';
import 'approval/task_approval_widget.dart';
import 'remote_task/remote_task_widget.dart';

class AGUIChatPanel extends StatefulWidget {
  final io.Socket socket;
  final bool isFullScreen;
  
  const AGUIChatPanel({
    super.key,
    required this.socket,
    this.isFullScreen = false,
  });

  @override
  State<AGUIChatPanel> createState() => _AGUIChatPanelState();
}

class _AGUIChatPanelState extends State<AGUIChatPanel> {
  late AGUISocketProvider _aguiProvider;
  
  @override
  void initState() {
    super.initState();
    _aguiProvider = AGUISocketProvider(socket: widget.socket);
  }
  
  @override
  void dispose() {
    _aguiProvider.dispose();
    super.dispose();
  }

  /// Custom response builder to handle chart tool calls and other custom widgets
  Widget _buildCustomResponse(BuildContext context, String response) {
    print(response);

    // Check if the response contains remote task request markers
    if (response.contains('📤') && response.contains('REMOTE_TASK:')) {
      // Extract the tool call ID from the response (supports encoded format: original_id::thread_id)
      final regex = RegExp(r'REMOTE_TASK:([a-zA-Z0-9_:-]+)');
      final match = regex.firstMatch(response);
      
      if (match != null) {
        final toolCallId = match.group(1)!;
        final remoteTaskData = _aguiProvider.getRemoteTaskData(toolCallId);
        
        if (remoteTaskData != null) {
          return Consumer<AGUISocketProvider>(
            builder: (context, provider, child) {
              final currentTaskData = provider.getRemoteTaskData(toolCallId);
              return RemoteTaskWidget(
                agentName: remoteTaskData.agentName,
                toolCallId: toolCallId,
                isCompleted: currentTaskData?.isCompleted ?? false,
                onComplete: () {
                  _aguiProvider.markRemoteTaskCompleted(toolCallId);
                },
              );
            },
          );
        }
      }
    }

    // Check if the response contains approval request markers
    if (response.contains('🔒') && response.contains('APPROVAL_REQUEST:')) {
      // Extract the tool call ID from the response (supports encoded format: original_id::thread_id)
      final regex = RegExp(r'APPROVAL_REQUEST:([a-zA-Z0-9_:-]+)');
      final match = regex.firstMatch(response);
      
      if (match != null) {
        final toolCallId = match.group(1)!;
        final approvalData = _aguiProvider.getApprovalData(toolCallId);
        
        if (approvalData != null) {
          return TaskApprovalWidget(
            title: approvalData.title,
            tasks: approvalData.tasks.map((task) => {
              'name': task.name,
              'description': task.description,
              'importance': task.importance,
            }).toList(),
            toolCallId: toolCallId,
            onResponse: (approved) {
              _aguiProvider.handleApprovalResponse(toolCallId, approved);
            },
          );
        }
      }
    }
    
    // Check if the response contains chart data markers
    if (response.contains('📊') && response.contains('**') && _aguiProvider.hasChartData) {
      // Get the latest chart data from the provider
      final chartData = _aguiProvider.getLatestChartData();
      if (chartData != null) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Display the text response
            if (response.isNotEmpty)
              Padding(
                padding: const EdgeInsets.all(8.0),
                child: Text(
                  response,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            // Display the chart
            TimeSeriesChartWidget(chartData: chartData),
          ],
        );
      }
    }
    
    // Default response - just return the text
    return MarkdownBody(data:response);
  }

  @override
  Widget build(BuildContext context) {
    final appState = Provider.of<Appstate>(context, listen: false);
    
    return Column(
      children: [
        // Header with quick questions and reset button
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
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Dropdown menu button on the left
              PopupMenuButton<String>(
                icon: const Icon(Icons.menu, color: Color(0xFF0D47A1)),
                tooltip: 'Quick questions',
                padding: EdgeInsets.zero,
                onSelected: (String value) {
                  // Send the selected question directly through AG-UI
                  _aguiProvider.generateStream(value);
                },
                itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
                  const PopupMenuItem<String>(
                    value: 'What network services can i deploy?',
                    child: Text('What network services can i deploy?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Give me more detail about the UERanSIM network service',
                    child: Text('Give me more detail about the UERanSIM network service'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What network services are already deployed?',
                    child: Text('What network services are already deployed?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What is the status of the control plane network service?',
                    child: Text('What is the status of the control plane network service?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'What locations are there?',
                    child: Text('What locations are there?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Propose a plan to deploy a 5G core',
                    child: Text('Propose a plan to deploy a 5G core'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a plan to deploy a new network location called cellsite1 with CIDR  10.0.40.0/24',
                    child: Text('Create a plan to deploy a new network location called cellsite1 with CIDR 10.0.40.0/24'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Can you add a radio simulator to cellsite1 and create a plan for a working 5G network',
                    child: Text('Can you add a radio simulator to cellsite1 and create a plan for a working 5G network'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a test called test1 between cellsite1-ueransim and DNN dnn',
                    child: Text('Create a test called test1 between cellsite1-ueransim and DNN dnn'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Create a plan to delete the ueransim network service, the ptp network connectivity service and the cellsite1 network location',
                    child: Text('Create a plan to delete the ueransim network service, the ptp network connectivity service and the cellsite1 network location'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Were there any error logs in the last 2 hours?',
                    child: Text('Were there any error logs in the last 2 hours?'),
                  ),
                  const PopupMenuItem<String>(
                    value: 'Delete all the network resources currently deployed except dataplane',
                    child: Text('Delete all the network resources currently deployed except dataplane'),
                  ),
                ],
              ),
              // Center title
              Expanded(
                child: Center(
                  child: Text(
                    'Network Agent Chat',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0D47A1), // Dark blue text
                    ),
                  ),
                ),
              ),
              // Expand/Collapse button
              IconButton(
                icon: Icon(
                  widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen, 
                  color: Color(0xFF0D47A1)
                ),
                tooltip: widget.isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  if (widget.isFullScreen) {
                    Navigator.of(context).pop();
                  } else {
                    Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (context) => FullScreenPanelView(
                          panelType: PanelType.chat,
                          socket: widget.socket,
                        ),
                      ),
                    );
                  }
                },
              ),
              // Reset button on the right
              IconButton(
                icon: const Icon(Icons.delete_forever, color: Color(0xFF0D47A1)),
                tooltip: 'Reset chat',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  // Reset the AG-UI conversation (creates new thread ID)
                  _aguiProvider.resetConversation();
                  // No need to call server-side reset - AG-UI manages thread IDs directly
                },
              ),
            ],
          ),
        ),
        // AG-UI Chat View
        Expanded(
          child: ChangeNotifierProvider.value(
            value: _aguiProvider,
            child: LlmChatView(
              provider: _aguiProvider,
              welcomeMessage: 'Welcome to the Network Agent! How can I help you manage your network today?',
              enableAttachments: false,
              enableVoiceNotes: false,
              responseBuilder: _buildCustomResponse,
            ),
          ),
        ),
      ],
    );
  }
}
