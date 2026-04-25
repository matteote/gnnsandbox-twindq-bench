import 'package:flutter/material.dart';
import 'package:socket_io_client/socket_io_client.dart' as io;
import 'models/agent.dart';
import 'models/network_node.dart';
import 'models/log_entry.dart';
import 'models/metrics.dart';
import 'models/push_notification.dart';
import 'models/vpn_info.dart';
import 'models/vyos_infrastructure_info.dart';
import 'utils/environment_config.dart';
import 'utils/APIService.dart';
import 'dart:async';

enum TopologyViewType { map, logical }

// ---------------------------------------------------------------------------
// Deploy progress model
// ---------------------------------------------------------------------------

class DeployProgress {
  final String stage;
  final String? kind;
  final String? name;
  final String? networkId;
  final String? error;

  const DeployProgress({
    required this.stage,
    this.kind,
    this.name,
    this.networkId,
    this.error,
  });

  factory DeployProgress.fromJson(Map<String, dynamic> json) {
    return DeployProgress(
      stage:     json['stage']      as String? ?? '',
      kind:      json['kind']       as String?,
      name:      json['name']       as String?,
      networkId: json['network_id'] as String?,
      error:     json['error']      as String?,
    );
  }

  /// Human-readable status line for display in the UI.
  String get displayMessage {
    switch (stage) {
      case 'teardown_started':
        return 'Starting teardown of existing network…';
      case 'deleting':
        return 'Deleting $kind/${name ?? ''}…';
      case 'waiting':
        return 'Waiting for $kind/${name ?? ''} to be removed…';
      case 'deleted':
        return '✓ Removed $kind/${name ?? ''}';
      case 'teardown_complete':
        return '✓ All existing resources removed — applying new network…';
      case 'teardown_only_complete':
        return '✅ Network resources deleted';
      case 'teardown_failed':
        return '❌ Teardown failed: ${error ?? 'unknown error'}';
      case 'deploying':
        return 'Applying new network descriptor…';
      case 'deploy_complete':
        return '✅ Deploy complete';
      case 'deploy_failed':
        return '❌ Deploy failed: ${error ?? 'unknown error'}';
      default:
        return stage;
    }
  }

  /// Whether the deploy operation is still in progress.
  bool get isActive =>
      stage != 'deploy_complete' &&
      stage != 'deploy_failed' &&
      stage != 'teardown_only_complete' &&
      stage != 'teardown_failed';

  /// Whether the deploy is in the teardown phase.
  bool get isTearingDown =>
      stage == 'teardown_started' ||
      stage == 'deleting' ||
      stage == 'waiting' ||
      stage == 'deleted' ||
      stage == 'teardown_complete' ||
      stage == 'teardown_only_complete';

  /// Whether this represents a success or failure terminal state.
  bool get isTerminal =>
      stage == 'deploy_complete' ||
      stage == 'deploy_failed' ||
      stage == 'teardown_only_complete' ||
      stage == 'teardown_failed';
}

class Appstate extends ChangeNotifier {
  // Socket connection
  io.Socket? _socket;
  
  // API Service
  final APIService _apiService = APIService();
  
  // Agents state
  final List<Agent> _agents = [];
  
  // Network topology state
  NetworkTopology _topology = NetworkTopology.empty();
  TopologyViewType _currentTopologyView = TopologyViewType.logical;
  bool _isConnected = false;
  
  // Log widget state
  List<LogEntry> _logs = [];
  bool _isLoadingLogs = false;
  
  // Metrics state
  Metrics _metrics = Metrics({});
  bool _isLoadingMetrics = false;
  
  // Performance graph widget state
  bool _showPerformanceGraph = false;
  
  // Push notifications state
  final List<PushNotification> _pushNotifications = [];
  

  // Trace widget state
  final List<Map<String, dynamic>> _traceEvents = [];
  bool _isTracesEnabled = false;
  
  // Topology polling state
  Timer? _topologyRefreshTimer;
  bool _isLiveMode = true;
  String? _selectedTimestamp;
  List<String> _availableSnapshots = [];

