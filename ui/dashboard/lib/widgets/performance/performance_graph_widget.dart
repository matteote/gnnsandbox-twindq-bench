import 'dart:async';
import 'package:flutter/material.dart';
import '../../models/panel_type.dart';
import '../../screens/full_screen_panel_view.dart';
import '../../utils/APIService.dart';
import '../../models/metrics.dart';
import '../../models/metric_entry.dart';

class PerformanceGraphWidget extends StatefulWidget {
  final socket;
  final bool isLoading;
  final bool isFullScreen;

  const PerformanceGraphWidget({
    super.key,
    required this.socket,
    this.isLoading = false,
    this.isFullScreen = false,
  });

  @override
  State<PerformanceGraphWidget> createState() => _PerformanceGraphWidgetState();
}

class _PerformanceGraphWidgetState extends State<PerformanceGraphWidget> {
  final APIService _apiService = APIService();
  Timer? _pollingTimer;
  Metrics? _metrics;
  bool _isLoadingMetrics = true;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _fetchMetrics();
    // Auto-refresh every 20 seconds
    _pollingTimer = Timer.periodic(const Duration(seconds: 20), (_) {
      _fetchMetrics();
    });
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchMetrics() async {
    try {
      final metrics = await _apiService.getAllLastMetrics();
      
      if (mounted) {
        setState(() {
          _metrics = metrics;
          _isLoadingMetrics = false;
          _errorMessage = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoadingMetrics = false;
          _errorMessage = e.toString();
        });
      }
    }
  }

  // Calculate aggregated metrics for a router
  Map<String, dynamic> _calculateRouterMetrics(List<MetricEntry> entries) {
    if (entries.isEmpty) {
      return {
        'totalUpload': null,
        'totalDownload': null,
        'interfaceCount': 0,
        'interfaces': <String, Map<String, dynamic>>{},
        'timestamp': 0,
        'hasIssues': false,
        'hasData': false,
      };
    }

    final latestEntry = entries.last;
    final interfaces = latestEntry.interfaces;
    
    double? totalUpload = 0.0;
    double? totalDownload = 0.0;
    Map<String, Map<String, dynamic>> interfaceDetails = {};
    bool hasIssues = false;
    bool hasAnyData = false;
    int nullCount = 0;

    interfaces.forEach((interfaceName, interfaceData) {
      if (interfaceData is Map) {
        final receiveThroughput = (interfaceData['byte_recv_throughput'] as num?)?.toDouble();
        final sendThroughput = (interfaceData['byte_sent_throughput'] as num?)?.toDouble();
        
        // Track if we have any non-null data
        if (receiveThroughput != null || sendThroughput != null) {
          hasAnyData = true;
        } else {
          nullCount++;
        }
        
        // Only add to totals if not null
        if (sendThroughput != null) {
          totalUpload = (totalUpload ?? 0.0) + sendThroughput;
        }
        if (receiveThroughput != null) {
          totalDownload = (totalDownload ?? 0.0) + receiveThroughput;
        }
        
        // Check for issues (very high traffic)
        final hasTraffic = (receiveThroughput != null && receiveThroughput > 0) || 
                          (sendThroughput != null && sendThroughput > 0);
        final veryHighTraffic = (receiveThroughput != null && receiveThroughput > 1000000000) || 
                               (sendThroughput != null && sendThroughput > 1000000000); // > 1 GB/s
        
        interfaceDetails[interfaceName] = {
          'upload': sendThroughput,
          'download': receiveThroughput,
          'hasIssues': veryHighTraffic,
          'isActive': hasTraffic,
          'hasData': receiveThroughput != null || sendThroughput != null,
        };
        
        if (veryHighTraffic) hasIssues = true;
      }
    });

    // If all interfaces have null data, set totals to null
    if (nullCount == interfaces.length) {
      totalUpload = null;
      totalDownload = null;
    }

    return {
      'totalUpload': totalUpload,
      'totalDownload': totalDownload,
      'interfaceCount': interfaces.length,
      'interfaces': interfaceDetails,
      'timestamp': latestEntry.timestamp,
      'hasIssues': hasIssues,
      'hasData': hasAnyData,
    };
  }

