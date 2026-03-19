import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_ai_toolkit/flutter_ai_toolkit.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'package:uuid/uuid.dart';

import 'agui_models.dart';
import '../widgets/charts/time_series_chart_widget.dart';

class AGUISocketProvider with ChangeNotifier implements LlmProvider {
  final io.Socket socket;
  final Uuid uuid = const Uuid();
  List<ChatMessage> _history = [];
  
  // Persistent thread ID for conversation continuity
  String _persistentThreadId;
  
  // Global event listener for the entire conversation
  bool _globalListenerSetup = false;
  
  // Current active message for streaming
  ChatMessage? _currentStreamingMessage;
  StreamController<String>? _currentController;
  
  // Tool call tracking
  final Map<String, String> _activeToolCalls = {}; // toolCallId -> toolName
  final Map<String, String> _toolCallArgs = {}; // toolCallId -> accumulated args
  
  // Chart data tracking
  Map<String, dynamic>? _latestChartData;
  
  // Approval data tracking
  final Map<String, ApprovalData> _pendingApprovals = {}; // toolCallId -> ApprovalData
  
  // Remote task tracking
  final Map<String, RemoteTaskData> _activeRemoteTasks = {}; // toolCallId -> RemoteTaskData

  AGUISocketProvider({required this.socket}) : _persistentThreadId = const Uuid().v4() {
    // Create one persistent thread ID for this conversation
    print('AGUISocketProvider: Created persistent thread ID: $_persistentThreadId');
    _setupGlobalEventListener();
  }
  
  void _setupGlobalEventListener() {
    if (_globalListenerSetup) return;
    
    print('AGUISocketProvider: Setting up global event listener');
    socket.on('agui_event', _handleGlobalAGUIEvent);
    _globalListenerSetup = true;
  }
  
