import 'metric_entry.dart';

/// A comprehensive class to store metrics data including node metrics, users, and service performance.
// Example of the new Metrics structure:
// {
//   "node_metrics": {
//     "eac8bfd1-f7f5-4a23-98ee-791eaa0d1c80": [
//       List of MetricEntry here (last or all)
//     ],
//     "fe07d625-327c-4b07-bbaf-d635c4c5fee2": [
//       List of MetricEntry here (last or all)
//     ]
//   },
//   "users": {
//     "active_user_sessions": 5,
//     "time_window_seconds": 20,
//     "query_timestamp": timestamp,
//     "threshold_timestamp": timestamp
//   },
//   "service_performance": {
//     "service_performance": {
//       "broadband": {
//         "avg_response_time_ms": 150.5,
//         "total_requests": 100,
//         "error_count": 2,
//         "error_rate": 2.0,
//         "unique_users": 5,
//         "unique_nodes": 3
//       },
//       "video": { ... },
//       "voice": { ... }
//     },
//     "time_window_seconds": 20,
//     "query_timestamp": timestamp,
//     "threshold_timestamp": timestamp,
//     "total_service_types": 3
//   }
// }

class Metrics {
  /// The node metrics data, keyed by node ID.
  final Map<String, List<MetricEntry>> data;
  
  /// The users data, containing active user session information.
  final Map<String, dynamic> users;
  
  /// The service performance data, containing performance metrics by service type.
  final Map<String, dynamic> servicePerformance;

  /// Creates a new Metrics instance with the provided data.
  Metrics(this.data, {this.users = const {}, this.servicePerformance = const {}});

  /// Creates a new Metrics instance from the provided metrics data.
  factory Metrics.fromJson(dynamic metricsData) {
    Map<String, List<MetricEntry>> parsedData = {};
    Map<String, dynamic> users = {};
    Map<String, dynamic> servicePerformance = {};
    
    if (metricsData is Map) {
      // Handle new structure with node_metrics, users, and service_performance
      if (metricsData.containsKey('node_metrics')) {
        final nodeMetrics = metricsData['node_metrics'];
        if (nodeMetrics is Map) {
          nodeMetrics.forEach((id, metricsList) {
            if (metricsList is List) {
              parsedData[id.toString()] = metricsList.map((metricItem) {
                if (metricItem is Map && metricItem.containsKey('metrics')) {
                  final metricsData = metricItem['metrics'];
                  if (metricsData is Map) {
                    final typedMetricsData = Map<String, dynamic>.from(metricsData);
                    typedMetricsData['timestamp'] = metricItem['timestamp'] ?? 0;
                    return MetricEntry.fromJson(typedMetricsData);
                  }
                }
                return MetricEntry(
                  hostname: '', 
                  interval: 0, 
                  cpu: {}, 
                  interfaces: {}, 
                  timestamp: metricItem is Map ? metricItem['timestamp'] ?? 0 : 0
                );
              }).toList();
            } else {
              parsedData[id.toString()] = [
                MetricEntry(hostname: '', interval: 0, cpu: {}, interfaces: {}, timestamp: 0)
              ];
            }
          });
        }
        
        // Parse users data
        if (metricsData.containsKey('users') && metricsData['users'] is Map) {
          users = Map<String, dynamic>.from(metricsData['users']);
        }
        
        // Parse service performance data
        if (metricsData.containsKey('service_performance') && metricsData['service_performance'] is Map) {
          servicePerformance = Map<String, dynamic>.from(metricsData['service_performance']);
        }
      } else {
        // Handle legacy structure for backward compatibility
        // This handles the case where the data is directly the node metrics without the 'node_metrics' wrapper
        metricsData.forEach((id, metricsList) {
          if (metricsList is List) {
            parsedData[id.toString()] = metricsList.map((metricItem) {
              if (metricItem is Map && metricItem.containsKey('metrics')) {
                final metricsData = metricItem['metrics'];
                if (metricsData is Map) {
                  final typedMetricsData = Map<String, dynamic>.from(metricsData);
                  typedMetricsData['timestamp'] = metricItem['timestamp'] ?? 0;
                  return MetricEntry.fromJson(typedMetricsData);
                }
              }
              return MetricEntry(
                hostname: '', 
                interval: 0, 
                cpu: {}, 
                interfaces: {}, 
                timestamp: metricItem is Map ? metricItem['timestamp'] ?? 0 : 0
              );
            }).toList();
          } else {
            parsedData[id.toString()] = [
              MetricEntry(hostname: '', interval: 0, cpu: {}, interfaces: {}, timestamp: 0)
            ];
          }
        });
      }
    }
    
    return Metrics(parsedData, users: users, servicePerformance: servicePerformance);
  }
  
  /// Get the number of active users
  int get activeUserCount => users['active_user_sessions'] ?? 0;
  
  /// Calculate average response time from service performance data
  double get averageResponseTime {
    if (servicePerformance.isEmpty || 
        !servicePerformance.containsKey('service_performance')) return 0.0;
    
    final services = servicePerformance['service_performance'];
    if (services is! Map) return 0.0;
    
    double totalResponseTime = 0.0;
    int serviceCount = 0;
    
    services.forEach((serviceType, serviceData) {
      if (serviceData is Map && serviceData.containsKey('avg_response_time_ms')) {
        final avgResponseTime = serviceData['avg_response_time_ms'];
        if (avgResponseTime is num) {
          totalResponseTime += avgResponseTime.toDouble();
          serviceCount++;
        }
      }
    });
    
    return serviceCount > 0 ? totalResponseTime / serviceCount : 0.0;
  }
  
  /// Get service performance data for a specific service type
  Map<String, dynamic>? getServicePerformance(String serviceType) {
    if (servicePerformance.isEmpty || 
        !servicePerformance.containsKey('service_performance')) return null;
    
    final services = servicePerformance['service_performance'];
    if (services is! Map) return null;
    
    return services[serviceType] as Map<String, dynamic>?;
  }
  
  /// Get all available service types
  List<String> get availableServiceTypes {
    if (servicePerformance.isEmpty || 
        !servicePerformance.containsKey('service_performance')) return [];
    
    final services = servicePerformance['service_performance'];
    if (services is! Map) return [];
    
    return services.keys.cast<String>().toList();
  }
  
  /// Get total error rate across all services
  double get totalErrorRate {
    if (servicePerformance.isEmpty || 
        !servicePerformance.containsKey('service_performance')) return 0.0;
    
    final services = servicePerformance['service_performance'];
    if (services is! Map) return 0.0;
    
    double totalErrors = 0.0;
    double totalRequests = 0.0;
    
    services.forEach((serviceType, serviceData) {
      if (serviceData is Map) {
        final errorCount = (serviceData['error_count'] as num?)?.toDouble() ?? 0.0;
        final requestCount = (serviceData['total_requests'] as num?)?.toDouble() ?? 0.0;
        totalErrors += errorCount;
        totalRequests += requestCount;
      }
    });
    
    return totalRequests > 0 ? (totalErrors / totalRequests) * 100 : 0.0;
  }
}
