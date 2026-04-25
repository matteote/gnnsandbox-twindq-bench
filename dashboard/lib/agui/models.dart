import 'package:json_annotation/json_annotation.dart';

part 'models.g.dart';

// Core Types

@JsonSerializable()
class RunAgentInput {
  final String threadId;
  final String runId;
  final dynamic state;
  final List<Message> messages;
  final List<Tool> tools;
  final List<Context> context;
  final dynamic forwardedProps;

  RunAgentInput({
    required this.threadId,
    required this.runId,
    this.state,
    required this.messages,
    required this.tools,
    required this.context,
    this.forwardedProps,
  });

  factory RunAgentInput.fromJson(Map<String, dynamic> json) =>
      _$RunAgentInputFromJson(json);
  Map<String, dynamic> toJson() => _$RunAgentInputToJson(this);
}

// Message Types

enum Role {
  @JsonValue('developer')
  developer,
  @JsonValue('system')
  system,
  @JsonValue('assistant')
  assistant,
  @JsonValue('user')
  user,
  @JsonValue('tool')
  tool,
}

@JsonSerializable()
class Message {
  final String id;
  final Role role;

  Message({required this.id, required this.role});

  factory Message.fromJson(Map<String, dynamic> json) {
    switch (json['role']) {
      case 'developer':
        return DeveloperMessage.fromJson(json);
      case 'system':
        return SystemMessage.fromJson(json);
      case 'assistant':
        return AssistantMessage.fromJson(json);
      case 'user':
        return UserMessage.fromJson(json);
      case 'tool':
        return ToolMessage.fromJson(json);
      default:
        throw ArgumentError('Invalid role: ${json['role']}');
    }
  }

  Map<String, dynamic> toJson() {
    if (this is DeveloperMessage) {
      return (this as DeveloperMessage).toJson();
    } else if (this is SystemMessage) {
      return (this as SystemMessage).toJson();
    } else if (this is AssistantMessage) {
      return (this as AssistantMessage).toJson();
    } else if (this is UserMessage) {
      return (this as UserMessage).toJson();
    } else if (this is ToolMessage) {
      return (this as ToolMessage).toJson();
    }
    return _$MessageToJson(this);
  }
}

@JsonSerializable()
class BaseMessage extends Message {
  final String? name;

  BaseMessage({required String id, required Role role, this.name})
    : super(id: id, role: role);

  factory BaseMessage.fromJson(Map<String, dynamic> json) =>
      _$BaseMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$BaseMessageToJson(this);
}

@JsonSerializable()
class DeveloperMessage extends BaseMessage {
  final String content;

  DeveloperMessage({required String id, this.content = '', String? name})
    : super(id: id, role: Role.developer, name: name);

  factory DeveloperMessage.fromJson(Map<String, dynamic> json) =>
      _$DeveloperMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$DeveloperMessageToJson(this);
}

@JsonSerializable()
class SystemMessage extends BaseMessage {
  final String content;

  SystemMessage({required String id, this.content = '', String? name})
    : super(id: id, role: Role.system, name: name);

  factory SystemMessage.fromJson(Map<String, dynamic> json) =>
      _$SystemMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$SystemMessageToJson(this);
}

@JsonSerializable()
class AssistantMessage extends BaseMessage {
  final String? content;
  final List<ToolCall>? toolCalls;

  AssistantMessage({
    required String id,
    this.content,
    this.toolCalls,
    String? name,
  }) : super(id: id, role: Role.assistant, name: name);

  factory AssistantMessage.fromJson(Map<String, dynamic> json) =>
      _$AssistantMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$AssistantMessageToJson(this);
}

@JsonSerializable()
class UserMessage extends BaseMessage {
  final String content;

  UserMessage({required String id, this.content = '', String? name})
    : super(id: id, role: Role.user, name: name);

  factory UserMessage.fromJson(Map<String, dynamic> json) =>
      _$UserMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$UserMessageToJson(this);
}

@JsonSerializable()
class ToolMessage extends BaseMessage {
  final String content;
  final String toolCallId;
  final String? error;

  ToolMessage({
    required String id,
    this.content = '',
    required this.toolCallId,
    this.error,
  }) : super(id: id, role: Role.tool);