  void _handleGlobalAGUIEvent(dynamic data) {
    try {
      if (data == null) return;
      
      print('AGUISocketProvider received event: ${data['type']}');
      print('AGUISocketProvider full event data: $data');
      print('AGUISocketProvider current thread_id: $_persistentThreadId');
      print('AGUISocketProvider event message_id: ${data['message_id']}');
      
      final event = Event.fromJson(data);
      
      if (event is TextMessageStartEvent) {
        // Backend started sending response - create new streaming message
        print('Starting new message response');
        _currentStreamingMessage = ChatMessage.llm();
        _history.add(_currentStreamingMessage!);
        notifyListeners();
      } else if (event is TextMessageContentEvent) {
        // The backend is sending a content delta
        print('Received content delta: ${event.delta}');
        if (_currentStreamingMessage != null) {
          _currentStreamingMessage!.append(event.delta);
          if (_currentController != null && !_currentController!.isClosed) {
            _currentController!.add(event.delta);
          }
          notifyListeners();
        }
      } else if (event is TextMessageEndEvent) {
        // The backend has finished sending the response
        print('Ending message response. Full response: ${_currentStreamingMessage?.text ?? "No message"}');
        if (_currentController != null && !_currentController!.isClosed) {
          _currentController!.close();
        }
        _currentStreamingMessage = null;
        _currentController = null;
      } else if (event is ToolCallStartEvent) {
        // Tool call started
        print('Tool call started: ${event.toolCallName} (${event.toolCallId})');
        _activeToolCalls[event.toolCallId] = event.toolCallName;
        _toolCallArgs[event.toolCallId] = '';
        
        // Handle send_task tool calls immediately to show the widget
        if (event.isSendTaskTool) {
          print('Detected send_task tool call: ${event.toolCallId}');
          // We'll create the remote task data when we get the args
        }
      } else if (event is ToolCallArgsEvent) {
        // Tool call arguments being streamed
        print('Tool call args delta: ${event.delta}');
        _toolCallArgs[event.toolCallId] = (_toolCallArgs[event.toolCallId] ?? '') + event.delta;
      } else if (event is ToolCallEndEvent) {
        // Tool call ended - process the complete arguments
        print('Tool call ended: ${event.toolCallId}');
        final toolName = _activeToolCalls[event.toolCallId];
        final argsJson = _toolCallArgs[event.toolCallId] ?? '';
        
        if (toolName != null && argsJson.isNotEmpty) {
          _handleToolCall(toolName, argsJson, event.toolCallId);
        }
        
        // Clean up
        _activeToolCalls.remove(event.toolCallId);
        _toolCallArgs.remove(event.toolCallId);
      } else if (event is ToolCallResultEvent) {
        // Tool call result received - check if it's a send_task completion
        print('Tool call result received: ${event.toolCallId}');
        
        // Check if this is a send_task tool call completion
        final remoteTask = _activeRemoteTasks[event.toolCallId];
        if (remoteTask != null) {
          print('Send_task tool call completed: ${event.toolCallId}');
          // Mark the remote task as completed to stop the progress indicator
          markRemoteTaskCompleted(event.toolCallId, event.content);
        }
      } else if (event is InputRequiredEvent) {
        // Handle input required events - agent needs user input
        print('Input required from user: ${event.message}');
        if (_currentStreamingMessage != null) {
          _currentStreamingMessage!.append('\n\n[Input Required] ${event.message}');
          notifyListeners();
        }
        if (_currentController != null && !_currentController!.isClosed) {
          _currentController!.close();
        }
        _currentStreamingMessage = null;
        _currentController = null;
      } else if (event is RunErrorEvent) {
        // Handle error events
        print('Received error event: ${event.message}');
        if (_currentController != null && !_currentController!.isClosed) {
          _currentController!.addError(Exception(event.message));
          _currentController!.close();
        }
        _currentStreamingMessage = null;
        _currentController = null;
      } else if (event is UnknownEvent) {
        // Handle unknown events - check if it's an input required event
        print('Received unknown event: ${event.data}');
        
        // Check if this is a RUN_FINISHED event - complete any active remote tasks
        if (event.data['type'] == 'RUN_FINISHED') {
          print('Detected RUN_FINISHED event - completing active remote tasks');
          final activeTaskIds = _activeRemoteTasks.keys.toList();
          for (final toolCallId in activeTaskIds) {
            print('Completing remote task: $toolCallId due to RUN_FINISHED');
            markRemoteTaskCompleted(toolCallId);
          }
        }
        
        // Check if this is a custom INPUT_REQUIRED event
        if (event.data['type'] == 'INPUT_REQUIRED' || 
            event.data['name'] == 'INPUT_REQUIRED' ||
            (event.data['value'] != null && event.data['value']['require_user_input'] == true)) {
          print('Detected INPUT_REQUIRED in unknown event');
          
          // Extract the message
          String message = 'Input required from user';
          if (event.data['value'] != null && event.data['value']['message'] != null) {
            message = event.data['value']['message'];
          } else if (event.data['message'] != null) {
            message = event.data['message'];
          }
          
          // Add the input required message to the LLM message
          if (_currentStreamingMessage != null) {
            _currentStreamingMessage!.append('\n\n[Input Required] $message');
            notifyListeners();
          }
          if (_currentController != null && !_currentController!.isClosed) {
            _currentController!.close();
          }
          _currentStreamingMessage = null;
          _currentController = null;
        }
      } else {
        print('Received unhandled event type: ${event.runtimeType}');
      }
    } catch (error) {
      print('Error processing AG-UI event: $error');
      if (_currentController != null && !_currentController!.isClosed) {
        _currentController!.addError(error);
        _currentController!.close();
      }
      _currentStreamingMessage = null;
      _currentController = null;
    }
  }

  @override
  List<ChatMessage> get history => _history;

  @override
  set history(Iterable<ChatMessage> newHistory) {
    _history = newHistory.toList();
    notifyListeners();
  }