  String _formatSpeed(double? bytesPerSecond) {
    if (bytesPerSecond == null) {
      return 'N/A';
    }
    if (bytesPerSecond < 1024) {
      return '${bytesPerSecond.toStringAsFixed(0)} B/s';
    } else if (bytesPerSecond < 1024 * 1024) {
      return '${(bytesPerSecond / 1024).toStringAsFixed(1)} KB/s';
    } else if (bytesPerSecond < 1024 * 1024 * 1024) {
      return '${(bytesPerSecond / (1024 * 1024)).toStringAsFixed(1)} MB/s';
    } else {
      return '${(bytesPerSecond / (1024 * 1024 * 1024)).toStringAsFixed(2)} GB/s';
    }
  }

  IconData _getRouterIcon(String routerName) {
    final lowerName = routerName.toLowerCase();
    
    if (lowerName.startsWith('p') && lowerName.length >= 2 && int.tryParse(lowerName[1]) != null) {
      // P router (e.g., p1, p2, p3, p4)
      return Icons.hub;
    } else if (lowerName.startsWith('pe')) {
      // PE router (e.g., pe1, pe2, pe3)
      return Icons.router;
    } else if (lowerName.startsWith('ce')) {
      // CE router (e.g., ce1-hub, ce2-spoke)
      return Icons.devices;
    } else if (lowerName.startsWith('rr')) {
      // Route Reflector (e.g., rr1, rr2)
      return Icons.settings_ethernet;
    } else {
      // Default router icon
      return Icons.router_outlined;
    }
  }

  String _formatTimestamp(int timestamp) {
    // Timestamp is already in milliseconds from the backend
    final date = DateTime.fromMillisecondsSinceEpoch(timestamp);
    final now = DateTime.now();
    final diff = now.difference(date);
    
    if (diff.inSeconds < 60) {
      return '${diff.inSeconds}s ago';
    } else if (diff.inMinutes < 60) {
      return '${diff.inMinutes}m ago';
    } else {
      return '${date.hour}:${date.minute.toString().padLeft(2, '0')}';
    }
  }

