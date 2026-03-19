import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:networkagent/models/agent.dart';
import 'package:networkagent/models/metrics.dart';
import 'package:networkagent/models/available_agent.dart';
import 'package:networkagent/models/incident.dart';
import 'package:networkagent/utils/environment_config.dart' as config;

class APIService{
  Map<String, String> getRequestHeaders = {
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'GET',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Map<String, String> postRequestHeaders = {
    'Content-type': 'application/json',
    'Accept': 'application/json',
    'Access-Control-Request-Method': 'POST',
    'Origin': '*',
    'Access-Control-Allow-Origin': '*'
  };

  Future<List<Agent>> listAgents() async {
    List<Agent> agents = [];
    try {
      var agent_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/listagents');
      print('Fetching agents from: $agent_url');
      
      try {
        final http.Response response = await http.get(agent_url, headers: getRequestHeaders);
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                print('Agent data: $agentData');
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Successfully added agent: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                    print('Agent data that failed: $agentData');
                  }
                } else {
                  print('Agent data is not a Map: ${agentData.runtimeType}');
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to load agents: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error fetching agents: $e');
    }
    
    print('Returning ${agents.length} agents');
    return agents;
  }
  
  Future<Agent?> addAgent(String url) async {
    try {
      var addUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/addagent');
      print('Adding agent with URL: $url');
      print('Add endpoint: $addUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          addUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final Map<String, dynamic> agentData = jsonDecode(response.body);
            print('Agent data: $agentData');
            
            final agent = Agent.fromJson(agentData);
            print('Successfully added agent: ${agent.name}');
            return agent;
          } catch (e) {
            print('Error parsing agent data: $e');
          }
        } else {
          print('Failed to add agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error adding agent: $e');
    }
    
    return null;
  }

  Future<List<Agent>> deleteAgent(String url) async {
    List<Agent> agents = [];
    try {
      var deleteUrl = Uri.parse('${config.EnvironmentConfig.agentUrl}/deleteagent');
      print('Deleting agent with URL: $url');
      print('Delete endpoint: $deleteUrl');
      
      // Create the request body
      Map<String, String> body = {'url': url};
      
      try {
        final http.Response response = await http.post(
          deleteUrl,
          headers: postRequestHeaders,
          body: jsonEncode(body)
        );
        print('Response status code: ${response.statusCode}');
        
        if (response.statusCode == 200) {
          print('Response body: ${response.body}');
          
          try {
            final dynamic decodedData = jsonDecode(response.body);
            print('Decoded data type: ${decodedData.runtimeType}');
            
            if (decodedData is List) {
              final List<dynamic> agentsList = decodedData;
              print('Updated agents list length: ${agentsList.length}');
              
              for (var agentData in agentsList) {
                if (agentData is Map<String, dynamic>) {
                  try {
                    final agent = Agent.fromJson(agentData);
                    agents.add(agent);
                    print('Agent in updated list: ${agent.name}');
                  } catch (e) {
                    print('Error parsing agent data: $e');
                  }
                }
              }
            } else {
              print('Decoded data is not a List: ${decodedData.runtimeType}');
            }
          } catch (e) {
            print('Error decoding JSON: $e');
          }
        } else {
          print('Failed to delete agent: ${response.statusCode}');
          print('Response body: ${response.body}');
        }
      } catch (e) {
        print('HTTP request error: $e');
      }
    } catch (e) {
      print('Error deleting agent: $e');
    }
    
    print('Returning ${agents.length} agents after deletion');
    return agents;
  }

  Future<String> getNodeDetails(String nodeId) async {
    try {
      var node_url = Uri.parse('${config.EnvironmentConfig.agentUrl}/node/$nodeId');
      print('Fetching node details from: $node_url');
      
      final http.Response response = await http.get(node_url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData.containsKey('summary')) {
          return decodedData['summary'];
        } else if (decodedData is String) {
          return decodedData;
        }
        else {
          throw Exception('Failed to parse node details summary');
        }
      } else {
        throw Exception('Failed to load node details');
      }
    } catch (e) {
      print('Error fetching node details: $e');
      rethrow;
    }
  }
  
  // Metrics API methods
  