  @override
  Stream<String> generateStream(
    String prompt, {
    Iterable<Attachment>? attachments,
  }) {
    final controller = StreamController<String>();
    // Use persistent thread ID for conversation continuity
    final threadId = _persistentThreadId;
    final runId = uuid.v4();

    // Set the current controller so the global event handler can use it
    _currentController = controller;

    // Add user message to history
    final userMessage = ChatMessage(
      origin: MessageOrigin.user,
      text: prompt,
      attachments: attachments?.toList() ?? [],
    );
    _history.add(userMessage);

    SchedulerBinding.instance.addPostFrameCallback((_) {
      notifyListeners();
    });

    // Create AG-UI message payload
    final aguiMessage = {
      'text': prompt,
      'thread_id': threadId,
      'run_id': runId,
      'state': {},
      'tools': [],
      'context': [],
    };

    // Send the AG-UI message to the backend
    try {
      print('AGUISocketProvider: Sending message with thread_id: $threadId, run_id: $runId');
      print('AGUISocketProvider: Message payload: $aguiMessage');
      socket.emit('agui_message', aguiMessage);
    } catch (error) {
      print('Error sending AG-UI message: $error');
      controller.addError(error);
      controller.close();
      _currentController = null;
    }

    return controller.stream;
  }

  @override
  Stream<String> sendMessageStream(
    String prompt, {
    Iterable<Attachment>? attachments,
  }) {
    return generateStream(prompt, attachments: attachments);
  }

  /// Reset the conversation and create a new thread ID
  void resetConversation() {
    final oldThreadId = _persistentThreadId;
    _persistentThreadId = uuid.v4();
    print('AGUISocketProvider: Reset conversation - old thread_id: $oldThreadId, new thread_id: $_persistentThreadId');
    
    // Clear history
    _history.clear();
    
    // Reset streaming state
    _currentStreamingMessage = null;
    if (_currentController != null && !_currentController!.isClosed) {
      _currentController!.close();
    }
    _currentController = null;
    
    // Clear all tracking data
    _activeToolCalls.clear();
    _toolCallArgs.clear();
    _latestChartData = null;
    _pendingApprovals.clear();
    _activeRemoteTasks.clear();
    
    print('AGUISocketProvider: Cleared all tracking data (tool calls, approvals, remote tasks, chart data)');
    
    notifyListeners();
  }

  /// Get the current thread ID for debugging
  String get currentThreadId => _persistentThreadId;
  
  /// Check if chart data is available
  bool get hasChartData => _latestChartData != null;
  
  /// Get the latest chart data
  Map<String, dynamic>? getLatestChartData() => _latestChartData;
  
  /// Handle tool calls and create appropriate widgets
  void _handleToolCall(String toolName, String argsJson, String toolCallId) {
    try {
      print('Handling tool call: $toolName with args: $argsJson');
      
      final args = jsonDecode(argsJson) as Map<String, dynamic>;
      
      switch (toolName) {
        case 'displayTimeSeriesChart':
          _handleChartToolCall(args, toolCallId);
          break;
        case 'requestTaskApproval':
          _handleApprovalToolCall(args, toolCallId);
          break;
        case 'displayNetworkTopology':
          _handleTopologyToolCall(args, toolCallId);
          break;
        case 'send_task':
          _handleSendTaskToolCall(args, toolCallId);
          break;
        default:
          print('Unknown tool call: $toolName');
          _sendToolResult(toolCallId, 'Unknown tool: $toolName');
      }
    } catch (e) {
      print('Error handling tool call: $e');
      _sendToolResult(toolCallId, 'Error: $e');
    }
  }
  
  /// Handle chart tool calls
  void _handleChartToolCall(Map<String, dynamic> args, String toolCallId) {
    try {
      // Store the chart data for the response builder
      _latestChartData = Map<String, dynamic>.from(args);
      
      // Create a chart message
      final chartMessage = ChatMessage.llm();
      chartMessage.append('📊 **${args['title'] ?? 'Chart'}**\n\n');
      
      _history.add(chartMessage);
      notifyListeners();
      
      // Send success result back to agent
      _sendToolResult(toolCallId, 'Chart displayed successfully');
    } catch (e) {
      print('Error creating chart: $e');
      _sendToolResult(toolCallId, 'Error creating chart: $e');
    }
  }
  
