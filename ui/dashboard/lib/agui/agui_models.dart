// Simplified AG-UI models without code generation for faster implementation

enum EventType {
  textMessageStart,
  textMessageContent,
  textMessageEnd,
  toolCallStart,
  toolCallArgs,
  toolCallEnd,
  toolCallResult,
  stateSnapshot,
  stateDelta,
  runStarted,
  runFinished,
  runError,
  inputRequired,
  custom,
}

abstract class Event {
  final EventType type;
  
  Event({required this.type});
  
  factory Event.fromJson(Map<String, dynamic> json) {
    final typeString = json['type'] as String;
    
    switch (typeString) {
      case 'TEXT_MESSAGE_START':
        return TextMessageStartEvent.fromJson(json);
      case 'TEXT_MESSAGE_CONTENT':
        return TextMessageContentEvent.fromJson(json);
      case 'TEXT_MESSAGE_END':
        return TextMessageEndEvent.fromJson(json);
      case 'TOOL_CALL_START':
        return ToolCallStartEvent.fromJson(json);
      case 'TOOL_CALL_ARGS':
        return ToolCallArgsEvent.fromJson(json);
      case 'TOOL_CALL_END':
        return ToolCallEndEvent.fromJson(json);
      case 'TOOL_CALL_RESULT':
        return ToolCallResultEvent.fromJson(json);
      case 'RUN_ERROR':
        return RunErrorEvent.fromJson(json);
      case 'INPUT_REQUIRED':
        return InputRequiredEvent.fromJson(json);
      default:
        return UnknownEvent.fromJson(json);
    }
  }
}

class TextMessageStartEvent extends Event {
  final String messageId;
  final String role;
  
  TextMessageStartEvent({
    required this.messageId,
    this.role = 'assistant',
  }) : super(type: EventType.textMessageStart);
  
  factory TextMessageStartEvent.fromJson(Map<String, dynamic> json) {
    return TextMessageStartEvent(
      messageId: json['messageId'] ?? json['message_id'] ?? '',
      role: json['role'] ?? 'assistant',
    );
  }
}

class TextMessageContentEvent extends Event {
  final String messageId;
  final String delta;
  
  TextMessageContentEvent({
    required this.messageId,
    required this.delta,
  }) : super(type: EventType.textMessageContent);
  
  factory TextMessageContentEvent.fromJson(Map<String, dynamic> json) {
    return TextMessageContentEvent(
      messageId: json['messageId'] ?? json['message_id'] ?? '',
      delta: json['delta'] ?? '',
    );
  }
}

class TextMessageEndEvent extends Event {
  final String messageId;
  
  TextMessageEndEvent({
    required this.messageId,
  }) : super(type: EventType.textMessageEnd);
  
  factory TextMessageEndEvent.fromJson(Map<String, dynamic> json) {
    return TextMessageEndEvent(
      messageId: json['messageId'] ?? json['message_id'] ?? '',
    );
  }
}

class ToolCallStartEvent extends Event {
  final String toolCallId;
  final String toolCallName;
  final String? parentMessageId;
  
  ToolCallStartEvent({
    required this.toolCallId,
    required this.toolCallName,
    this.parentMessageId,
  }) : super(type: EventType.toolCallStart);
  
  factory ToolCallStartEvent.fromJson(Map<String, dynamic> json) {
    return ToolCallStartEvent(
      toolCallId: json['toolCallId'] ?? json['tool_call_id'] ?? '',
      toolCallName: json['toolCallName'] ?? json['tool_call_name'] ?? '',
      parentMessageId: json['parentMessageId'] ?? json['parent_message_id'],
    );
  }
  
  bool get isChartTool => toolCallName == 'displayTimeSeriesChart';
  bool get isApprovalTool => toolCallName == 'requestTaskApproval';
  bool get isTopologyTool => toolCallName == 'displayNetworkTopology';
  bool get isSendTaskTool => toolCallName == 'send_task';
}

class ToolCallArgsEvent extends Event {
  final String toolCallId;
  final String delta;
  
  ToolCallArgsEvent({
    required this.toolCallId,
    required this.delta,
  }) : super(type: EventType.toolCallArgs);
  
  factory ToolCallArgsEvent.fromJson(Map<String, dynamic> json) {
    return ToolCallArgsEvent(
      toolCallId: json['toolCallId'] ?? json['tool_call_id'] ?? '',
      delta: json['delta'] ?? '',
    );
  }
}

class ToolCallEndEvent extends Event {
  final String toolCallId;
  
  ToolCallEndEvent({
    required this.toolCallId,
  }) : super(type: EventType.toolCallEnd);
  
  factory ToolCallEndEvent.fromJson(Map<String, dynamic> json) {
    return ToolCallEndEvent(
      toolCallId: json['toolCallId'] ?? json['tool_call_id'] ?? '',
    );
  }
}

class ToolCallResultEvent extends Event {
  final String messageId;
  final String toolCallId;
  final String content;
  