  // Deploy progress state
  DeployProgress? _deployProgress;

  // VPN and traffic test state
  List<VpnInfo> _vpns = [];
  List<TrafficTestInfo> _trafficTests = [];
  Timer? _vpnRefreshTimer;

  // VPN delete state — name of the VPN currently being deleted (null = idle).
  String? _deletingVpnName;

  // TrafficTest delete state — names of tests whose K8s delete has been issued
  // but not yet confirmed gone. The periodic poll filters these out so they
  // don't flicker back into the UI while Kubernetes is terminating them.
  final Set<String> _deletingTestNames = {};

  // VyosInfrastructure state
  List<VyosInfrastructureInfo> _vyosInfrastructures = [];

  // Topology highlight state — node IDs to highlight
  Set<String> _highlightedNodeIds = {};
  String? _highlightedItemName;

  // Getters
  io.Socket? get socket => _socket;
  List<Agent> get agents => List.unmodifiable(_agents);
  NetworkTopology get topology => _topology;
  TopologyViewType get currentTopologyView => _currentTopologyView;
  bool get isConnected => _isConnected;
  List<LogEntry> get logs => _logs;
  bool get isLoadingLogs => _isLoadingLogs;
  Metrics get metrics => _metrics;
  bool get isLoadingMetrics => _isLoadingMetrics;
  bool get showPerformanceGraph => _showPerformanceGraph;
  List<PushNotification> get pushNotifications => List.unmodifiable(_pushNotifications);
  List<Map<String, dynamic>> get traceEvents => _traceEvents;
  bool get isLiveMode => _isLiveMode;
  List<String> get availableSnapshots => List.unmodifiable(_availableSnapshots);
  DeployProgress? get deployProgress => _deployProgress;

  // VPN / traffic test getters
  List<VpnInfo> get vpns => List.unmodifiable(_vpns);
  List<TrafficTestInfo> get trafficTests => List.unmodifiable(_trafficTests);

  /// Name of the VPN currently being deleted, or null when idle.
  String? get deletingVpnName => _deletingVpnName;

  /// Names of TrafficTests whose delete has been issued but not yet confirmed.
  Set<String> get deletingTestNames => Set.unmodifiable(_deletingTestNames);
  Set<String> get highlightedNodeIds => Set.unmodifiable(_highlightedNodeIds);
  String? get highlightedItemName => _highlightedItemName;

  // VyosInfrastructure getter
  List<VyosInfrastructureInfo> get vyosInfrastructures =>
      List.unmodifiable(_vyosInfrastructures);

  Appstate() {
    _connectToServer();
    _startLiveTopologyPolling();
    _loadAvailableSnapshots();
    _startVpnPolling();
  }
  
  // Load available snapshots from backend
  Future<void> _loadAvailableSnapshots() async {
    try {
      final snapshots = await _apiService.getSnapshots();
      _availableSnapshots = snapshots;
      print('Loaded ${snapshots.length} available snapshots');
      notifyListeners();
    } catch (e) {
      print('Error loading available snapshots: $e');
    }
  }
  
  // Set LIVE mode or historical mode
  void setLiveMode(bool isLive, {String? timestamp}) {
    _isLiveMode = isLive;
    _selectedTimestamp = timestamp;
    
    if (isLive) {
      print('Switching to LIVE mode - starting topology polling');
      _startLiveTopologyPolling();
    } else {
      print('Switching to historical mode at timestamp: $timestamp');
      _stopLiveTopologyPolling();
      _fetchTopologyAtTimestamp(timestamp);
    }
  }
  
  // Start LIVE mode topology polling
  void _startLiveTopologyPolling() {
    _stopLiveTopologyPolling(); // Clear any existing timer
    
    // Fetch immediately
    _fetchLiveTopology();
    _loadAvailableSnapshots(); // Refresh snapshots
    
    // Then poll every 10 seconds
    _topologyRefreshTimer = Timer.periodic(const Duration(seconds: 10), (_) {
      if (_isLiveMode) {
        _fetchLiveTopology();
        _loadAvailableSnapshots(); // Refresh snapshots to pick up new ones
      }
    });
  }
  