  /// Handle approval tool calls
  void _handleApprovalToolCall(Map<String, dynamic> args, String toolCallId) {
    try {
      print('Creating approval request for tool call: $toolCallId');
      
      // Create approval data from arguments
      final approvalData = ApprovalData(
        toolCallId: toolCallId,
        title: args['title'] ?? 'Task Approval Required',
        tasks: (args['tasks'] as List<dynamic>? ?? [])
            .map((task) => ApprovalTask.fromJson(task as Map<String, dynamic>))
            .toList(),
      );
      
      // Store pending approval
      _pendingApprovals[toolCallId] = approvalData;
      
      // Create a special approval message that will be rendered as a widget
      final approvalMessage = ChatMessage.llm();
      approvalMessage.append('🔒 **APPROVAL_REQUEST:$toolCallId**');
      
      _history.add(approvalMessage);
      notifyListeners();
      
      print('Approval request created and stored for tool call: $toolCallId');
    } catch (e) {
      print('Error creating approval: $e');
      _sendToolResult(toolCallId, jsonEncode({
        'error': 'Failed to create approval request: $e'
      }));
    }
  }
  
  /// Handle topology tool calls
  void _handleTopologyToolCall(Map<String, dynamic> args, String toolCallId) {
    try {
      // For now, create a simple topology message
      // TODO: Implement proper topology widget
      final topologyMessage = ChatMessage.llm();
      final title = args['title'] ?? 'Network Topology';
      final elements = args['elements'] as List<dynamic>? ?? [];
      
      topologyMessage.append('🌐 **$title**\n\n');
      
      final nodes = elements.where((e) => e['group'] == 'nodes').length;
      final edges = elements.where((e) => e['group'] == 'edges').length;
      
      topologyMessage.append('Network topology with $nodes nodes and $edges connections.\n');
      
      _history.add(topologyMessage);
      notifyListeners();
      
      _sendToolResult(toolCallId, 'Topology displayed successfully');
    } catch (e) {
      print('Error creating topology: $e');
      _sendToolResult(toolCallId, 'Error creating topology: $e');
    }
  }
  
  /// Handle send_task tool calls
  void _handleSendTaskToolCall(Map<String, dynamic> args, String toolCallId) {
    try {
      print('Creating remote task request for tool call: $toolCallId');
      
      // Extract agent name and message from arguments
      final agentName = args['agent_name'] ?? 'Unknown Agent';
      final message = args['message'] ?? 'No message provided';
      
      // Create remote task data
      final remoteTaskData = RemoteTaskData(
        toolCallId: toolCallId,
        agentName: agentName,
        message: message,
      );
      
      // Store active remote task
      _activeRemoteTasks[toolCallId] = remoteTaskData;
      
      // Create a special remote task message that will be rendered as a widget
      final remoteTaskMessage = ChatMessage.llm();
      remoteTaskMessage.append('📤 **REMOTE_TASK:$toolCallId**');
      
      _history.add(remoteTaskMessage);
      notifyListeners();
      
      print('Remote task request created and stored for tool call: $toolCallId');
      
      // DON'T send tool result immediately - this keeps the chat view in "waiting for response" state
      // The backend will handle the actual task and send the result when the remote task completes
      // This creates the desired waiting behavior in the chat UI
    } catch (e) {
      print('Error creating remote task: $e');
      _sendToolResult(toolCallId, jsonEncode({
        'error': 'Failed to create remote task request: $e'
      }));
    }
  }
  
  /// Send tool result back to the agent
  void _sendToolResult(String toolCallId, String result) {
    try {
      final toolResult = {
        'tool_call_id': toolCallId,
        'content': result,
      };
      
      print('Sending tool result: $toolResult');
      socket.emit('agui_tool_result', toolResult);
    } catch (e) {
      print('Error sending tool result: $e');
    }
  }
  
  /// Get approval data for a specific tool call ID
  ApprovalData? getApprovalData(String toolCallId) {
    return _pendingApprovals[toolCallId];
  }
  
  /// Get remote task data for a specific tool call ID
  RemoteTaskData? getRemoteTaskData(String toolCallId) {
    return _activeRemoteTasks[toolCallId];
  }
  