  Future<Metrics> getAllLastMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/last');
      print('Fetching all last metrics from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all last metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all last metrics');
      }
    } catch (e) {
      print('Error fetching all last metrics: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getAllMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/all');
      print('Fetching all metrics from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all metrics');
      }
    } catch (e) {
      print('Error fetching all metrics: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getLastMetricsForId(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/last/$nodeId');
      print('Fetching last metrics for node $nodeId from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load last metrics for node $nodeId: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load last metrics for node $nodeId');
      }
    } catch (e) {
      print('Error fetching last metrics for node $nodeId: $e');
      rethrow;
    }
  }
  
  Future<Metrics> getAllMetricsForId(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/all/$nodeId');
      print('Fetching all metrics for node $nodeId from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return Metrics.fromJson(decodedData);
      } else {
        print('Failed to load all metrics for node $nodeId: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load all metrics for node $nodeId');
      }
    } catch (e) {
      print('Error fetching all metrics for node $nodeId: $e');
      rethrow;
    }
  }
  
  Future<bool> resetMetrics() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/metrics/reset');
      print('Resetting metrics at: $url');
      
      final http.Response response = await http.post(url, headers: postRequestHeaders);
      print('Reset metrics response status: ${response.statusCode}');
      print('Reset metrics response body: ${response.body}');
      
      if (response.statusCode == 200) {
        try {
          final dynamic decodedData = jsonDecode(response.body);
          print('Decoded reset metrics data: $decodedData');
          
          // Check if response has status field
          if (decodedData is Map<String, dynamic> && decodedData.containsKey('status')) {
            bool success = decodedData['status'] == 'success';
            print('Reset metrics success: $success');
            return success;
          } else {
            // If no status field, assume success if we got 200
            print('No status field in response, assuming success for 200 status code');
            return true;
          }
        } catch (jsonError) {
          print('Error parsing JSON response: $jsonError');
          // If JSON parsing fails but we got 200, assume success
          return true;
        }
      } else {
        print('Failed to reset metrics: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false; // Return false instead of throwing exception
      }
    } catch (e) {
      print('Error resetting metrics: $e');
      return false; // Return false instead of rethrowing
    }
  }
  
  Future<bool> deleteLogs() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/logs/delete');
      print('Deleting logs at: $url');
      
      final http.Response response = await http.post(url, headers: postRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        return decodedData['status'] == 'success';
      } else {
        print('Failed to delete logs: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to delete logs');
      }
    } catch (e) {
      print('Error deleting logs: $e');
      rethrow;
    }
  }

  Future<List<AvailableAgent>> getAvailableAgents() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/agents/available');
      print('Available network agents at: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final List<dynamic> decodedData = jsonDecode(response.body);
        List<AvailableAgent> availableAgents = [];
        for (var agentData in decodedData) {
          availableAgents.add(AvailableAgent.fromJson(agentData));
        }
        return availableAgents;
      } else {
        print('Failed to get available network agents: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to get available network agents');
      }
    } catch (e) {
      print('Error getting available agents: $e'); rethrow;
    }
  }

  Future<List<Incident>> getAllOpenIncidents() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/incidents');
      print('Fetching all open incidents from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        print('Raw incidents response body: ${response.body}');
        
        final List<dynamic> decodedData = jsonDecode(response.body);
        print('Decoded incidents data type: ${decodedData.runtimeType}');
        print('Number of incidents in response: ${decodedData.length}');
        
        List<Incident> incidents = [];
        for (int i = 0; i < decodedData.length; i++) {
          var incidentData = decodedData[i];
          print('Processing incident $i: $incidentData');
          
          if (incidentData is Map<String, dynamic>) {
            try {
              // Log the fields we're looking for
              print('Incident $i fields:');
              print('  - id: ${incidentData['id']}');
              print('  - issue: ${incidentData['issue']}');
              print('  - strategy: ${incidentData['strategy']}');
              print('  - rootCause: ${incidentData['rootCause']}');
              print('  - resolution: ${incidentData['resolution']}');
              print('  - recordedTimestamp: ${incidentData['recordedTimestamp']}');
              print('  - agentTaskId: ${incidentData['agentTaskId']}');
              print('  - lastProgressUpdate: ${incidentData['lastProgressUpdate']}');
              
              final incident = Incident.fromJson(incidentData);
              incidents.add(incident);
              
              print('Successfully parsed incident ${incident.id}:');
              print('  - Has strategy: ${incident.hasStrategy}');
              print('  - Has rootCause: ${incident.hasRootCause}');
              print('  - Has resolution: ${incident.hasResolution}');
              print('  - Progress stage: ${incident.progressStage}');
              print('  - Progress percentage: ${incident.progressPercentage}');
              
            } catch (e) {
              print('Error parsing incident data at index $i: $e');
              print('Incident data that failed: $incidentData');
            }
          } else {
            print('Incident data at index $i is not a Map: ${incidentData.runtimeType}');
          }
        }
        
        print('Successfully fetched and parsed ${incidents.length} incidents with complete progress data');
        return incidents;
      } else {
        print('Failed to get open incidents: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to get open incidents');
      }
    } catch (e) {
      print('Error getting open incidents: $e');
      rethrow;
    }
  }

  Future<bool> deleteAllIncidents() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/incidents/delete');
      print('Deleting all incidents at: $url');
      
      final http.Response response = await http.post(url, headers: postRequestHeaders);
      print('Delete incidents response status: ${response.statusCode}');
      print('Delete incidents response body: ${response.body}');
      
      if (response.statusCode == 200) {
        try {
          final dynamic decodedData = jsonDecode(response.body);
          print('Decoded delete incidents data: $decodedData');
          
          // Check if response has status field
          if (decodedData is Map<String, dynamic> && decodedData.containsKey('status')) {
            bool success = decodedData['status'] == 'success';
            print('Delete incidents success: $success');
            return success;
          } else {
            // If no status field, assume success if we got 200
            print('No status field in response, assuming success for 200 status code');
            return true;
          }
        } catch (jsonError) {
          print('Error parsing JSON response: $jsonError');
          // If JSON parsing fails but we got 200, assume success
          return true;
        }
      } else {
        print('Failed to delete incidents: ${response.statusCode}');
        print('Response body: ${response.body}');
        return false; // Return false instead of throwing exception
      }
    } catch (e) {
      print('Error deleting incidents: $e');
      return false; // Return false instead of rethrowing
    }
  }

  Future<Map<String, dynamic>> fetchPhysicalTopology({String? timestamp}) async {
    try {
      // Build URL with optional timestamp parameter
      String urlStr = '${config.EnvironmentConfig.agentUrl}/topology/physical';
      if (timestamp != null) {
        urlStr += '?timestamp=$timestamp';
      }
      var url = Uri.parse(urlStr);
      print('Fetching physical topology from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          print('Successfully fetched physical topology with ${decodedData['nodes']?.length ?? 0} nodes');
          // Embeddings are now included in the topology response
          return decodedData;
        } else {
          print('Physical topology data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid physical topology data format');
        }
      } else {
        print('Failed to load physical topology: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load physical topology');
      }
    } catch (e) {
      print('Error fetching physical topology: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchRouterDetails(String routerId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/router/$routerId');
      print('Fetching router details from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Router details data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid router details data format');
        }
      } else {
        print('Failed to load router details: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load router details');
      }
    } catch (e) {
      print('Error fetching router details: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchDeviceDetails(String deviceId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/device/$deviceId');
      print('Fetching device details from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Device details data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid device details data format');
        }
      } else {
        print('Failed to load device details: ${response.statusCode}');
        print('Response body: ${response.body}');
        throw Exception('Failed to load device details');
      }
    } catch (e) {
      print('Error fetching device details: $e');
      rethrow;
    }
  }

  Future<Map<String, dynamic>> fetchNodeEmbeddings(String nodeId) async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/embeddings/$nodeId');
      print('Fetching node embeddings from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic>) {
          return decodedData;
        } else {
          print('Node embeddings data is not a Map: ${decodedData.runtimeType}');
          throw Exception('Invalid node embeddings data format');
        }
      } else {
        print('Failed to load node embeddings: ${response.statusCode}');
        print('Response body: ${response.body}');
        // Return empty result instead of throwing
        return {'node_id': nodeId, 'router_embedding': null, 'interface_embeddings': []};
      }
    } catch (e) {
      print('Error fetching node embeddings: $e');
      // Return empty result instead of throwing
      return {'node_id': nodeId, 'router_embedding': null, 'interface_embeddings': []};
    }
  }

  // GNN / Anomaly API methods
  Future<List<String>> getSnapshots() async {
    try {
      var url = Uri.parse('${config.EnvironmentConfig.agentUrl}/snapshots');
      print('Fetching snapshots from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData['snapshots'] is List) {
          return List<String>.from(decodedData['snapshots']);
        }
        return [];
      } else {
        print('Failed to load snapshots: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching snapshots: $e');
      return [];
    }
  }

  Future<List<Map<String, dynamic>>> getAnomalies({String? timestamp}) async {
    try {
      String urlStr = '${config.EnvironmentConfig.agentUrl}/anomalies';
      if (timestamp != null) {
        urlStr += '?timestamp=$timestamp';
      }
      var url = Uri.parse(urlStr);
      print('Fetching anomalies from: $url');
      
      final http.Response response = await http.get(url, headers: getRequestHeaders);
      
      if (response.statusCode == 200) {
        final dynamic decodedData = jsonDecode(response.body);
        if (decodedData is Map<String, dynamic> && decodedData['anomalies'] is List) {
           return List<Map<String, dynamic>>.from(decodedData['anomalies']);
        }
        return [];
      } else {
        print('Failed to load anomalies: ${response.statusCode}');
        return [];
      }
    } catch (e) {
      print('Error fetching anomalies: $e');
      return [];
    }
  }
}