  // Stop LIVE mode topology polling
  void _stopLiveTopologyPolling() {
    _topologyRefreshTimer?.cancel();
    _topologyRefreshTimer = null;
  }
  
  // Fetch LIVE topology with latest embeddings
  Future<void> _fetchLiveTopology() async {
    try {
      final topologyData = await _apiService.fetchPhysicalTopology();
      _updatePhysicalTopologyWithEmbeddings(topologyData);
    } catch (e) {
      print('Error fetching LIVE topology: $e');
    }
  }
  
  // Fetch historical topology snapshot at a specific timestamp
  Future<void> _fetchTopologyAtTimestamp(String? timestamp) async {
    if (timestamp == null) {
      print('No timestamp provided for historical fetch');
      return;
    }
    
    try {
      final topologyData = await _apiService.fetchPhysicalTopology(timestamp: timestamp);
      _updatePhysicalTopologyWithEmbeddings(topologyData);
    } catch (e) {
      print('Error fetching historical topology: $e');
    }
  }

  // Connect to the server and initialize socket
  void _connectToServer() {
    // Connect to the NetworkAgent socket server
    _socket = io.io(EnvironmentConfig.agentUrl, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': true,
    });
    
    _socket!.onConnect((_) async {
      print('Connected to NetworkAgent server');
      _isConnected = true;
      
      // Request initial topology data when connected
      // _socket!.emit('get_topology', {'view': NetworkTopologyWidget.defaultView});
      _fetchPhysicalTopology();
      
      // Reset trace cursor to current time to avoid receiving old events
      final currentTimestamp = DateTime.now().toUtc().toIso8601String();
      _socket!.emit('reset_traces', {'timestamp': currentTimestamp});
      print('Reset trace cursor to current time: $currentTimestamp');
      
      // Re-enable traces if they were previously enabled
      if (_isTracesEnabled) {
        print('Re-enabling traces after reconnection');
        _socket!.emit('get_traces', {'enabled': true});
      }
      
      // Initialize the list of remote agents from REST API
      try {
        final agents = await _apiService.listAgents();
        if (agents.isNotEmpty) {
          _agents.clear();
          _agents.addAll(agents);
          print('Initialized ${agents.length} remote agents from REST API');
        }
      } catch (e) {
        print('Error initializing remote agents: $e');
      }
      
      notifyListeners();
    });
    
    _socket!.onDisconnect((_) {
      print('Disconnected from NetworkAgent server');
      _isConnected = false;
      
      // Reset remote agents when socket disconnects
      if (_agents.isNotEmpty) {
        print('Resetting remote agents due to socket disconnection');
        _agents.clear();
      }
      
      notifyListeners();
    });
    
    // Agent management has been moved to REST endpoints
    
    // Listen for topology updates
    _socket!.on('topology_update', (data) {
      if (data != null && data['elements'] != null) {
        print('Received topology update with ${data['elements'].length} elements');
        // _updateTopology(data['elements']);
        // Ignore socket topology updates for now as we are using physical topology from REST
      }
      
      // If logs are enabled and logs data is included, update logs
      if (data != null && data['logs'] != null) {
        _updateLogs(data['logs']);
      }
    });

    // Listen for log updates
    _socket!.on('logs_update', (data) {
      if (data != null) {
        _updateLogs(data);
      }
    });

    // Listen for metrics updates
    _socket!.on('metrics_update', (data) {
      if (data != null) {
        _updateMetrics(data);        
      }
    });
    
    // Listen for push notifications from supervisor agent
    _socket!.on('push_notification', (data) {
      if (data != null) {
        _addPushNotification(data);
      }
    });
    
    // Listen for trace updates
    _socket!.on('trace_update', (data) {
      if (data != null) {
        _addTraceEvent(data);
      }
    });