  factory ToolMessage.fromJson(Map<String, dynamic> json) =>
      _$ToolMessageFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$ToolMessageToJson(this);
}

// Tool Types

@JsonSerializable()
class ToolCall {
  final String id;
  final String type;
  final FunctionCall function;

  ToolCall({required this.id, this.type = 'function', required this.function});

  factory ToolCall.fromJson(Map<String, dynamic> json) =>
      _$ToolCallFromJson(json);
  Map<String, dynamic> toJson() => _$ToolCallToJson(this);
}

@JsonSerializable()
class FunctionCall {
  final String name;
  final String arguments;

  FunctionCall({required this.name, required this.arguments});

  factory FunctionCall.fromJson(Map<String, dynamic> json) =>
      _$FunctionCallFromJson(json);
  Map<String, dynamic> toJson() => _$FunctionCallToJson(this);
}

@JsonSerializable()
class Context {
  final String description;
  final String value;

  Context({required this.description, required this.value});

  factory Context.fromJson(Map<String, dynamic> json) =>
      _$ContextFromJson(json);
  Map<String, dynamic> toJson() => _$ContextToJson(this);
}

@JsonSerializable()
class Tool {
  final String name;
  final String description;
  final dynamic parameters;

  Tool({required this.name, required this.description, this.parameters});

  factory Tool.fromJson(Map<String, dynamic> json) => _$ToolFromJson(json);
  Map<String, dynamic> toJson() => _$ToolToJson(this);
}

// Event Types

enum EventType {
  @JsonValue('TEXT_MESSAGE_START')
  textMessageStart,
  @JsonValue('TEXT_MESSAGE_CONTENT')
  textMessageContent,
  @JsonValue('TEXT_MESSAGE_END')
  textMessageEnd,
  @JsonValue('TOOL_CALL_START')
  toolCallStart,
  @JsonValue('TOOL_CALL_ARGS')
  toolCallArgs,
  @JsonValue('TOOL_CALL_END')
  toolCallEnd,
  @JsonValue('TOOL_CALL_RESULT')
  toolCallResult,
  @JsonValue('STATE_SNAPSHOT')
  stateSnapshot,
  @JsonValue('STATE_DELTA')
  stateDelta,
  @JsonValue('MESSAGES_SNAPSHOT')
  messagesSnapshot,
  @JsonValue('RAW')
  raw,
  @JsonValue('CUSTOM')
  custom,
  @JsonValue('RUN_STARTED')
  runStarted,
  @JsonValue('RUN_FINISHED')
  runFinished,
  @JsonValue('RUN_ERROR')
  runError,
  @JsonValue('STEP_STARTED')
  stepStarted,
  @JsonValue('STEP_FINISHED')
  stepFinished,
}

@JsonSerializable()
class Event {
  final EventType type;

  Event({required this.type});

  factory Event.fromJson(Map<String, dynamic> json) {
    switch (json['type']) {
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
      case 'STATE_SNAPSHOT':
        return StateSnapshotEvent.fromJson(json);
      case 'STATE_DELTA':
        return StateDeltaEvent.fromJson(json);
      case 'MESSAGES_SNAPSHOT':
        return MessagesSnapshotEvent.fromJson(json);
      case 'RAW':
        return RawEvent.fromJson(json);
      case 'CUSTOM':
        return CustomEvent.fromJson(json);
      case 'RUN_STARTED':
        return RunStartedEvent.fromJson(json);
      case 'RUN_FINISHED':
        return RunFinishedEvent.fromJson(json);
      case 'RUN_ERROR':
        return RunErrorEvent.fromJson(json);
      case 'STEP_STARTED':
        return StepStartedEvent.fromJson(json);
      case 'STEP_FINISHED':
        return StepFinishedEvent.fromJson(json);
      default:
        throw ArgumentError('Invalid event type: ${json['type']}');
    }
  }

