import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

class RemoteTaskWidget extends StatefulWidget {
  final String agentName;
  final String toolCallId;
  final bool isCompleted;
  final VoidCallback? onComplete;

  const RemoteTaskWidget({
    super.key,
    required this.agentName,
    required this.toolCallId,
    this.isCompleted = false,
    this.onComplete,
  });

  @override
  State<RemoteTaskWidget> createState() => _RemoteTaskWidgetState();
}

class _RemoteTaskWidgetState extends State<RemoteTaskWidget> {
  @override
  void didUpdateWidget(RemoteTaskWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isCompleted != widget.isCompleted && widget.isCompleted) {
      widget.onComplete?.call();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 8.0),
      child: Row(
        children: [
          // Progress indicator at the beginning
          if (!widget.isCompleted)
            SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                valueColor: AlwaysStoppedAnimation<Color>(
                  Colors.blue[600]!,
                ),
              ),
            ),
          if (!widget.isCompleted) const SizedBox(width: 8),
          // Agent name
          Expanded(
            child: Text(
              widget.agentName,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Colors.grey[700],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
