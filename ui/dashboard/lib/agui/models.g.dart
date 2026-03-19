// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'models.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

RunAgentInput _$RunAgentInputFromJson(Map<String, dynamic> json) =>
    RunAgentInput(
      threadId: json['threadId'] as String,
      runId: json['runId'] as String,
      state: json['state'],
      messages: (json['messages'] as List<dynamic>)
          .map((e) => Message.fromJson(e as Map<String, dynamic>))
          .toList(),
      tools: (json['tools'] as List<dynamic>)
          .map((e) => Tool.fromJson(e as Map<String, dynamic>))
          .toList(),
      context: (json['context'] as List<dynamic>)
          .map((e) => Context.fromJson(e as Map<String, dynamic>))
          .toList(),
      forwardedProps: json['forwardedProps'],
    );

Map<String, dynamic> _$RunAgentInputToJson(RunAgentInput instance) =>
    <String, dynamic>{
      'threadId': instance.threadId,
      'runId': instance.runId,
      'state': instance.state,
      'messages': instance.messages,
      'tools': instance.tools,
      'context': instance.context,
      'forwardedProps': instance.forwardedProps,
    };

Message _$MessageFromJson(Map<String, dynamic> json) => Message(
  id: json['id'] as String,
  role: $enumDecode(_$RoleEnumMap, json['role']),
);

Map<String, dynamic> _$MessageToJson(Message instance) => <String, dynamic>{
  'id': instance.id,
  'role': _$RoleEnumMap[instance.role]!,
};

const _$RoleEnumMap = {
  Role.developer: 'developer',
  Role.system: 'system',
  Role.assistant: 'assistant',
  Role.user: 'user',
  Role.tool: 'tool',
};

BaseMessage _$BaseMessageFromJson(Map<String, dynamic> json) => BaseMessage(
  id: json['id'] as String,
  role: $enumDecode(_$RoleEnumMap, json['role']),
  name: json['name'] as String?,
);

Map<String, dynamic> _$BaseMessageToJson(BaseMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'role': _$RoleEnumMap[instance.role]!,
      'name': instance.name,
    };

DeveloperMessage _$DeveloperMessageFromJson(Map<String, dynamic> json) =>
    DeveloperMessage(
      id: json['id'] as String,
      content: json['content'] as String? ?? '',
      name: json['name'] as String?,
    );

Map<String, dynamic> _$DeveloperMessageToJson(DeveloperMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'name': instance.name,
      'content': instance.content,
    };

SystemMessage _$SystemMessageFromJson(Map<String, dynamic> json) =>
    SystemMessage(
      id: json['id'] as String,
      content: json['content'] as String? ?? '',
      name: json['name'] as String?,
    );

Map<String, dynamic> _$SystemMessageToJson(SystemMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'name': instance.name,
      'content': instance.content,
    };

AssistantMessage _$AssistantMessageFromJson(Map<String, dynamic> json) =>
    AssistantMessage(
      id: json['id'] as String,
      content: json['content'] as String?,
      toolCalls: (json['toolCalls'] as List<dynamic>?)
          ?.map((e) => ToolCall.fromJson(e as Map<String, dynamic>))
          .toList(),
      name: json['name'] as String?,
    );

Map<String, dynamic> _$AssistantMessageToJson(AssistantMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'name': instance.name,
      'content': instance.content,
      'toolCalls': instance.toolCalls,
    };

UserMessage _$UserMessageFromJson(Map<String, dynamic> json) => UserMessage(
  id: json['id'] as String,
  content: json['content'] as String? ?? '',
  name: json['name'] as String?,
);

Map<String, dynamic> _$UserMessageToJson(UserMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'name': instance.name,
      'content': instance.content,
    };

ToolMessage _$ToolMessageFromJson(Map<String, dynamic> json) => ToolMessage(
  id: json['id'] as String,
  content: json['content'] as String? ?? '',
  toolCallId: json['toolCallId'] as String,
  error: json['error'] as String?,
);

Map<String, dynamic> _$ToolMessageToJson(ToolMessage instance) =>
    <String, dynamic>{
      'id': instance.id,
      'content': instance.content,
      'toolCallId': instance.toolCallId,
      'error': instance.error,
    };

ToolCall _$ToolCallFromJson(Map<String, dynamic> json) => ToolCall(
  id: json['id'] as String,
  type: json['type'] as String? ?? 'function',
  function: FunctionCall.fromJson(json['function'] as Map<String, dynamic>),
);

Map<String, dynamic> _$ToolCallToJson(ToolCall instance) => <String, dynamic>{
  'id': instance.id,
  'type': instance.type,
  'function': instance.function,
};

FunctionCall _$FunctionCallFromJson(Map<String, dynamic> json) => FunctionCall(
  name: json['name'] as String,
  arguments: json['arguments'] as String,
);