  Map<String, dynamic> toJson() {
    if (this is TextMessageStartEvent) {
      return (this as TextMessageStartEvent).toJson();
    } else if (this is TextMessageContentEvent) {
      return (this as TextMessageContentEvent).toJson();
    } else if (this is TextMessageEndEvent) {
      return (this as TextMessageEndEvent).toJson();
    } else if (this is ToolCallStartEvent) {
      return (this as ToolCallStartEvent).toJson();
    } else if (this is ToolCallArgsEvent) {
      return (this as ToolCallArgsEvent).toJson();
    } else if (this is ToolCallEndEvent) {
      return (this as ToolCallEndEvent).toJson();
    } else if (this is ToolCallResultEvent) {
      return (this as ToolCallResultEvent).toJson();
    } else if (this is StateSnapshotEvent) {
      return (this as StateSnapshotEvent).toJson();
    } else if (this is StateDeltaEvent) {
      return (this as StateDeltaEvent).toJson();
    } else if (this is MessagesSnapshotEvent) {
      return (this as MessagesSnapshotEvent).toJson();
    } else if (this is RawEvent) {
      return (this as RawEvent).toJson();
    } else if (this is CustomEvent) {
      return (this as CustomEvent).toJson();
    } else if (this is RunStartedEvent) {
      return (this as RunStartedEvent).toJson();
    } else if (this is RunFinishedEvent) {
      return (this as RunFinishedEvent).toJson();
    } else if (this is RunErrorEvent) {
      return (this as RunErrorEvent).toJson();
    } else if (this is StepStartedEvent) {
      return (this as StepStartedEvent).toJson();
    } else if (this is StepFinishedEvent) {
      return (this as StepFinishedEvent).toJson();
    }
    return _$EventToJson(this);
  }
}

@JsonSerializable()
class BaseEvent extends Event {
  final int? timestamp;
  final dynamic rawEvent;

  BaseEvent({required EventType type, this.timestamp, this.rawEvent})
    : super(type: type);

  factory BaseEvent.fromJson(Map<String, dynamic> json) =>
      _$BaseEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$BaseEventToJson(this);
}

// Lifecycle Events

@JsonSerializable()
class RunStartedEvent extends BaseEvent {
  final String threadId;
  final String runId;

  RunStartedEvent({required this.threadId, required this.runId})
    : super(type: EventType.runStarted);

  factory RunStartedEvent.fromJson(Map<String, dynamic> json) =>
      _$RunStartedEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$RunStartedEventToJson(this);
}

@JsonSerializable()
class RunFinishedEvent extends BaseEvent {
  final String threadId;
  final String runId;
  final dynamic result;

  RunFinishedEvent({required this.threadId, required this.runId, this.result})
    : super(type: EventType.runFinished);

  factory RunFinishedEvent.fromJson(Map<String, dynamic> json) =>
      _$RunFinishedEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$RunFinishedEventToJson(this);
}

@JsonSerializable()
class RunErrorEvent extends BaseEvent {
  final String message;
  final String? code;

  RunErrorEvent({required this.message, this.code})
    : super(type: EventType.runError);

  factory RunErrorEvent.fromJson(Map<String, dynamic> json) =>
      _$RunErrorEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$RunErrorEventToJson(this);
}

@JsonSerializable()
class StepStartedEvent extends BaseEvent {
  final String stepName;

  StepStartedEvent({required this.stepName})
    : super(type: EventType.stepStarted);

  factory StepStartedEvent.fromJson(Map<String, dynamic> json) =>
      _$StepStartedEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$StepStartedEventToJson(this);
}

@JsonSerializable()
class StepFinishedEvent extends BaseEvent {
  final String stepName;

  StepFinishedEvent({required this.stepName})
    : super(type: EventType.stepFinished);

  factory StepFinishedEvent.fromJson(Map<String, dynamic> json) =>
      _$StepFinishedEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$StepFinishedEventToJson(this);
}

// Text Message Events

@JsonSerializable()
class TextMessageStartEvent extends BaseEvent {
  final String messageId;
  final Role role;

  TextMessageStartEvent({required this.messageId, this.role = Role.assistant})
    : super(type: EventType.textMessageStart);

  factory TextMessageStartEvent.fromJson(Map<String, dynamic> json) =>
      _$TextMessageStartEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$TextMessageStartEventToJson(this);
}

@JsonSerializable()
class TextMessageContentEvent extends BaseEvent {
  final String messageId;
  final String delta;

  TextMessageContentEvent({required this.messageId, required this.delta})
    : super(type: EventType.textMessageContent);