  ToolCallResultEvent({
    required this.messageId,
    required this.toolCallId,
    required this.content,
  }) : super(type: EventType.toolCallResult);
  
  factory ToolCallResultEvent.fromJson(Map<String, dynamic> json) {
    return ToolCallResultEvent(
      messageId: json['messageId'] ?? json['message_id'] ?? '',
      toolCallId: json['toolCallId'] ?? json['tool_call_id'] ?? '',
      content: json['content'] ?? '',
    );
  }
}

class RunErrorEvent extends Event {
  final String message;
  final String? code;
  
  RunErrorEvent({
    required this.message,
    this.code,
  }) : super(type: EventType.runError);
  
  factory RunErrorEvent.fromJson(Map<String, dynamic> json) {
    return RunErrorEvent(
      message: json['message'] ?? 'Unknown error',
      code: json['code'],
    );
  }
}

class InputRequiredEvent extends Event {
  final String message;
  final String? messageId;
  
  InputRequiredEvent({
    required this.message,
    this.messageId,
  }) : super(type: EventType.inputRequired);
  
  factory InputRequiredEvent.fromJson(Map<String, dynamic> json) {
    return InputRequiredEvent(
      message: json['message'] ?? json['text'] ?? 'Input required from user',
      messageId: json['messageId'] ?? json['message_id'],
    );
  }
}

class UnknownEvent extends Event {
  final Map<String, dynamic> data;
  
  UnknownEvent({
    required this.data,
  }) : super(type: EventType.custom);
  
  factory UnknownEvent.fromJson(Map<String, dynamic> json) {
    return UnknownEvent(data: json);
  }
}

// User message for sending to backend
class UserMessage {
  final String id;
  final String content;
  
  UserMessage({
    required this.id,
    required this.content,
  });
  
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'content': content,
      'role': 'user',
    };
  }
}

// Run agent input for AG-UI protocol
class RunAgentInput {
  final String threadId;
  final String runId;
  final Map<String, dynamic> state;
  final List<UserMessage> messages;
  final List<dynamic> tools;
  final List<dynamic> context;
  
  RunAgentInput({
    required this.threadId,
    required this.runId,
    required this.state,
    required this.messages,
    required this.tools,
    required this.context,
  });
  
  Map<String, dynamic> toJson() {
    return {
      'thread_id': threadId,
      'run_id': runId,
      'state': state,
      'messages': messages.map((m) => m.toJson()).toList(),
      'tools': tools,
      'context': context,
    };
  }
}

// Approval data models
class ApprovalData {
  final String toolCallId;
  final String title;
  final List<ApprovalTask> tasks;
  
  ApprovalData({
    required this.toolCallId,
    required this.title,
    required this.tasks,
  });
  
  factory ApprovalData.fromJson(Map<String, dynamic> json) {
    return ApprovalData(
      toolCallId: json['toolCallId'] ?? '',
      title: json['title'] ?? 'Task Approval Required',
      tasks: (json['tasks'] as List<dynamic>? ?? [])
          .map((task) => ApprovalTask.fromJson(task as Map<String, dynamic>))
          .toList(),
    );
  }
  
  Map<String, dynamic> toJson() {
    return {
      'toolCallId': toolCallId,
      'title': title,
      'tasks': tasks.map((task) => task.toJson()).toList(),
    };
  }
}

class ApprovalTask {
  final String name;
  final String description;
  final String importance;
  
  ApprovalTask({
    required this.name,
    required this.description,
    this.importance = 'medium',
  });
  
  factory ApprovalTask.fromJson(Map<String, dynamic> json) {
    return ApprovalTask(
      name: json['name'] ?? 'Unknown task',
      description: json['description'] ?? '',
      importance: json['importance'] ?? 'medium',
    );
  }
  
  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'description': description,
      'importance': importance,
    };
  }
}

// Remote task data models
class RemoteTaskData {
  final String toolCallId;
  final String agentName;
  final String message;
  final DateTime startTime;
  bool isCompleted;
  
  RemoteTaskData({
    required this.toolCallId,
    required this.agentName,
    required this.message,
    DateTime? startTime,
    this.isCompleted = false,
  }) : startTime = startTime ?? DateTime.now();
  
  factory RemoteTaskData.fromJson(Map<String, dynamic> json) {
    return RemoteTaskData(
      toolCallId: json['toolCallId'] ?? '',
      agentName: json['agentName'] ?? 'Unknown Agent',
      message: json['message'] ?? '',
      startTime: json['startTime'] != null 
          ? DateTime.parse(json['startTime']) 
          : DateTime.now(),
      isCompleted: json['isCompleted'] ?? false,
    );
  }
  
  Map<String, dynamic> toJson() {
    return {
      'toolCallId': toolCallId,
      'agentName': agentName,
      'message': message,
      'startTime': startTime.toIso8601String(),
      'isCompleted': isCompleted,
    };
  }
}