  /// Mark a remote task as completed
  void markRemoteTaskCompleted(String toolCallId, [String? content]) {
    final remoteTask = _activeRemoteTasks[toolCallId];
    if (remoteTask != null) {
      remoteTask.isCompleted = true;
      notifyListeners();
      
      // Update the chat message to show completion
      _updateRemoteTaskMessage(toolCallId, true, content);
      
      // Remove from active tasks after a delay to show completion state
      Future.delayed(const Duration(seconds: 2), () {
        _activeRemoteTasks.remove(toolCallId);
        notifyListeners();
      });
    }
  }
  
  /// Handle approval response from user
  void handleApprovalResponse(String toolCallId, bool approved) {
    try {
      print('Handling approval response for $toolCallId: $approved');
      
      // Get the approval data
      final approvalData = _pendingApprovals[toolCallId];
      if (approvalData == null) {
        print('No approval data found for tool call: $toolCallId');
        return;
      }
      
      // Create response message
      final response = {
        'approved': approved,
        'timestamp': DateTime.now().toIso8601String(),
        'tasks': approvalData.tasks.map((task) => task.toJson()).toList(),
      };
      
      // Send the approval result
      _sendToolResult(toolCallId, jsonEncode(response));
      
      // Remove from pending approvals
      _pendingApprovals.remove(toolCallId);
      
      // Update the chat message to show the result
      _updateApprovalMessage(toolCallId, approved);
      
      print('Approval response sent for $toolCallId: $approved');
    } catch (e) {
      print('Error handling approval response: $e');
    }
  }
  
  /// Update the approval message in chat history to show the result
  void _updateApprovalMessage(String toolCallId, bool approved) {
    try {
      // Find the approval message in history
      for (int i = _history.length - 1; i >= 0; i--) {
        final message = _history[i];
        if (message.text?.contains('APPROVAL_REQUEST:$toolCallId') == true) {
          // Replace the approval request with the result
          final resultText = approved 
              ? '✅ **Tasks Approved**\n\nThe requested tasks have been approved and will proceed.'
              : '❌ **Tasks Denied**\n\nThe requested tasks have been denied and will not proceed.';
          
          // Create a new message with the result
          final resultMessage = ChatMessage.llm();
          resultMessage.append(resultText);
          
          // Replace the approval request message
          _history[i] = resultMessage;
          notifyListeners();
          break;
        }
      }
    } catch (e) {
      print('Error updating approval message: $e');
    }
  }
  
  /// Update the remote task message in chat history to show the result
  void _updateRemoteTaskMessage(String toolCallId, bool completed, [String? content]) {
    try {
      // Find the remote task message in history
      for (int i = _history.length - 1; i >= 0; i--) {
        final message = _history[i];
        if (message.text?.contains('REMOTE_TASK:$toolCallId') == true) {
          // Replace the remote task with the result
          final remoteTask = _activeRemoteTasks[toolCallId];
          
          String resultText;
          if (completed) {
            resultText = '✅ **${remoteTask?.agentName ?? 'Agent'}**';
            
            if (content != null && content.isNotEmpty) {
               try {
                   final decoded = jsonDecode(content);
                   String? textContent;
                   if (decoded is Map) {
                       textContent = decoded['text'] ?? decoded['result'] ?? decoded['message'];
                   }
                   
                   if (textContent != null && textContent.isNotEmpty) {
                       resultText += '\n\n$textContent';
                   } else if (decoded is! Map) {
                       // If not a map but decoded (e.g. list or primitive), just append
                       resultText += '\n\n$content';
                   }
               } catch (e) {
                   // If not JSON, append if it looks like meaningful text
                   if (content.trim().isNotEmpty) {
                        resultText += '\n\n$content';
                   }
               }
            }
          } else {
            resultText = '❌ **Remote Task Failed** to ${remoteTask?.agentName ?? 'agent'}.';
          }
          
          // Create a new message with the result
          final resultMessage = ChatMessage.llm();
          resultMessage.append(resultText);
          
          // Replace the remote task message
          _history[i] = resultMessage;
          notifyListeners();
          break;
        }
      }
    } catch (e) {
      print('Error updating remote task message: $e');
    }
  }
}
