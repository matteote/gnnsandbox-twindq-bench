import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../../appstate.dart';
import '../../models/incident.dart';

class IncidentNotificationsWidget extends StatefulWidget {
  const IncidentNotificationsWidget({super.key});

  @override
  State<IncidentNotificationsWidget> createState() => _IncidentNotificationsWidgetState();
}

class _IncidentNotificationsWidgetState extends State<IncidentNotificationsWidget> {
  // Set to track which incident IDs are expanded
  final Set<String> _expandedCards = {};

  @override
  void initState() {
    super.initState();
    // Incidents are already loaded on startup and updated via notifications
    // No need to call refreshIncidents() here
  }

  // Toggle card expansion
  void _toggleCardExpansion(String incidentId) {
    setState(() {
      if (_expandedCards.contains(incidentId)) {
        _expandedCards.remove(incidentId);
      } else {
        _expandedCards.add(incidentId);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, child) {
        if (appState.isLoadingIncidents) {
          return const Center(
            child: CircularProgressIndicator(),
          );
        }

        final incidents = appState.incidents;
        
        if (incidents.isEmpty) {
          return Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text(
                  'No open incidents',
                  style: TextStyle(
                    fontSize: 18,
                    fontStyle: FontStyle.italic,
                    color: Colors.grey,
                  ),
                ),
                const SizedBox(height: 16),
                ElevatedButton.icon(
                  onPressed: () => appState.refreshIncidents(),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Refresh'),
                ),
              ],
            ),
          );
        }
        
        return Column(
          children: [
            // Header with refresh button
            Padding(
              padding: const EdgeInsets.all(8.0),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    '${incidents.length} Open Incidents',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  IconButton(
                    onPressed: () => appState.refreshIncidents(),
                    icon: const Icon(Icons.refresh),
                    tooltip: 'Refresh Incidents',
                  ),
                ],
              ),
            ),
            // Incidents list
            Expanded(
              child: Container(
                width: double.infinity,
                color: Colors.white,
                child: ListView.builder(
                  padding: const EdgeInsets.all(8.0),
                  itemCount: incidents.length,
                  itemBuilder: (context, index) {
                    final incident = incidents[index];
                    final isExpanded = _expandedCards.contains(incident.id);
                    
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 8.0),
                      child: Card(
                        elevation: 2,
                        margin: EdgeInsets.zero,
                        color: Colors.white,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                          side: BorderSide(color: Colors.grey.shade300, width: 1),
                        ),
                        child: InkWell(
                          onTap: () => _toggleCardExpansion(incident.id),
                          hoverColor: Colors.transparent,
                          splashColor: Colors.transparent,
                          highlightColor: Colors.transparent,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              // Card header
                              Padding(
                                padding: const EdgeInsets.all(12.0),
                                child: Row(
                                  children: [
                                    const SizedBox(width: 12),
                                    
                                    // Title and details
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            incident.title,
                                            style: const TextStyle(
                                              fontWeight: FontWeight.bold,
                                              fontSize: 16,
                                            ),
                                            overflow: TextOverflow.ellipsis,
                                          ),
                                          const SizedBox(height: 4),
                                          Row(
                                            children: [
                                              if (incident.affectedNode != null) ...[
                                                Icon(
                                                  Icons.device_hub,
                                                  size: 14,
                                                  color: Colors.grey[600],
                                                ),
                                                const SizedBox(width: 4),
                                                Text(
                                                  incident.affectedNode!,
                                                  style: TextStyle(
                                                    fontSize: 12,
                                                    color: Colors.grey[600],
                                                  ),
                                                ),
                                                const SizedBox(width: 12),
                                              ],
                                              if (incident.assignedAgent != null) ...[
                                                Icon(
                                                  Icons.person,
                                                  size: 14,
                                                  color: Colors.grey[600],
                                                ),
                                                const SizedBox(width: 4),
                                                Text(
                                                  incident.assignedAgent!,
                                                  style: TextStyle(
                                                    fontSize: 12,
                                                    color: Colors.grey[600],
                                                  ),
                                                ),
                                              ],
                                            ],
                                          ),
                                        ],
                                      ),
                                    ),
                                    
                                    // State chip
                                    Container(
                                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                                      decoration: BoxDecoration(
                                        color: _getStateColor(incident.state),
                                        borderRadius: BorderRadius.circular(12),
                                        boxShadow: [
                                          BoxShadow(
                                            color: _getStateColor(incident.state).withOpacity(0.3),
                                            blurRadius: 4,
                                            offset: const Offset(0, 2),
                                          ),
                                        ],
                                      ),
                                      child: Text(
                                        incident.state.toUpperCase(),
                                        style: const TextStyle(
                                          fontSize: 10,
                                          color: Colors.white,
                                          fontWeight: FontWeight.bold,
                                          letterSpacing: 0.5,
                                        ),
                                      ),
                                    ),
                                    
                                    const SizedBox(width: 8),
                                    
                                    // Timestamp
                                    Column(
                                      crossAxisAlignment: CrossAxisAlignment.end,
                                      children: [
                                        Text(
                                          '${_formatTimestamp(incident.createdAt)} UTC',
                                          style: const TextStyle(
                                            fontSize: 12,
                                            color: Colors.grey,
                                          ),
                                        ),
                                        if (incident.updatedAt != incident.createdAt)
                                          Text(
                                            'Updated ${_formatTimestamp(incident.updatedAt)} UTC',
                                            style: const TextStyle(
                                              fontSize: 10,
                                              color: Colors.grey,
                                            ),
                                          ),
                                      ],
                                    ),
                                    
                                    const SizedBox(width: 8),
                                    
                                    // Expand/collapse icon
                                    Icon(
                                      isExpanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                                      color: Colors.grey,
                                    ),
                                  ],
                                ),
                              ),
                              
                              // Content preview (when collapsed)
                              if (!isExpanded)
                                Padding(
                                  padding: const EdgeInsets.fromLTRB(12.0, 0.0, 12.0, 12.0),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      const Divider(),
                                      Text(
                                        _getFirstLines(incident.description),
                                        style: const TextStyle(fontSize: 14),
                                        overflow: TextOverflow.ellipsis,
                                        maxLines: 2,
                                      ),
                                      if (_hasMoreContent(incident.description))
                                        Padding(
                                          padding: const EdgeInsets.only(top: 4.0),
                                          child: Text(
                                            'Tap to expand...',
                                            style: TextStyle(
                                              color: Theme.of(context).colorScheme.primary,
                                              fontSize: 12,
                                              fontStyle: FontStyle.italic,
                                            ),
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                              
                              // Expanded content
                              if (isExpanded) ...[
                                const Divider(height: 1),
                                Padding(
                                  padding: const EdgeInsets.all(12.0),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      // Description
                                      const Text(
                                        'Description:',
                                        style: TextStyle(
                                          fontWeight: FontWeight.bold,
                                          fontSize: 14,
                                        ),
                                      ),
                                      const SizedBox(height: 8),
                                      MarkdownBody(
                                        data: incident.description,
                                        styleSheet: MarkdownStyleSheet(
                                          p: const TextStyle(fontSize: 14),
                                          code: const TextStyle(
                                            backgroundColor: Colors.grey,
                                            color: Colors.black,
                                            fontSize: 13,
                                          ),
                                          codeblockDecoration: BoxDecoration(
                                            color: Colors.grey[100],
                                            borderRadius: BorderRadius.circular(4.0),
                                          ),
                                        ),
                                      ),
                                      
                      // Progress tracking section
                      const SizedBox(height: 16),
                      const Text(
                        'Resolution Progress:',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 8),
                      _buildProgressSection(incident),
                      
                      // Additional incident details
                      const SizedBox(height: 16),
                      const Text(
                        'Incident Details:',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Container(
                        padding: const EdgeInsets.all(12.0),
                        decoration: BoxDecoration(
                          color: Colors.grey[50],
                          borderRadius: BorderRadius.circular(8.0),
                          border: Border.all(color: Colors.grey[300]!),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _buildDetailRow('Agent Task ID', incident.agentTaskId),
                            _buildDetailRow('Recorded', '${_formatTimestamp(incident.recordedTimestamp)} UTC'),
                            if (incident.resolvedTimestamp != null)
                              _buildDetailRow('Resolved', '${_formatTimestamp(incident.resolvedTimestamp!)} UTC'),
                            if (incident.lastProgressUpdate != null)
                              _buildDetailRow('Last Update', '${_formatTimestamp(incident.lastProgressUpdate!)} UTC'),
                            if (incident.cause != null && incident.cause!.isNotEmpty)
                              _buildDetailRow('Cause', incident.cause.toString()),
                            if (incident.resolution != null && incident.resolution!.isNotEmpty)
                              _buildDetailRow('Resolution', incident.resolution.toString()),
                          ],
                        ),
                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                      ),
                    );
                  },
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  // Helper method to format timestamp
  String _formatTimestamp(DateTime timestamp) {
    final now = DateTime.now();
    final difference = now.difference(timestamp);
    
    if (difference.inDays > 0) {
      return '${difference.inDays}d ago';
    } else if (difference.inHours > 0) {
      return '${difference.inHours}h ago';
    } else if (difference.inMinutes > 0) {
      return '${difference.inMinutes}m ago';
    } else {
      return 'Just now';
    }
  }

  // Helper method to get color based on incident state
  Color _getStateColor(String state) {
    switch (state.toLowerCase()) {
      case 'open':
        return Colors.red;
      case 'investigating':
        return Colors.orange;
      case 'resolved':
        return Colors.green;
      case 'closed':
        return Colors.grey;
      default:
        return Colors.blue;
    }
  }

  
  // Helper method to get first lines of content
  String _getFirstLines(String content) {
    final lines = content.split('\n');
    if (lines.isEmpty) {
      return '';
    }
    return lines.take(2).join('\n');
  }
  
  // Helper method to check if there's more content
  bool _hasMoreContent(String content) {
    return content.split('\n').length > 2;
  }
  
  // Helper method to build detail rows
  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$label: ',
            style: const TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 13,
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
  
  // Helper method to build progress section
  Widget _buildProgressSection(Incident incident) {
    return Container(
      padding: const EdgeInsets.all(12.0),
      decoration: BoxDecoration(
        color: Colors.blue[50],
        borderRadius: BorderRadius.circular(8.0),
        border: Border.all(color: Colors.blue[200]!),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Progress bar and stage
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      incident.progressStage,
                      style: const TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 14,
                      ),
                    ),
                    const SizedBox(height: 4),
                    LinearProgressIndicator(
                      value: incident.progressPercentage,
                      backgroundColor: Colors.grey[300],
                      valueColor: AlwaysStoppedAnimation<Color>(
                        _getProgressColor(incident.progressPercentage),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Text(
                '${(incident.progressPercentage * 100).toInt()}%',
                style: const TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 14,
                ),
              ),
            ],
          ),
          
          const SizedBox(height: 12),
          
          // Progress steps
          Row(
            children: [
              _buildProgressStep(
                'Initial',
                true,
                incident.progressPercentage >= 0.25,
                Icons.report_problem,
                incident,
                null, // No data for initial step
              ),
              _buildProgressConnector(incident.progressPercentage >= 0.5),
              _buildProgressStep(
                'Strategy',
                incident.hasStrategy,
                incident.progressPercentage >= 0.5,
                Icons.search,
                incident,
                incident.strategy,
              ),
              _buildProgressConnector(incident.progressPercentage >= 0.75),
              _buildProgressStep(
                'Root Cause',
                incident.hasRootCause,
                incident.progressPercentage >= 0.75,
                Icons.psychology,
                incident,
                incident.hasRootCause ? {'root_cause': incident.rootCause ?? 'No root cause data available'} : null,
              ),
              _buildProgressConnector(incident.progressPercentage >= 1.0),
              _buildProgressStep(
                'Resolution',
                incident.hasResolution,
                incident.progressPercentage >= 1.0,
                Icons.check_circle,
                incident,
                incident.hasResolution ? {'resolution': incident.resolution ?? 'No resolution data available'} : null,
              ),
            ],
          ),
        ],
      ),
    );
  }
  