  factory TextMessageContentEvent.fromJson(Map<String, dynamic> json) =>
      _$TextMessageContentEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$TextMessageContentEventToJson(this);
}

@JsonSerializable()
class TextMessageEndEvent extends BaseEvent {
  final String messageId;

  TextMessageEndEvent({required this.messageId})
    : super(type: EventType.textMessageEnd);

  factory TextMessageEndEvent.fromJson(Map<String, dynamic> json) =>
      _$TextMessageEndEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$TextMessageEndEventToJson(this);
}

// Tool Call Events

@JsonSerializable()
class ToolCallStartEvent extends BaseEvent {
  final String toolCallId;
  final String toolCallName;
  final String? parentMessageId;

  ToolCallStartEvent({
    required this.toolCallId,
    required this.toolCallName,
    this.parentMessageId,
  }) : super(type: EventType.toolCallStart);

  factory ToolCallStartEvent.fromJson(Map<String, dynamic> json) =>
      _$ToolCallStartEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$ToolCallStartEventToJson(this);
}

@JsonSerializable()
class ToolCallArgsEvent extends BaseEvent {
  final String toolCallId;
  final String delta;

  ToolCallArgsEvent({required this.toolCallId, required this.delta})
    : super(type: EventType.toolCallArgs);

  factory ToolCallArgsEvent.fromJson(Map<String, dynamic> json) =>
      _$ToolCallArgsEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$ToolCallArgsEventToJson(this);
}

@JsonSerializable()
class ToolCallEndEvent extends BaseEvent {
  final String toolCallId;

  ToolCallEndEvent({required this.toolCallId})
    : super(type: EventType.toolCallEnd);

  factory ToolCallEndEvent.fromJson(Map<String, dynamic> json) =>
      _$ToolCallEndEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$ToolCallEndEventToJson(this);
}

@JsonSerializable()
class ToolCallResultEvent extends BaseEvent {
  final String messageId;
  final String toolCallId;
  final String content;
  final Role? role;

  ToolCallResultEvent({
    required this.messageId,
    required this.toolCallId,
    required this.content,
    this.role = Role.tool,
  }) : super(type: EventType.toolCallResult);

  factory ToolCallResultEvent.fromJson(Map<String, dynamic> json) =>
      _$ToolCallResultEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$ToolCallResultEventToJson(this);
}

// State Management Events

@JsonSerializable()
class StateSnapshotEvent extends BaseEvent {
  final dynamic snapshot;

  StateSnapshotEvent({required this.snapshot})
    : super(type: EventType.stateSnapshot);

  factory StateSnapshotEvent.fromJson(Map<String, dynamic> json) =>
      _$StateSnapshotEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$StateSnapshotEventToJson(this);
}

@JsonSerializable()
class StateDeltaEvent extends BaseEvent {
  final List<dynamic> delta;

  StateDeltaEvent({required this.delta}) : super(type: EventType.stateDelta);

  factory StateDeltaEvent.fromJson(Map<String, dynamic> json) =>
      _$StateDeltaEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$StateDeltaEventToJson(this);
}

@JsonSerializable()
class MessagesSnapshotEvent extends BaseEvent {
  final List<Message> messages;

  MessagesSnapshotEvent({required this.messages})
    : super(type: EventType.messagesSnapshot);

  factory MessagesSnapshotEvent.fromJson(Map<String, dynamic> json) =>
      _$MessagesSnapshotEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$MessagesSnapshotEventToJson(this);
}

// Special Events

@JsonSerializable()
class RawEvent extends BaseEvent {
  final dynamic event;
  final String? source;

  RawEvent({required this.event, this.source}) : super(type: EventType.raw);

  factory RawEvent.fromJson(Map<String, dynamic> json) =>
      _$RawEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$RawEventToJson(this);
}

@JsonSerializable()
class CustomEvent extends BaseEvent {
  final String name;
  final dynamic value;

  CustomEvent({required this.name, required this.value})
    : super(type: EventType.custom);

  factory CustomEvent.fromJson(Map<String, dynamic> json) =>
      _$CustomEventFromJson(json);
  @override
  Map<String, dynamic> toJson() => _$CustomEventToJson(this);
}