  Widget _buildRouterCard(String routerName, List<MetricEntry> entries) {
    final metrics = _calculateRouterMetrics(entries);
    final totalUpload = metrics['totalUpload'] as double?;
    final totalDownload = metrics['totalDownload'] as double?;
    final interfaceCount = metrics['interfaceCount'] as int;
    final interfaces = metrics['interfaces'] as Map<String, Map<String, dynamic>>;
    final timestamp = metrics['timestamp'] as int;
    final hasIssues = metrics['hasIssues'] as bool;
    final hasData = metrics['hasData'] as bool;

    return Card(
      elevation: 3,
      margin: const EdgeInsets.all(8.0),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12.0),
        side: BorderSide(
          color: hasIssues ? Colors.orange : const Color(0xFF0D47A1),
          width: hasIssues ? 2 : 1,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // Header with router name and status
            Row(
              children: [
                Icon(
                  _getRouterIcon(routerName),
                  color: hasIssues ? Colors.orange : (hasData ? const Color(0xFF0D47A1) : Colors.grey),
                  size: 20,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    routerName,
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                      color: hasIssues ? Colors.orange : (hasData ? const Color(0xFF0D47A1) : Colors.grey),
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (hasIssues)
                  Icon(
                    Icons.warning_amber_rounded,
                    color: Colors.orange,
                    size: 16,
                  ),
                if (!hasData)
                  Tooltip(
                    message: 'No throughput data available',
                    child: Icon(
                      Icons.info_outline,
                      color: Colors.grey,
                      size: 16,
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 4),
            
            // Timestamp
            Text(
              _formatTimestamp(timestamp),
              style: TextStyle(
                fontSize: 10,
                color: Colors.grey[600],
                fontStyle: FontStyle.italic,
              ),
            ),
            
            const SizedBox(height: 8),
            
            // Activity gauge
            _buildActivityGauge(totalUpload, totalDownload),
            
            const Divider(height: 16, thickness: 1),
            
            // Summary metrics in a compact row
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                // Upload
                Expanded(
                  child: _buildMetricColumn(
                    Icons.upload,
                    Colors.blue,
                    'Upload',
                    _formatSpeed(totalUpload),
                  ),
                ),
                Container(
                  width: 1,
                  height: 30,
                  color: Colors.grey[300],
                ),
                // Download
                Expanded(
                  child: _buildMetricColumn(
                    Icons.download,
                    Colors.green,
                    'Download',
                    _formatSpeed(totalDownload),
                  ),
                ),
                Container(
                  width: 1,
                  height: 30,
                  color: Colors.grey[300],
                ),
                // Interfaces
                Expanded(
                  child: _buildMetricColumn(
                    Icons.lan,
                    Colors.purple,
                    'Interfaces',
                    '$interfaceCount',
                  ),
                ),
              ],
            ),
            
            const Divider(height: 16, thickness: 1),
            
            // Interface list - compact view
            Text(
              'Interfaces:',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: Colors.grey[700],
              ),
            ),
            const SizedBox(height: 6),
            
            // Scrollable interface list with max height
            if (interfaces.isEmpty)
              Center(
                child: Text(
                  'No interfaces',
                  style: TextStyle(
                    fontSize: 10,
                    fontStyle: FontStyle.italic,
                    color: Colors.grey[500],
                  ),
                ),
              )
            else
              ConstrainedBox(
                constraints: const BoxConstraints(
                  maxHeight: 150, // Max height for interface list
                ),
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: interfaces.entries.map((entry) {
                final interfaceName = entry.key;
                final interfaceData = entry.value;
                final upload = interfaceData['upload'] as double?;
                final download = interfaceData['download'] as double?;
                final isActive = interfaceData['isActive'] as bool;
                final hasInterfaceIssues = interfaceData['hasIssues'] as bool;
                final hasInterfaceData = interfaceData['hasData'] as bool;

                return Padding(
                  padding: const EdgeInsets.only(bottom: 6.0),
                  child: Row(
                    children: [
                      // Status indicator
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: !hasInterfaceData
                              ? Colors.grey[400]
                              : (hasInterfaceIssues
                                  ? Colors.orange
                                  : (isActive ? Colors.green : Colors.grey)),
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 6),
                      
                      // Interface name
                      Expanded(
                        flex: 2,
                        child: Text(
                          interfaceName,
                          style: const TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                          ),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      
                      // Upload speed
                      Expanded(
                        flex: 2,
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.arrow_upward, size: 10, color: Colors.blue),
                            const SizedBox(width: 2),
                            Text(
                              _formatSpeed(upload),
                              style: const TextStyle(fontSize: 9),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                      
                      // Download speed
                      Expanded(
                        flex: 2,
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.arrow_downward, size: 10, color: Colors.green),
                            const SizedBox(width: 2),
                            Text(
                              _formatSpeed(download),
                              style: const TextStyle(fontSize: 9),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                    ],
                      ),
                    );
                    }).toList(),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildActivityGauge(double? upload, double? download) {
    // Calculate total throughput
    final total = (upload ?? 0.0) + (download ?? 0.0);
    
    // Calculate fill percentage as actual usage relative to 100MB
    final hundredMB = 100 * 1024 * 1024.0;
    double fillPercentage = total / hundredMB;
    // Cap at 100%
    if (fillPercentage > 1.0) fillPercentage = 1.0;
    
    // Determine activity level and color based on thresholds
    String activityLevel;
    Color gaugeColor;
    
    if (total == 0) {
      activityLevel = 'Idle';
      gaugeColor = Colors.grey;
    } else if (total < 1024 * 1024) { // < 1 MB/s
      activityLevel = 'Low';
      gaugeColor = Colors.green;
    } else if (total < 100 * 1024 * 1024) { // < 100 MB/s
      activityLevel = 'Medium';
      gaugeColor = Colors.blue;
    } else if (total < 1024 * 1024 * 1024) { // < 1 GB/s
      activityLevel = 'High';
      gaugeColor = Colors.orange;
    } else { // >= 1 GB/s
      activityLevel = 'Very High';
      gaugeColor = Colors.red;
    }
    
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Activity:',
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.bold,
                color: Colors.grey[700],
              ),
            ),
            Text(
              activityLevel,
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.bold,
                color: gaugeColor,
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        // Gauge bar
        Container(
          height: 8,
          decoration: BoxDecoration(
            color: Colors.grey[200],
            borderRadius: BorderRadius.circular(4),
          ),
          child: FractionallySizedBox(
            alignment: Alignment.centerLeft,
            widthFactor: fillPercentage,
            child: Container(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    gaugeColor.withOpacity(0.7),
                    gaugeColor,
                  ],
                ),
                borderRadius: BorderRadius.circular(4),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildMetricColumn(IconData icon, Color color, String label, String value) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 16, color: color),
        const SizedBox(height: 4),
        Text(
          value,
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.bold,
            color: Colors.grey[800],
          ),
          textAlign: TextAlign.center,
          overflow: TextOverflow.ellipsis,
        ),
        Text(
          label,
          style: TextStyle(
            fontSize: 9,
            color: Colors.grey[600],
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }

  // Calculate overall network summary
  Map<String, dynamic> _calculateNetworkSummary() {
    if (_metrics == null || _metrics!.data.isEmpty) {
      return {
        'totalRouters': 0,
        'totalUpload': 0.0,
        'totalDownload': 0.0,
        'totalInterfaces': 0,
        'activeInterfaces': 0,
        'routersWithIssues': 0,
      };
    }

    double totalUpload = 0.0;
    double totalDownload = 0.0;
    int totalInterfaces = 0;
    int activeInterfaces = 0;
    int routersWithIssues = 0;

    _metrics!.data.forEach((routerName, entries) {
      final routerMetrics = _calculateRouterMetrics(entries);
      final upload = routerMetrics['totalUpload'] as double?;
      final download = routerMetrics['totalDownload'] as double?;
      final interfaces = routerMetrics['interfaces'] as Map<String, Map<String, dynamic>>;
      final hasIssues = routerMetrics['hasIssues'] as bool;

      if (upload != null) totalUpload += upload;
      if (download != null) totalDownload += download;
      totalInterfaces += interfaces.length;
      
      interfaces.forEach((name, data) {
        if (data['isActive'] == true) activeInterfaces++;
      });

      if (hasIssues) routersWithIssues++;
    });

    return {
      'totalRouters': _metrics!.data.length,
      'totalUpload': totalUpload,
      'totalDownload': totalDownload,
      'totalInterfaces': totalInterfaces,
      'activeInterfaces': activeInterfaces,
      'routersWithIssues': routersWithIssues,
    };
  }

  Widget _buildNetworkSummary() {
    final summary = _calculateNetworkSummary();
    final totalRouters = summary['totalRouters'] as int;
    final totalUpload = summary['totalUpload'] as double;
    final totalDownload = summary['totalDownload'] as double;
    final totalInterfaces = summary['totalInterfaces'] as int;
    final activeInterfaces = summary['activeInterfaces'] as int;
    final routersWithIssues = summary['routersWithIssues'] as int;

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8.0, vertical: 8.0),
      padding: const EdgeInsets.all(16.0),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF0D47A1),
            const Color(0xFF1976D2),
          ],
        ),
        borderRadius: BorderRadius.circular(12.0),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.2),
            blurRadius: 8,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.summarize, color: Colors.white, size: 20),
              const SizedBox(width: 8),
              Text(
                'Network Summary',
                style: TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                  color: Colors.white,
                ),
              ),
              const Spacer(),
              if (routersWithIssues > 0)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.orange,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.warning_amber_rounded, color: Colors.white, size: 14),
                      const SizedBox(width: 4),
                      Text(
                        '$routersWithIssues Issue${routersWithIssues > 1 ? 's' : ''}',
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                          color: Colors.white,
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _buildSummaryMetric(
                  Icons.router,
                  'Routers',
                  '$totalRouters',
                  Colors.white70,
                ),
              ),
              Expanded(
                child: _buildSummaryMetric(
                  Icons.upload,
                  'Total Upload',
                  _formatSpeed(totalUpload),
                  Colors.lightBlue[200]!,
                ),
              ),
              Expanded(
                child: _buildSummaryMetric(
                  Icons.download,
                  'Total Download',
                  _formatSpeed(totalDownload),
                  Colors.lightGreen[200]!,
                ),
              ),
              Expanded(
                child: _buildSummaryMetric(
                  Icons.lan,
                  'Interfaces',
                  '$activeInterfaces / $totalInterfaces active',
                  Colors.purple[200]!,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          const Divider(color: Colors.white24, height: 1),
          const SizedBox(height: 12),
          // Router Activity Overview
          Text(
            'Router Activity Overview',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.bold,
              color: Colors.white,
            ),
          ),
          const SizedBox(height: 8),
          _buildRouterActivityOverview(),
        ],
      ),
    );
  }

  Widget _buildRouterActivityOverview() {
    if (_metrics == null || _metrics!.data.isEmpty) {
      return const SizedBox.shrink();
    }

    // Get all routers with their activity levels
    final routerActivities = <Map<String, dynamic>>[];
    
    _metrics!.data.forEach((routerName, entries) {
      final metrics = _calculateRouterMetrics(entries);
      final upload = metrics['totalUpload'] as double?;
      final download = metrics['totalDownload'] as double?;
      final total = (upload ?? 0.0) + (download ?? 0.0);
      
      // Calculate fill percentage as actual usage relative to 100MB
      final hundredMB = 100 * 1024 * 1024.0;
      double fillPercentage = total / hundredMB;
      // Cap at 100%
      if (fillPercentage > 1.0) fillPercentage = 1.0;
      
      // Determine activity level and color based on thresholds
      String activityLevel;
      Color gaugeColor;
      
      if (total == 0) {
        activityLevel = 'Idle';
        gaugeColor = Colors.grey;
      } else if (total < 1024 * 1024) { // < 1 MB/s
        activityLevel = 'Low';
        gaugeColor = Colors.green;
      } else if (total < 100 * 1024 * 1024) { // < 100 MB/s
        activityLevel = 'Medium';
        gaugeColor = Colors.blue;
      } else if (total < 1024 * 1024 * 1024) { // < 1 GB/s
        activityLevel = 'High';
        gaugeColor = Colors.orange;
      } else { // >= 1 GB/s
        activityLevel = 'Very High';
        gaugeColor = Colors.red;
      }
      
      routerActivities.add({
        'name': routerName,
        'level': activityLevel,
        'color': gaugeColor,
        'fill': fillPercentage,
        'total': total,
      });
    });
    
    // Sort by activity level (highest first)
    routerActivities.sort((a, b) => (b['total'] as double).compareTo(a['total'] as double));
    
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: routerActivities.asMap().entries.map((entry) {
          final index = entry.key;
          final router = entry.value;
          
          return Padding(
            padding: EdgeInsets.only(
              left: index == 0 ? 0 : 6,
              right: index == routerActivities.length - 1 ? 0 : 6,
            ),
            child: Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: (router['color'] as Color).withOpacity(0.3),
                  width: 1,
                ),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
              // Circular gauge
              SizedBox(
                width: 60,
                height: 60,
                child: CustomPaint(
                  painter: _CircularGaugePainter(
                    fillPercentage: router['fill'] as double,
                    gaugeColor: router['color'] as Color,
                  ),
                  child: Center(
                    child: Icon(
                      _getRouterIcon(router['name'] as String),
                      size: 20,
                      color: router['color'] as Color,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 6),
              // Router name
              SizedBox(
                width: 80,
                child: Text(
                  router['name'] as String,
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                  ),
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(height: 2),
                  // Activity level
                  Text(
                    router['level'] as String,
                    style: TextStyle(
                      fontSize: 9,
                      fontWeight: FontWeight.bold,
                      color: router['color'] as Color,
                    ),
                  ),
                ],
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildSummaryMetric(IconData icon, String label, String value, Color iconColor) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: iconColor, size: 20),
        const SizedBox(height: 6),
        Text(
          value,
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.bold,
            color: Colors.white,
          ),
          textAlign: TextAlign.center,
          overflow: TextOverflow.ellipsis,
        ),
        Text(
          label,
          style: TextStyle(
            fontSize: 10,
            color: Colors.white70,
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Header with title and expand button
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD), // Light blue background
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Stack(
            alignment: Alignment.center,
            children: [
              // Centered Title
              Center(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.speed, color: Color(0xFF0D47A1), size: 18),
                    const SizedBox(width: 8),
                    Text(
                      'Router Performance Metrics',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: const Color(0xFF0D47A1),
                      ),
                    ),
                    const SizedBox(width: 8),
                    // Auto-refresh indicator
                    Tooltip(
                      message: 'Auto-refreshes every 20 seconds',
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.green.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: Colors.green, width: 1),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.refresh, size: 12, color: Colors.green[700]),
                            const SizedBox(width: 4),
                            Text(
                              '20s',
                              style: TextStyle(
                                fontSize: 10,
                                color: Colors.green[700],
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              
              // Expand/Collapse button (positioned on the right)
              Positioned(
                right: 0,
                child: IconButton(
                  icon: Icon(
                    widget.isFullScreen ? Icons.fullscreen_exit : Icons.fullscreen,
                    color: const Color(0xFF0D47A1),
                  ),
                  tooltip: widget.isFullScreen ? 'Exit full screen' : 'Expand to full screen',
                  onPressed: () {
                    if (widget.isFullScreen) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (context) => FullScreenPanelView(
                            panelType: PanelType.performance,
                            socket: widget.socket,
                            isLoading: widget.isLoading,
                          ),
                        ),
                      );
                    }
                  },
                ),
              ),
            ],
          ),
        ),
        
        // Performance graphs content - Grid layout with summary
        Expanded(
          child: _isLoadingMetrics && _metrics == null
              ? const Center(child: CircularProgressIndicator())
              : _errorMessage != null
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.error_outline, color: Colors.red, size: 48),
                          const SizedBox(height: 16),
                          Text(
                            'Error loading metrics',
                            style: const TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            _errorMessage!,
                            style: const TextStyle(color: Colors.red),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    )
                  : _metrics == null || _metrics!.data.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.info_outline, color: Colors.grey, size: 48),
                              const SizedBox(height: 16),
                              const Text(
                                'No router metrics available',
                                style: TextStyle(
                                  fontSize: 16,
                                  fontStyle: FontStyle.italic,
                                ),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                'Waiting for data...',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: Colors.grey[600],
                                ),
                              ),
                            ],
                          ),
                        )
                      : RefreshIndicator(
                          onRefresh: _fetchMetrics,
                          child: LayoutBuilder(
                            builder: (context, constraints) {
                              // Calculate number of columns based on available width
                              final width = constraints.maxWidth;
                              int crossAxisCount = 3; // Default: 3 columns
                              
                              if (width < 800) {
                                crossAxisCount = 1; // Small screens: 1 column
                              } else if (width < 1200) {
                                crossAxisCount = 2; // Medium screens: 2 columns
                              }
                              
                              final keys = _metrics!.data.keys.toList();
                              keys.sort(); // Sort router names alphabetically
                              
                              return CustomScrollView(
                                slivers: [
                                  // Network Summary at the top
                                  SliverToBoxAdapter(
                                    child: _buildNetworkSummary(),
                                  ),
                                  // Router cards in grid
                                  SliverPadding(
                                    padding: const EdgeInsets.all(8.0),
                                    sliver: SliverGrid(
                                      gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                                        crossAxisCount: crossAxisCount,
                                        childAspectRatio: 1.2,
                                        crossAxisSpacing: 8,
                                        mainAxisSpacing: 8,
                                      ),
                                      delegate: SliverChildBuilderDelegate(
                                        (context, index) {
                                          final routerName = keys[index];
                                          final entries = _metrics!.data[routerName]!;
                                          return _buildRouterCard(routerName, entries);
                                        },
                                        childCount: keys.length,
                                      ),
                                    ),
                                  ),
                                ],
                              );
                            },
                          ),
                        ),
        ),
      ],
    );
  }
}

// Custom painter for circular gauge
class _CircularGaugePainter extends CustomPainter {
  final double fillPercentage;
  final Color gaugeColor;

  _CircularGaugePainter({
    required this.fillPercentage,
    required this.gaugeColor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2;
    const strokeWidth = 6.0;

    // Background circle (grey)
    final backgroundPaint = Paint()
      ..color = Colors.white.withOpacity(0.2)
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    canvas.drawCircle(center, radius - strokeWidth / 2, backgroundPaint);

    // Foreground arc (colored based on activity)
    if (fillPercentage > 0) {
      final foregroundPaint = Paint()
        ..shader = LinearGradient(
          colors: [
            gaugeColor.withOpacity(0.6),
            gaugeColor,
          ],
        ).createShader(Rect.fromCircle(center: center, radius: radius))
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round;

      // Draw arc from top (270 degrees) clockwise
      final sweepAngle = 2 * 3.14159 * fillPercentage;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius - strokeWidth / 2),
        -3.14159 / 2, // Start from top
        sweepAngle,
        false,
        foregroundPaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _CircularGaugePainter oldDelegate) {
    return oldDelegate.fillPercentage != fillPercentage ||
        oldDelegate.gaugeColor != gaugeColor;
  }
}