Map<String, dynamic> _$FunctionCallToJson(FunctionCall instance) =>
    <String, dynamic>{'name': instance.name, 'arguments': instance.arguments};

Context _$ContextFromJson(Map<String, dynamic> json) => Context(
  description: json['description'] as String,
  value: json['value'] as String,
);

Map<String, dynamic> _$ContextToJson(Context instance) => <String, dynamic>{
  'description': instance.description,
  'value': instance.value,
};

Tool _$ToolFromJson(Map<String, dynamic> json) => Tool(
  name: json['name'] as String,
  description: json['description'] as String,
  parameters: json['parameters'],
);

Map<String, dynamic> _$ToolToJson(Tool instance) => <String, dynamic>{
  'name': instance.name,
  'description': instance.description,
  'parameters': instance.parameters,
};

Event _$EventFromJson(Map<String, dynamic> json) =>
    Event(type: $enumDecode(_$EventTypeEnumMap, json['type']));

Map<String, dynamic> _$EventToJson(Event instance) => <String, dynamic>{
  'type': _$EventTypeEnumMap[instance.type]!,
};

const _$EventTypeEnumMap = {
  EventType.textMessageStart: 'TEXT_MESSAGE_START',
  EventType.textMessageContent: 'TEXT_MESSAGE_CONTENT',
  EventType.textMessageEnd: 'TEXT_MESSAGE_END',
  EventType.toolCallStart: 'TOOL_CALL_START',
  EventType.toolCallArgs: 'TOOL_CALL_ARGS',
  EventType.toolCallEnd: 'TOOL_CALL_END',
  EventType.toolCallResult: 'TOOL_CALL_RESULT',
  EventType.stateSnapshot: 'STATE_SNAPSHOT',
  EventType.stateDelta: 'STATE_DELTA',
  EventType.messagesSnapshot: 'MESSAGES_SNAPSHOT',
  EventType.raw: 'RAW',
  EventType.custom: 'CUSTOM',
  EventType.runStarted: 'RUN_STARTED',
  EventType.runFinished: 'RUN_FINISHED',
  EventType.runError: 'RUN_ERROR',
  EventType.stepStarted: 'STEP_STARTED',
  EventType.stepFinished: 'STEP_FINISHED',
};

BaseEvent _$BaseEventFromJson(Map<String, dynamic> json) => BaseEvent(
  type: $enumDecode(_$EventTypeEnumMap, json['type']),
  timestamp: (json['timestamp'] as num?)?.toInt(),
  rawEvent: json['rawEvent'],
);

Map<String, dynamic> _$BaseEventToJson(BaseEvent instance) => <String, dynamic>{
  'type': _$EventTypeEnumMap[instance.type]!,
  'timestamp': instance.timestamp,
  'rawEvent': instance.rawEvent,
};

RunStartedEvent _$RunStartedEventFromJson(Map<String, dynamic> json) =>
    RunStartedEvent(
      threadId: json['threadId'] as String,
      runId: json['runId'] as String,
    );

Map<String, dynamic> _$RunStartedEventToJson(RunStartedEvent instance) =>
    <String, dynamic>{'threadId': instance.threadId, 'runId': instance.runId};

RunFinishedEvent _$RunFinishedEventFromJson(Map<String, dynamic> json) =>
    RunFinishedEvent(
      threadId: json['threadId'] as String,
      runId: json['runId'] as String,
      result: json['result'],
    );

Map<String, dynamic> _$RunFinishedEventToJson(RunFinishedEvent instance) =>
    <String, dynamic>{
      'threadId': instance.threadId,
      'runId': instance.runId,
      'result': instance.result,
    };

RunErrorEvent _$RunErrorEventFromJson(Map<String, dynamic> json) =>
    RunErrorEvent(
      message: json['message'] as String,
      code: json['code'] as String?,
    );

Map<String, dynamic> _$RunErrorEventToJson(RunErrorEvent instance) =>
    <String, dynamic>{'message': instance.message, 'code': instance.code};

StepStartedEvent _$StepStartedEventFromJson(Map<String, dynamic> json) =>
    StepStartedEvent(stepName: json['stepName'] as String);

Map<String, dynamic> _$StepStartedEventToJson(StepStartedEvent instance) =>
    <String, dynamic>{'stepName': instance.stepName};

StepFinishedEvent _$StepFinishedEventFromJson(Map<String, dynamic> json) =>
    StepFinishedEvent(stepName: json['stepName'] as String);

Map<String, dynamic> _$StepFinishedEventToJson(StepFinishedEvent instance) =>
    <String, dynamic>{'stepName': instance.stepName};

TextMessageStartEvent _$TextMessageStartEventFromJson(
  Map<String, dynamic> json,
) => TextMessageStartEvent(
  messageId: json['messageId'] as String,
  role: $enumDecodeNullable(_$RoleEnumMap, json['role']) ?? Role.assistant,
);