  // Helper method to build progress steps
  Widget _buildProgressStep(String label, bool hasData, bool isCompleted, IconData icon, Incident incident, Map<String, dynamic>? stepData) {
    return Expanded(
      child: Column(
        children: [
          GestureDetector(
            onTap: () {
              if (label == 'Strategy' && stepData != null && stepData.isNotEmpty) {
                _showStrategyMarkdownDialog(stepData, icon, _getStepColor(label));
              } else if (stepData != null && stepData.isNotEmpty) {
                _showStepDetailsDialog(label, stepData, icon, _getStepColor(label));
              } else if (label == 'Initial') {
                _showInitialStepDialog(incident);
              }
            },
            child: Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isCompleted ? Colors.green : (hasData ? Colors.orange : Colors.grey[300]),
                border: Border.all(
                  color: isCompleted ? Colors.green : (hasData ? Colors.orange : Colors.grey),
                  width: 2,
                ),
                // Add a subtle shadow to indicate clickability when there's data
                boxShadow: (stepData != null && stepData.isNotEmpty) || label == 'Initial' ? [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.2),
                    blurRadius: 2,
                    offset: const Offset(0, 1),
                  ),
                ] : null,
              ),
              child: Icon(
                icon,
                size: 16,
                color: isCompleted || hasData ? Colors.white : Colors.grey,
              ),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            label,
            style: TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.bold,
              color: isCompleted ? Colors.green : (hasData ? Colors.orange : Colors.grey),
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
  
  // Helper method to build progress connector
  Widget _buildProgressConnector(bool isActive) {
    return Container(
      width: 20,
      height: 2,
      color: isActive ? Colors.green : Colors.grey[300],
      margin: const EdgeInsets.only(bottom: 20),
    );
  }
  
  // Helper method to build progress detail
  Widget _buildProgressDetail(String title, String description, IconData icon, Color color) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          icon,
          size: 16,
          color: color,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                  color: color,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                description,
                style: const TextStyle(
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
  
  // Helper method to get progress color
  Color _getProgressColor(double progress) {
    if (progress >= 1.0) return Colors.green;
    if (progress >= 0.75) return Colors.blue;
    if (progress >= 0.5) return Colors.orange;
    return Colors.red;
  }
  
  // Helper method to build detailed progress section with full data
  Widget _buildDetailedProgressSection(String title, Map<String, dynamic> data, IconData icon, Color color) {
    return Container(
      padding: const EdgeInsets.all(12.0),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(8.0),
        border: Border.all(color: color.withOpacity(0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Section header
          Row(
            children: [
              Icon(
                icon,
                size: 18,
                color: color,
              ),
              const SizedBox(width: 8),
              Text(
                title,
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 14,
                  color: color,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          
          // Display all key-value pairs from the data
          ...data.entries.map((entry) => _buildDataRow(entry.key, entry.value)).toList(),
        ],
      ),
    );
  }
  
  // Helper method to build data rows for detailed progress sections
  Widget _buildDataRow(String key, dynamic value) {
    // Format the key to be more readable
    String formattedKey = _formatKey(key);
    String formattedValue = _formatValue(value);
    
    return Padding(
      padding: const EdgeInsets.only(bottom: 6.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text(
              '$formattedKey:',
              style: const TextStyle(
                fontWeight: FontWeight.w600,
                fontSize: 12,
                color: Colors.black87,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _buildValueWidget(formattedValue),
          ),
        ],
      ),
    );
  }
  
  // Helper method to format keys for display
  String _formatKey(String key) {
    // Convert snake_case and camelCase to Title Case
    return key
        .replaceAllMapped(RegExp(r'[_]([a-z])'), (match) => ' ${match.group(1)!.toUpperCase()}')
        .replaceAllMapped(RegExp(r'([a-z])([A-Z])'), (match) => '${match.group(1)} ${match.group(2)}')
        .split(' ')
        .map((word) => word.isEmpty ? '' : word[0].toUpperCase() + word.substring(1).toLowerCase())
        .join(' ');
  }
  
  // Helper method to format values for display
  String _formatValue(dynamic value) {
    if (value == null) return 'N/A';
    if (value is String) return value;
    if (value is bool) return value ? 'Yes' : 'No';
    if (value is num) return value.toString();
    if (value is List) {
      return value.map((item) => _formatValue(item)).join(', ');
    }
    if (value is Map) {
      // For nested objects, create a formatted string
      return value.entries
          .map((entry) => '${_formatKey(entry.key)}: ${_formatValue(entry.value)}')
          .join('; ');
    }
    return value.toString();
  }
  
  // Helper method to build value widget (supports markdown for longer text)
  Widget _buildValueWidget(String value) {
    // If the value is long or contains markdown-like formatting, use MarkdownBody
    if (value.length > 100 || value.contains('\n') || value.contains('*') || value.contains('`')) {
      return MarkdownBody(
        data: value,
        styleSheet: MarkdownStyleSheet(
          p: const TextStyle(fontSize: 12),
          code: const TextStyle(
            backgroundColor: Colors.grey,
            color: Colors.black,
            fontSize: 11,
          ),
          codeblockDecoration: BoxDecoration(
            color: Colors.grey[100],
            borderRadius: BorderRadius.circular(4.0),
          ),
        ),
      );
    } else {
      return Text(
        value,
        style: const TextStyle(
          fontSize: 12,
          color: Colors.black87,
        ),
      );
    }
  }
  
  // Helper method to get step color
  Color _getStepColor(String stepLabel) {
    switch (stepLabel.toLowerCase()) {
      case 'strategy':
        return Colors.orange;
      case 'root cause':
        return Colors.blue;
      case 'resolution':
        return Colors.green;
      case 'initial':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }
  
  // Show dialog for strategy with markdown formatting
  void _showStrategyMarkdownDialog(Map<String, dynamic> strategyData, IconData icon, Color color) {
    String markdownContent = _convertJsonToMarkdown(strategyData);
    
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return Dialog(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16.0),
          ),
          child: Container(
            constraints: const BoxConstraints(
              maxWidth: 600,
              maxHeight: 500,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Dialog header
                Container(
                  padding: const EdgeInsets.all(20.0),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.1),
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(16.0),
                      topRight: Radius.circular(16.0),
                    ),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        icon,
                        size: 24,
                        color: color,
                      ),
                      const SizedBox(width: 12),
                      const Expanded(
                        child: Text(
                          'Investigation Strategy',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: Colors.orange,
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close),
                        color: Colors.grey[600],
                      ),
                    ],
                  ),
                ),
                
                // Dialog content with markdown
                Flexible(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(20.0),
                    child: MarkdownBody(
                      data: markdownContent,
                      styleSheet: MarkdownStyleSheet(
                        p: const TextStyle(fontSize: 14),
                        h1: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                        h2: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                        h3: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold),
                        listBullet: const TextStyle(fontSize: 14),
                        code: const TextStyle(
                          backgroundColor: Colors.grey,
                          color: Colors.black,
                          fontSize: 13,
                        ),
                        codeblockDecoration: BoxDecoration(
                          color: Colors.grey[100],
                          borderRadius: BorderRadius.circular(4.0),
                          border: Border.all(color: Colors.grey[300]!),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
  
  // Convert JSON data to markdown bullet points
  String _convertJsonToMarkdown(Map<String, dynamic> data, {int indentLevel = 0}) {
    StringBuffer markdown = StringBuffer();
    String indent = '  ' * indentLevel;
    
    for (var entry in data.entries) {
      String key = _formatKey(entry.key);
      dynamic value = entry.value;
      
      if (value == null) {
        markdown.writeln('${indent}- **$key**: N/A');
      } else if (value is String) {
        // Handle multi-line strings
        if (value.contains('\n')) {
          markdown.writeln('${indent}- **$key**:');
          for (String line in value.split('\n')) {
            if (line.trim().isNotEmpty) {
              markdown.writeln('${indent}  - ${line.trim()}');
            }
          }
        } else {
          markdown.writeln('${indent}- **$key**: $value');
        }
      } else if (value is bool) {
        markdown.writeln('${indent}- **$key**: ${value ? 'Yes' : 'No'}');
      } else if (value is num) {
        markdown.writeln('${indent}- **$key**: $value');
      } else if (value is List) {
        markdown.writeln('${indent}- **$key**:');
        for (int i = 0; i < value.length; i++) {
          dynamic item = value[i];
          if (item is Map<String, dynamic>) {
            markdown.writeln('${indent}  - Node ${i + 1}:');
            markdown.write(_convertJsonToMarkdown(item, indentLevel: indentLevel + 2));
          } else {
            markdown.writeln('${indent}  - ${_formatValue(item)}');
          }
        }
      } else if (value is Map<String, dynamic>) {
        markdown.writeln('${indent}- **$key**:');
        markdown.write(_convertJsonToMarkdown(value, indentLevel: indentLevel + 1));
      } else {
        markdown.writeln('${indent}- **$key**: ${value.toString()}');
      }
    }
    
    return markdown.toString();
  }

  // Show dialog for step details
  void _showStepDetailsDialog(String title, Map<String, dynamic> data, IconData icon, Color color) {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return Dialog(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16.0),
          ),
          child: Container(
            constraints: const BoxConstraints(
              maxWidth: 600,
              maxHeight: 500,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Dialog header
                Container(
                  padding: const EdgeInsets.all(20.0),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.1),
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(16.0),
                      topRight: Radius.circular(16.0),
                    ),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        icon,
                        size: 24,
                        color: color,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          title,
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: color,
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close),
                        color: Colors.grey[600],
                      ),
                    ],
                  ),
                ),
                
                // Dialog content
                Flexible(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(20.0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: data.entries.map((entry) => 
                        _buildDialogDataRow(entry.key, entry.value)
                      ).toList(),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
  
  // Show dialog for initial step (incident details)
  void _showInitialStepDialog(Incident incident) {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return Dialog(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16.0),
          ),
          child: Container(
            constraints: const BoxConstraints(
              maxWidth: 600,
              maxHeight: 500,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Dialog header
                Container(
                  padding: const EdgeInsets.all(20.0),
                  decoration: BoxDecoration(
                    color: Colors.red.withOpacity(0.1),
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(16.0),
                      topRight: Radius.circular(16.0),
                    ),
                  ),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.report_problem,
                        size: 24,
                        color: Colors.red,
                      ),
                      const SizedBox(width: 12),
                      const Expanded(
                        child: Text(
                          'Initial Incident Report',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: Colors.red,
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close),
                        color: Colors.grey[600],
                      ),
                    ],
                  ),
                ),
                
                // Dialog content
                Flexible(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.all(20.0),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _buildDialogDataRow('Title', incident.title),
                        _buildDialogDataRow('Description', incident.description),
                        _buildDialogDataRow('Severity', incident.severity),
                        if (incident.affectedNode != null)
                          _buildDialogDataRow('Affected Node', incident.affectedNode!),
                        if (incident.assignedAgent != null)
                          _buildDialogDataRow('Assigned Agent', incident.assignedAgent!),
                        _buildDialogDataRow('Recorded Time', '${_formatTimestamp(incident.recordedTimestamp)} UTC'),
                        _buildDialogDataRow('Agent Task ID', incident.agentTaskId),
                        
                        // Show all issue data
                        const SizedBox(height: 16),
                        const Text(
                          'Raw Issue Data:',
                          style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 14,
                          ),
                        ),
                        const SizedBox(height: 8),
                        ...incident.issue.entries.map((entry) => 
                          _buildDialogDataRow(entry.key, entry.value)
                        ).toList(),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
  
  // Helper method to build data rows for dialogs
  Widget _buildDialogDataRow(String key, dynamic value) {
    String formattedKey = _formatKey(key);
    String formattedValue = _formatValue(value);
    
    return Padding(
      padding: const EdgeInsets.only(bottom: 12.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$formattedKey:',
            style: const TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 14,
              color: Colors.black87,
            ),
          ),
          const SizedBox(height: 4),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12.0),
            decoration: BoxDecoration(
              color: Colors.grey[50],
              borderRadius: BorderRadius.circular(8.0),
              border: Border.all(color: Colors.grey[300]!),
            ),
            child: _buildDialogValueWidget(formattedValue),
          ),
        ],
      ),
    );
  }
  
  // Helper method to build value widget for dialogs
  Widget _buildDialogValueWidget(String value) {
    // If the value is long or contains markdown-like formatting, use MarkdownBody
    if (value.length > 100 || value.contains('\n') || value.contains('*') || value.contains('`')) {
      return MarkdownBody(
        data: value,
        styleSheet: MarkdownStyleSheet(
          p: const TextStyle(fontSize: 14),
          code: const TextStyle(
            backgroundColor: Colors.grey,
            color: Colors.black,
            fontSize: 13,
          ),
          codeblockDecoration: BoxDecoration(
            color: Colors.grey[200],
            borderRadius: BorderRadius.circular(4.0),
          ),
        ),
      );
    } else {
      return Text(
        value,
        style: const TextStyle(
          fontSize: 14,
          color: Colors.black87,
        ),
      );
    }
  }
}
