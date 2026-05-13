import 'package:flutter/material.dart';
import 'package:flutter_ai_toolkit/flutter_ai_toolkit.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:provider/provider.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;

import '../agent/socket_provider.dart';
import '../models/panel_type.dart';
import '../screens/full_screen_panel_view.dart';

class ChatPanel extends StatefulWidget {
  final io.Socket socket;
  final bool isFullScreen;

  const ChatPanel({
    super.key,
    required this.socket,
    this.isFullScreen = false,
  });

  @override
  State<ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends State<ChatPanel> {
  late ChatSocketProvider _chatProvider;

  static const List<String> _quickQuestions = [
    'Create a new vyos network with 4 p routers, 8 pe routers, 16 ce routers and 2 devices per ce router',
    'Add a new PE router called pe5 to core router P3 and update the underlay and router reflectors to include the new PE router',
    'Add a new mesh vpn called GREEN to pe2 and pe5',
    'what network failures can i deploy',
    'Inject a link down bridge network failure between p1 and p2'
  ];

  @override
  void initState() {
    super.initState();
    _chatProvider = ChatSocketProvider(socket: widget.socket);
  }

  @override
  void dispose() {
    _chatProvider.dispose();
    super.dispose();
  }

  /// Simple response builder — renders markdown text only.
  Widget _buildResponse(BuildContext context, String response) {
    return MarkdownBody(data: response);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // ── Header bar ──────────────────────────────────────────────────────
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD),
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Quick-question dropdown
              PopupMenuButton<String>(
                icon: const Icon(Icons.menu, color: Color(0xFF0D47A1)),
                tooltip: 'Quick questions',
                padding: EdgeInsets.zero,
                onSelected: (String value) {
                  _chatProvider.generateStream(value);
                },
                itemBuilder: (BuildContext context) => _quickQuestions
                    .map((q) => PopupMenuItem<String>(value: q, child: Text(q)))
                    .toList(),
              ),
              // Title
              Expanded(
                child: Center(
                  child: Text(
                    'Network Agent Chat',
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: const Color(0xFF0D47A1),
                        ),
                  ),
                ),
              ),
              // Expand / collapse
              IconButton(
                icon: Icon(
                  widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                  color: const Color(0xFF0D47A1),
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
              // Reset chat
              IconButton(
                icon: const Icon(Icons.delete_forever, color: Color(0xFF0D47A1)),
                tooltip: 'Reset chat',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () => _chatProvider.resetConversation(),
              ),
            ],
          ),
        ),

        // ── Chat view ────────────────────────────────────────────────────────
        Expanded(
          child: ChangeNotifierProvider.value(
            value: _chatProvider,
            child: LlmChatView(
              provider: _chatProvider,
              welcomeMessage:
                  'Welcome to the Network Agent! How can I help you manage your network today?',
              enableAttachments: false,
              enableVoiceNotes: false,
              responseBuilder: _buildResponse,
            ),
          ),
        ),
      ],
    );
  }
}