Map<String, dynamic> _$TextMessageStartEventToJson(
  TextMessageStartEvent instance,
) => <String, dynamic>{
  'messageId': instance.messageId,
  'role': _$RoleEnumMap[instance.role]!,
};

TextMessageContentEvent _$TextMessageContentEventFromJson(
  Map<String, dynamic> json,
) => TextMessageContentEvent(
  messageId: json['messageId'] as String,
  delta: json['delta'] as String,
);

Map<String, dynamic> _$TextMessageContentEventToJson(
  TextMessageContentEvent instance,
) => <String, dynamic>{
  'messageId': instance.messageId,
  'delta': instance.delta,
};

TextMessageEndEvent _$TextMessageEndEventFromJson(Map<String, dynamic> json) =>
    TextMessageEndEvent(messageId: json['messageId'] as String);

Map<String, dynamic> _$TextMessageEndEventToJson(
  TextMessageEndEvent instance,
) => <String, dynamic>{'messageId': instance.messageId};

ToolCallStartEvent _$ToolCallStartEventFromJson(Map<String, dynamic> json) =>
    ToolCallStartEvent(
      toolCallId: json['toolCallId'] as String,
      toolCallName: json['toolCallName'] as String,
      parentMessageId: json['parentMessageId'] as String?,
    );

Map<String, dynamic> _$ToolCallStartEventToJson(ToolCallStartEvent instance) =>
    <String, dynamic>{
      'toolCallId': instance.toolCallId,
      'toolCallName': instance.toolCallName,
      'parentMessageId': instance.parentMessageId,
    };

ToolCallArgsEvent _$ToolCallArgsEventFromJson(Map<String, dynamic> json) =>
    ToolCallArgsEvent(
      toolCallId: json['toolCallId'] as String,
      delta: json['delta'] as String,
    );

Map<String, dynamic> _$ToolCallArgsEventToJson(ToolCallArgsEvent instance) =>
    <String, dynamic>{
      'toolCallId': instance.toolCallId,
      'delta': instance.delta,
    };

ToolCallEndEvent _$ToolCallEndEventFromJson(Map<String, dynamic> json) =>
    ToolCallEndEvent(toolCallId: json['toolCallId'] as String);

Map<String, dynamic> _$ToolCallEndEventToJson(ToolCallEndEvent instance) =>
    <String, dynamic>{'toolCallId': instance.toolCallId};

ToolCallResultEvent _$ToolCallResultEventFromJson(Map<String, dynamic> json) =>
    ToolCallResultEvent(
      messageId: json['messageId'] as String,
      toolCallId: json['toolCallId'] as String,
      content: json['content'] as String,
      role: $enumDecodeNullable(_$RoleEnumMap, json['role']) ?? Role.tool,
    );

Map<String, dynamic> _$ToolCallResultEventToJson(
  ToolCallResultEvent instance,
) => <String, dynamic>{
  'messageId': instance.messageId,
  'toolCallId': instance.toolCallId,
  'content': instance.content,
  'role': _$RoleEnumMap[instance.role],
};

StateSnapshotEvent _$StateSnapshotEventFromJson(Map<String, dynamic> json) =>
    StateSnapshotEvent(snapshot: json['snapshot']);

Map<String, dynamic> _$StateSnapshotEventToJson(StateSnapshotEvent instance) =>
    <String, dynamic>{'snapshot': instance.snapshot};

StateDeltaEvent _$StateDeltaEventFromJson(Map<String, dynamic> json) =>
    StateDeltaEvent(delta: json['delta'] as List<dynamic>);

Map<String, dynamic> _$StateDeltaEventToJson(StateDeltaEvent instance) =>
    <String, dynamic>{'delta': instance.delta};

MessagesSnapshotEvent _$MessagesSnapshotEventFromJson(
  Map<String, dynamic> json,
) => MessagesSnapshotEvent(
  messages: (json['messages'] as List<dynamic>)
      .map((e) => Message.fromJson(e as Map<String, dynamic>))
      .toList(),
);

Map<String, dynamic> _$MessagesSnapshotEventToJson(
  MessagesSnapshotEvent instance,
) => <String, dynamic>{'messages': instance.messages};

RawEvent _$RawEventFromJson(Map<String, dynamic> json) =>
    RawEvent(event: json['event'], source: json['source'] as String?);

Map<String, dynamic> _$RawEventToJson(RawEvent instance) => <String, dynamic>{
  'event': instance.event,
  'source': instance.source,
};

CustomEvent _$CustomEventFromJson(Map<String, dynamic> json) =>
    CustomEvent(name: json['name'] as String, value: json['value']);

Map<String, dynamic> _$CustomEventToJson(CustomEvent instance) =>
    <String, dynamic>{'name': instance.name, 'value': instance.value};