    // Listen for deploy progress events from the supervisor
    _socket!.on('deploy_progress', (data) {
      if (data != null) {
        _updateDeployProgress(data);
      }
    });

    // Listen for VPN delete progress events
    _socket!.on('vpn_delete_progress', (data) {
      if (data != null) {
        _handleVpnDeleteProgress(data);
      }
    });

    _socket!.connect();
  }
  
  // Set the socket connection (legacy method, kept for compatibility)
  void setSocket(io.Socket socket) {
    // This method is kept for backward compatibility but doesn't do anything
    // since the socket is now initialized in the constructor
  }
  
  // resetChat method removed - AG-UI chat panel manages thread IDs directly
  
  // Agent management methods
  Future<void> addAgent(String url) async {
    try {
      print('Adding agent with URL: $url');
      
      // Call the REST API to add the agent
      final agent = await _apiService.addAgent(url);
      
      if (agent != null) {
        // Add the new agent to the list
        _agents.add(agent);
        print('Successfully added agent: ${agent.name}');
        
        // Refresh the full list to ensure consistency
        final agents = await _apiService.listAgents();
        if (agents.isNotEmpty) {
          _agents.clear();
          _agents.addAll(agents);
          print('Updated agents list with ${agents.length} agents');
        }
        
        // Notify listeners about the state change
        notifyListeners();
      } else {
        print('Failed to add agent with URL: $url');
      }
    } catch (e) {
      print('Error adding agent: $e');
    }
  }
  
  Future<void> removeAgent(String id) async {
    try {
      // Find the agent to get its URL
      Agent? agentToRemove = _agents.firstWhere((a) => a.id == id);
      print('Removing agent with ID: $id, URL: ${agentToRemove.url}');
      
      // Call the REST API to delete the agent
      final updatedAgents = await _apiService.deleteAgent(agentToRemove.url);
      
      // Update the local agents list
      _agents.clear();
      _agents.addAll(updatedAgents);
      print('Updated agents list with ${updatedAgents.length} agents');
      
      // Notify listeners about the state change
      notifyListeners();
    } catch (e) {
      print('Error removing agent: $e');
    }
  }

  // Fetch physical topology from REST API
  Future<void> _fetchPhysicalTopology() async {
    try {
      print('Fetching physical topology from REST API...');
      
      // Fetch physical topology data from the API
      final topologyData = await _apiService.fetchPhysicalTopology();
      
      // Update the topology state with the fetched data
      _updatePhysicalTopology(topologyData);
      
      print('Successfully fetched and updated physical topology');
      
    } catch (e) {
      print('Error fetching physical topology: $e');
    }
  }
  
  // Update topology from physical topology data (legacy, without embeddings)
  void _updatePhysicalTopology(Map<String, dynamic> data) {
    _updatePhysicalTopologyWithEmbeddings(data);
  }
  
  // Update topology from physical topology data WITH embeddings
  void _updatePhysicalTopologyWithEmbeddings(Map<String, dynamic> data) {
    try {
      final nodesData = data['nodes'] as List<dynamic>? ?? [];
      final connectionsData = data['connections'] as List<dynamic>? ?? [];
      
      final nodes = <NetworkNode>[];
      final connections = <NetworkConnection>[];
      final nodeIds = <String>{};
      
      for (var nodeData in nodesData) {
        final id = nodeData['id'];
        final name = nodeData['name'] ?? 'Unknown';
        final role = nodeData['role'] ?? 'unknown';
        final status = nodeData['status'] ?? 'unknown';
        final location = nodeData['location'];
        
        // Extract embeddings data (all 3 GNN models)
        final double? stgnnScore = nodeData['stgnn_score'] != null
            ? (nodeData['stgnn_score'] as num).toDouble()
            : null;
        final double? dgatScore = nodeData['dgat_score'] != null
            ? (nodeData['dgat_score'] as num).toDouble()
            : null;
        final double? hetgnnScore = nodeData['hetgnn_score'] != null
            ? (nodeData['hetgnn_score'] as num).toDouble()
            : null;
        final routerRCA = nodeData['router_rca'];
        final embeddingTimestamp = nodeData['embedding_timestamp'];
        
        // Parse interface MSEs (now contains all 3 model scores)
        Map<String, Map<String, double>>? interfaceMSEs;
        if (nodeData['interface_mses'] != null) {
          interfaceMSEs = {};
          final interfaceMSEsData = nodeData['interface_mses'] as Map<String, dynamic>;
          for (var entry in interfaceMSEsData.entries) {
            final scores = entry.value as Map<String, dynamic>;
            interfaceMSEs[entry.key] = {
              'stgnn_score': (scores['stgnn_score'] as num?)?.toDouble() ?? 0.0,
              'dgat_score': (scores['dgat_score'] as num?)?.toDouble() ?? 0.0,
              'hetgnn_score': (scores['hetgnn_score'] as num?)?.toDouble() ?? 0.0,
            };
          }
        }
        
        // Determine node type - check for device type first
        NodeType type = NodeType.P;  // Default to P router
        final nodeType = nodeData['type']?.toString().toLowerCase();
        
        if (nodeType == 'device') {
          type = NodeType.Device;
        } else {
          // Physical topology typically consists of routers (default) and switches
          final roleStr = role.toString().toLowerCase();
          
          if (roleStr == 'pe' || roleStr == 'provider_edge') {
            type = NodeType.PE;
          } else if (roleStr == 'ce' || roleStr == 'customer_edge') {
            type = NodeType.CE;
          }
        }
        
        nodes.add(NetworkNode(
          id: id,
          name: name,
          type: type,
          properties: {
            'kind': nodeType == 'device' ? 'Device' : 'Router',
            'role': role,
            'status': status,
            'location': location,
            'interfaces': nodeData['interfaces'],
            'router_id': nodeData['router_id'],  // For devices
            'network_name': nodeData['network_name'],  // For devices
            'ip_address': nodeData['ip_address'],  // For devices
            'gateway': nodeData['gateway'],  // For devices
            'vlan': nodeData['vlan'],  // For devices
          },
          stgnnScore: stgnnScore,
          dgatScore: dgatScore,
          hetgnnScore: hetgnnScore,
          interfaceMSEs: interfaceMSEs,
          routerRCA: routerRCA,
          embeddingTimestamp: embeddingTimestamp,
        ));
        
        nodeIds.add(id);
      }
      
      for (var connData in connectionsData) {
        final id = connData['id'];
        final connType = connData['type'] ?? '';
        String? sourceId;
        String? targetId;
        
        // Handle both router-to-router and device-to-router/interface connections
        if (connType == 'device_to_router' || connType == 'device_to_interface') {
          sourceId = connData['source_device_id'];
          targetId = connData['target_router_id'];
        } else {
          sourceId = connData['source_router_id'];
          targetId = connData['target_router_id'];
        }
        
        final name = connData['name'] ?? '';
        
        if (sourceId != null && targetId != null && 
            nodeIds.contains(sourceId) && nodeIds.contains(targetId)) {
          connections.add(NetworkConnection(
            id: id,
            sourceId: sourceId,
            targetId: targetId,
            label: name,
            properties: {'type': connType},
          ));
        }
      }
      
      _topology = NetworkTopology(nodes: nodes, connections: connections);
      
      // Log embedding stats (check any of the 3 models)
      final nodesWithMSE = nodes.where((n) => 
        n.stgnnScore != null || n.dgatScore != null || n.hetgnnScore != null
      ).length;
      final nodesWithHighMSE = nodes.where((n) => n.hasHighMSE).length;
      print('Updated topology with ${nodes.length} nodes and ${connections.length} connections');
      print('Embeddings: $nodesWithMSE nodes have MSE data, $nodesWithHighMSE have high MSE');
      
      notifyListeners();
      
    } catch (e) {
      print('Error updating physical topology with embeddings: $e');
    }
  }
  
  // Toggle topology view
  void toggleTopologyView() {
    _currentTopologyView = _currentTopologyView == TopologyViewType.map
        ? TopologyViewType.logical
        : TopologyViewType.map;
    notifyListeners();
  }
  
  // Update logs from server data
  void _updateLogs(dynamic logsData) {
    try {
      List<LogEntry> newLogs = [];
      
      if (logsData is List) {
        // Convert each log entry from JSON to LogEntry object
        newLogs = logsData.map((logData) => 
          logData is Map<String, dynamic> 
            ? LogEntry.fromJson(logData)
            : LogEntry(
                timestamp: DateTime.now().toIso8601String(),
                severity: 'INFO',
                message: logData.toString(),
                source: 'unknown',
              )
        ).toList();
      } else if (logsData != null) {
        // Handle any unexpected format
        print('Unexpected log data format: ${logsData.runtimeType}');
        newLogs.add(LogEntry(
          timestamp: DateTime.now().toIso8601String(),
          severity: 'WARNING',
          message: 'Received logs in unexpected format: ${logsData.runtimeType}',
          source: 'dashboard',
        ));
      }
      
      _logs = newLogs;
      _isLoadingLogs = false;
      notifyListeners();
    } catch (e) {
      print('Error updating logs: $e');
      _isLoadingLogs = false;
      notifyListeners();
    }
  }
  
  // Toggle logs visibility
  void toggleLogs(bool showLogs) {
    _isLoadingLogs = showLogs;
    
    if (_socket != null && _socket!.connected) {
      if (showLogs) {
        // Request logs from server
        _socket!.emit('get_logs', {'enabled': true});
      } else {
        // Notify server to stop sending logs
        _socket!.emit('get_logs', {'enabled': false});
      }
    }
    
    notifyListeners();
  }
  
  // Reset logs
  void resetLogs() {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_logs');
    }
  }
  
  // Add a trace event from server data
  void _addTraceEvent(dynamic data) {
    try {
      if (data is Map<String, dynamic>) {
        // Create a new list to ensure the UI rebuilds correctly
        final updatedTraceEvents = List<Map<String, dynamic>>.from(_traceEvents);
        updatedTraceEvents.add(data);
        _traceEvents.clear();
        _traceEvents.addAll(updatedTraceEvents);
        
        print('Received trace event: ${data['event_type']} - ${data['operation_name']}');
        notifyListeners();
      }
    } catch (e) {
      print('Error adding trace event: $e');
    }
  }
  
  // Toggle traces visibility
  void toggleTraces(bool showTraces) {
    _isTracesEnabled = showTraces;
    
    if (_socket != null && _socket!.connected) {
      if (showTraces) {
        // Request traces from server
        _socket!.emit('get_traces', {'enabled': true});
        print('Enabled trace streaming from server');
      } else {
        // Notify server to stop sending traces
        _socket!.emit('get_traces', {'enabled': false});
        print('Disabled trace streaming from server');
      }
    }
    
    notifyListeners();
  }
  
  // Clear all trace events
  void clearTraces() {
    _traceEvents.clear();
    print('Cleared all trace events');
    
    // Get the current timestamp in UTC and send it to the backend
    final timestamp = DateTime.now().toUtc().toIso8601String();
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_traces', {'timestamp': timestamp});
      print('Requested backend to reset trace cursor to $timestamp');
    }
    
    notifyListeners();
  }
  
  // Update metrics from server data
  void _updateMetrics(dynamic metricsData) {
    try {
      // Use the Metrics class to parse the metrics data
      final metrics = Metrics.fromJson(metricsData);
      
      _metrics = metrics;
      _isLoadingMetrics = false;
      notifyListeners();
    } catch (e) {
      print('Error updating metrics: $e');
      _isLoadingMetrics = false;
      notifyListeners();
    }
  }
  
  // Reset metrics
  void resetMetrics() {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('reset_metrics');
    }
  }
  
  // Toggle performance graph visibility
  void togglePerformanceGraph() {
    _showPerformanceGraph = !_showPerformanceGraph;
    notifyListeners();
  }
  
  // Get node details
  void getNodeDetails(String nodeId) {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_node_details', {'id': nodeId});
    }
  }
  
  // Get topology view
  void getTopologyView(String view) {
    if (_socket != null && _socket!.connected) {
      _socket!.emit('get_topology', {'view': view});
    }
  }
  
  // Add a push notification
  void _addPushNotification(dynamic data) {
    try {
      final notification = PushNotification.fromJson(data);
      if (notification.state == 'input_required') {
        _pushNotifications.add(notification);
        notifyListeners();
      } else {
        // For any other notification type, just notify listeners
        notifyListeners();
      }
    } catch (e) {
      print('Error adding push notification: $e');
    }
  }
  
  // Mark a push notification as read
  void markNotificationAsRead(String id) {
    final index = _pushNotifications.indexWhere((notification) => notification.id == id);
    if (index != -1) {
      final notification = _pushNotifications[index];
      final updatedNotification = notification.copyWith(isRead: true);
      _pushNotifications[index] = updatedNotification;
      notifyListeners();
    }
  }
  
  // Clear all push notifications
  void clearAllNotifications() {
    _pushNotifications.clear();
    notifyListeners();
  }
  
  // Remove a specific notification by ID
  void removeNotification(String id) {
    final index = _pushNotifications.indexWhere((notification) => notification.id == id);
    if (index != -1) {
      _pushNotifications.removeAt(index);
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------------------
  // Deploy progress
  // ---------------------------------------------------------------------------

  /// Handle an incoming ``deploy_progress`` Socket.IO event.
  void _updateDeployProgress(dynamic data) {
    try {
      if (data is Map<String, dynamic>) {
        _deployProgress = DeployProgress.fromJson(data);
        print('Deploy progress: ${_deployProgress!.stage} '
            '${_deployProgress!.kind != null ? '(${_deployProgress!.kind}/${_deployProgress!.name})' : ''}');
        notifyListeners();
      }
    } catch (e) {
      print('Error updating deploy progress: $e');
    }
  }

  /// Clear the deploy progress state (e.g. when the user dismisses the banner).
  void clearDeployProgress() {
    _deployProgress = null;
    notifyListeners();
  }

  // ---------------------------------------------------------------------------
  // VPN and traffic test refresh
  // ---------------------------------------------------------------------------

  void _startVpnPolling() {
    _stopVpnPolling();
    _refreshVpnsAndTests();
    _vpnRefreshTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      _refreshVpnsAndTests();
    });
  }

  void _stopVpnPolling() {
    _vpnRefreshTimer?.cancel();
    _vpnRefreshTimer = null;
  }

  Future<void> _refreshVpnsAndTests() async {
    try {
      final vpns = await _apiService.fetchVpns();
      final tests = await _apiService.fetchTrafficTests();
      final infras = await _apiService.fetchVyosInfrastructure();
      _vpns = vpns;

      // Determine which previously-deleting tests are now truly gone from K8s.
      // Those can be removed from the pending set.
      if (_deletingTestNames.isNotEmpty) {
        final freshNames = tests.map((t) => t.name).toSet();
        _deletingTestNames.removeWhere((name) => !freshNames.contains(name));
        // Filter out any tests still in the deleting set (K8s hasn't confirmed
        // their removal yet — don't let them flicker back into the UI).
        _trafficTests = tests
            .where((t) => !_deletingTestNames.contains(t.name))
            .toList();
      } else {
        _trafficTests = tests;
      }

      _vyosInfrastructures = infras;
      notifyListeners();
    } catch (e) {
      print('Error refreshing VPNs/TrafficTests/Infrastructure: $e');
    }
  }

  /// Manually trigger a refresh of VPN and traffic test data.
  Future<void> refreshVpnsAndTests() async {
    await _refreshVpnsAndTests();
  }

  // ---------------------------------------------------------------------------
  // VPN and traffic test delete
  // ---------------------------------------------------------------------------

  /// Delete a single TrafficTest by name.
  ///
  /// Immediately removes the test from the local list and adds its name to
  /// [_deletingTestNames] so that background polls don't bring it back while
  /// Kubernetes is still terminating it.  The name is removed from the set
  /// automatically once [_refreshVpnsAndTests] confirms it is gone from K8s.
  Future<bool> deleteTrafficTest(String name) async {
    // Optimistically hide the test immediately.
    _deletingTestNames.add(name);
    _trafficTests = _trafficTests.where((t) => t.name != name).toList();
    notifyListeners();

    final success = await _apiService.deleteTrafficTest(name);
    if (!success) {
      // Delete failed — restore the test in the UI.
      _deletingTestNames.remove(name);
      await _refreshVpnsAndTests();
    }
    // On success we intentionally do NOT immediately refresh: the next
    // periodic poll will confirm deletion and clean up _deletingTestNames.
    return success;
  }

  /// Delete a VPN and all its linked TrafficTests.
  ///
  /// Returns false if a VPN delete is already in progress (409 from server).
  /// Progress is streamed via [vpn_delete_progress] Socket.IO events which
  /// are handled in [_handleVpnDeleteProgress].
  Future<bool> deleteVpn(String name) async {
    final accepted = await _apiService.deleteVpn(name);
    if (accepted) {
      _deletingVpnName = name;
      notifyListeners();
    }
    return accepted;
  }

  /// Handle incoming ``vpn_delete_progress`` Socket.IO events.
  void _handleVpnDeleteProgress(dynamic data) {
    try {
      if (data is! Map<String, dynamic>) return;
      final stage   = data['stage']    as String? ?? '';
      final vpnName = data['vpn_name'] as String? ?? '';

      print('VPN delete progress: stage=$stage vpn=$vpnName');

      if (stage == 'complete' || stage == 'failed') {
        _deletingVpnName = null;
        notifyListeners();
        // Refresh list so deleted resources disappear.
        _refreshVpnsAndTests();
      } else {
        // Update the deleting name in case it changed (shouldn't normally).
        if (_deletingVpnName != vpnName && vpnName.isNotEmpty) {
          _deletingVpnName = vpnName;
          notifyListeners();
        }
      }
    } catch (e) {
      print('Error handling vpn_delete_progress: $e');
    }
  }

  // ---------------------------------------------------------------------------
  // Topology highlight (VPN / traffic test click)
  // ---------------------------------------------------------------------------

  /// Highlight topology nodes associated with a VPN (by router name).
  void highlightVpnNodes(VpnInfo vpn) {
    final routerNames = Set<String>.from(vpn.routers);
    _highlightedNodeIds = _topology.nodes
        .where((n) => routerNames.contains(n.name))
        .map((n) => n.id)
        .toSet();
    _highlightedItemName = vpn.name;
    notifyListeners();
  }

  /// Highlight topology nodes associated with a TrafficTest (by device name).
  void highlightTrafficTestNodes(TrafficTestInfo test) {
    final deviceNames = Set<String>.from(test.allDeviceNames);
    _highlightedNodeIds = _topology.nodes
        .where((n) => deviceNames.contains(n.name))
        .map((n) => n.id)
        .toSet();
    _highlightedItemName = test.name;
    notifyListeners();
  }

  /// Clear any topology highlight.
  void clearHighlight() {
    _highlightedNodeIds = {};
    _highlightedItemName = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _topologyRefreshTimer?.cancel();
    _vpnRefreshTimer?.cancel();
    
    // Remove event listeners to prevent memory leaks
    if (_socket != null) {
      // Agent management has been moved to REST endpoints
      _socket!.off('topology_update');
      _socket!.off('logs_update');
      _socket!.off('all_last_metrics_update');
      _socket!.off('push_notification');
      _socket!.off('trace_update');
      _socket!.off('deploy_progress');
      _socket!.disconnect();
    }
    super.dispose();
  }
}
