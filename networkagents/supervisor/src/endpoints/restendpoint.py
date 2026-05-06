# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import asyncio
import logging
import json
import time
from aiohttp_cors.cors_config import CorsConfig
import aiohttp_cors
from aiohttp import web
from agent.host_agent import HostAgent
from endpoints.socketendpoint import SocketEndpoint
from tools.topology import fetch_physical_topology, fetch_router_details, fetch_device_details, fetch_node_embeddings, fetch_snapshots, fetch_anomalies, fetch_vpns, fetch_traffic_tests, fetch_vyos_infrastructure, fetch_infrastructure_state, clear_topology, delete_traffic_test_crd
from tools.metrics import (
    fetch_all_last_metrics,
    fetch_all_metrics,
    fetch_last_metrics_for_id,
    fetch_all_metrics_for_id,
    clear_network_metrics,
    fetch_traffic_test_metrics,
    fetch_routing_metrics,
)
from tools.agents import get_available_agents
from tools.logs import delete_logs
from tools.networkdescriptors import (
    list_network_descriptors,
    get_network_descriptor_summary,
    get_descriptor_for_deploy,
    apply_crds_background,
    teardown_and_deploy_background,
    deploy_to_git_background,
    teardown_only_background,
    delete_vpn_with_tests_background,
    get_vpn_delete_status,
)

logger = logging.getLogger(__name__)

class RestEndpoint:

    _instance = None

    def __init__(self, app: web.Application, cors: CorsConfig):
        logger.info("RestEndpoint init")

        RestEndpoint._instance = self

        self.app = app
        self.cors = cors
        corsConfig = {
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        }

        addAgentRoute = self.app.router.add_post("/addagent", self.addAgent)
        self.cors.add(addAgentRoute, corsConfig)

        listAgentsRoute = self.app.router.add_get("/listagents", self.listAgents)
        self.cors.add(listAgentsRoute, corsConfig)

        deleteAgentRoute = self.app.router.add_post("/deleteagent", self.deleteAgent)
        self.cors.add(deleteAgentRoute, corsConfig)

        pushNotificationRoute = self.app.router.add_post("/pushnotification", self.pushNotification)
        self.cors.add(pushNotificationRoute, corsConfig)

        # Add metric-related REST endpoints
        getAllLastMetricsRoute = self.app.router.add_get("/metrics/last", self.getAllLastMetrics)
        self.cors.add(getAllLastMetricsRoute, corsConfig)

        getAllMetricsRoute = self.app.router.add_get("/metrics/all", self.getAllMetrics)
        self.cors.add(getAllMetricsRoute, corsConfig)

        getLastMetricsForIdRoute = self.app.router.add_get("/metrics/last/{node_id}", self.getLastMetricsForId)
        self.cors.add(getLastMetricsForIdRoute, corsConfig)

        getAllMetricsForIdRoute = self.app.router.add_get("/metrics/all/{node_id}", self.getAllMetricsForId)
        self.cors.add(getAllMetricsForIdRoute, corsConfig)

        resetMetricsRoute = self.app.router.add_post("/metrics/reset", self.resetMetrics)
        self.cors.add(resetMetricsRoute, corsConfig)

        getRoutingMetricsRoute = self.app.router.add_get("/metrics/routing/{node_id}", self.getRoutingMetrics)
        self.cors.add(getRoutingMetricsRoute, corsConfig)

        deleteLogsRoute = self.app.router.add_post("/logs/delete", self.deleteLogs)
        self.cors.add(deleteLogsRoute, corsConfig)

        resetTopologyRoute = self.app.router.add_post("/topology/reset", self.resetTopology)
        self.cors.add(resetTopologyRoute, corsConfig)

        getAvailableAgentsRoute = self.app.router.add_get("/agents/available", self.getAvailableAgents)
        self.cors.add(getAvailableAgentsRoute, corsConfig)

        # Add topology endpoints
        getPhysicalTopologyRoute = self.app.router.add_get("/topology/physical", self.getPhysicalTopology)
        self.cors.add(getPhysicalTopologyRoute, corsConfig)

        getRouterDetailsRoute = self.app.router.add_get("/router/{router_id}", self.getRouterDetails)
        self.cors.add(getRouterDetailsRoute, corsConfig)

        getDeviceDetailsRoute = self.app.router.add_get("/device/{device_id}", self.getDeviceDetails)
        self.cors.add(getDeviceDetailsRoute, corsConfig)

        getSnapshotsRoute = self.app.router.add_get("/snapshots", self.getSnapshots)
        self.cors.add(getSnapshotsRoute, corsConfig)

        getAnomaliesRoute = self.app.router.add_get("/anomalies", self.getAnomalies)
        self.cors.add(getAnomaliesRoute, corsConfig)

        getNodeEmbeddingsRoute = self.app.router.add_get("/embeddings/{node_id}", self.getNodeEmbeddings)
        self.cors.add(getNodeEmbeddingsRoute, corsConfig)

        # VPN and TrafficTest endpoints
        getVpnsRoute = self.app.router.add_get("/vpns", self.getVpns)
        self.cors.add(getVpnsRoute, corsConfig)

        deleteVpnRoute = self.app.router.add_post("/vpns/{name}/delete", self.deleteVpn)
        self.cors.add(deleteVpnRoute, corsConfig)

        getVpnDeleteStatusRoute = self.app.router.add_get("/vpns/delete/status", self.getVpnDeleteStatus)
        self.cors.add(getVpnDeleteStatusRoute, corsConfig)

        getTrafficTestsRoute = self.app.router.add_get("/traffictests", self.getTrafficTests)
        self.cors.add(getTrafficTestsRoute, corsConfig)

        deleteTrafficTestRoute = self.app.router.add_post("/traffictests/{name}/delete", self.deleteTrafficTest)
        self.cors.add(deleteTrafficTestRoute, corsConfig)

        getTrafficTestMetricsRoute = self.app.router.add_get("/traffictests/{name}/metrics", self.getTrafficTestMetrics)
        self.cors.add(getTrafficTestMetricsRoute, corsConfig)

        # VyosInfrastructure endpoint
        getInfrastructureRoute = self.app.router.add_get("/infrastructure", self.getVyosInfrastructure)
        self.cors.add(getInfrastructureRoute, corsConfig)

        # Combined infrastructure state endpoint (vpns + traffic_tests + infrastructure in one call)
        getInfrastructureStateRoute = self.app.router.add_get("/infrastructure/state", self.getInfrastructureState)
        self.cors.add(getInfrastructureStateRoute, corsConfig)

        # Network descriptor endpoints
        listNetworksRoute = self.app.router.add_get("/networks", self.listNetworkDescriptors)
        self.cors.add(listNetworksRoute, corsConfig)

        getNetworkRoute = self.app.router.add_get("/networks/{network_id}", self.getNetworkDescriptor)
        self.cors.add(getNetworkRoute, corsConfig)

        deployNetworkRoute = self.app.router.add_post("/networks/{network_id}/deploy", self.deployNetworkDescriptor)
        self.cors.add(deployNetworkRoute, corsConfig)

        teardownNetworkRoute = self.app.router.add_post("/networks/teardown", self.teardownDeployment)
        self.cors.add(teardownNetworkRoute, corsConfig)

    #################################################################
    # Topology endpoints
    #################################################################
    async def getPhysicalTopology(self, request):
        """
        Get the physical network topology including routers, their interfaces, 
        links, connectivity, and embeddings data (always included).
        
        Query Parameters:
            timestamp: Optional ISO-8601 timestamp for historical snapshot
        
        Returns:
            aiohttp.web.Response: JSON response with physical topology data including embeddings
        """
        logger.info("REST endpoint: get physical topology")
        try:
            # Get optional timestamp parameter
            timestamp_str = request.query.get('timestamp')
            
            topology = fetch_physical_topology(timestamp_str=timestamp_str)
            return web.json_response(topology)
        except Exception as e:
            logger.error(f"Error fetching physical topology: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching physical topology: {str(e)}"},
                status=500
            )

    #################################################################
    # Get router details
    #################################################################
    async def getRouterDetails(self, request):
        """
        Get detailed information for a specific router by ID.
        
        Args:
            request: The HTTP request object with router_id in the URL path
            
        Returns:
            aiohttp.web.Response: JSON response with router details
        """
        logger.info("REST endpoint: get router details")
        try:
            router_id = request.match_info.get('router_id')
            if not router_id:
                return web.json_response(
                    {"error": "No router ID provided"},
                    status=400
                )

            router_details = fetch_router_details(router_id)
            
            if 'error' in router_details:
                return web.json_response(router_details, status=404)
            
            return web.json_response(router_details)
            
        except Exception as e:
            logger.error(f"Error fetching router details: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching router details: {str(e)}"},
                status=500
            )

    #################################################################
    # Get device details
    #################################################################
    async def getDeviceDetails(self, request):
        """
        Get detailed information for a specific device by ID.
        
        Args:
            request: The HTTP request object with device_id in the URL path
            
        Returns:
            aiohttp.web.Response: JSON response with device details
        """
        logger.info("REST endpoint: get device details")
        try:
            device_id = request.match_info.get('device_id')
            if not device_id:
                return web.json_response(
                    {"error": "No device ID provided"},
                    status=400
                )

            device_details = fetch_device_details(device_id)
            
            if 'error' in device_details:
                return web.json_response(device_details, status=404)
            
            return web.json_response(device_details)
            
        except Exception as e:
            logger.error(f"Error fetching device details: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching device details: {str(e)}"},
                status=500
            )

    #################################################################
    # Get node embeddings (router and interfaces)
    #################################################################
    async def getNodeEmbeddings(self, request):
        """
        Get the latest embeddings for a router and its interfaces.
        
        Args:
            request: The HTTP request object with node_id in the URL path
            
        Returns:
            aiohttp.web.Response: JSON response with embeddings data including MSE
        """
        logger.info("REST endpoint: get node embeddings")
        try:
            node_id = request.match_info.get('node_id')
            if not node_id:
                return web.json_response(
                    {"error": "No node ID provided"},
                    status=400
                )

            embeddings_data = fetch_node_embeddings(node_id)
            
            if 'error' in embeddings_data:
                return web.json_response(embeddings_data, status=500)
            
            return web.json_response(embeddings_data)
            
        except Exception as e:
            logger.error(f"Error fetching node embeddings: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching node embeddings: {str(e)}"},
                status=500
            )

    #################################################################
    # Anomalies and Snapshots
    #################################################################
    async def getSnapshots(self, request):
        """
        Get the list of available network snapshot timestamps.
        
        Returns:
            aiohttp.web.Response: JSON response with snapshots array
        """
        logger.info("REST endpoint: get snapshots")
        try:
            snapshots_data = fetch_snapshots()
            if 'error' in snapshots_data:
                return web.json_response(snapshots_data, status=500)
            return web.json_response(snapshots_data)
        except Exception as e:
            logger.error(f"Error fetching snapshots: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching snapshots: {str(e)}"},
                status=500
            )

    async def getAnomalies(self, request):
        """
        Get the top anomalies for a specific timestamp or the latest snapshot.
        
        Args:
            request: HTTP request with optional query params 'limit' and 'timestamp'
            
        Returns:
            aiohttp.web.Response: JSON response with anomalies array
        """
        logger.info("REST endpoint: get anomalies")
        try:
            limit = int(request.query.get('limit', 50))
            timestamp_str = request.query.get('timestamp')
            
            anomalies_data = fetch_anomalies(limit=limit, timestamp_str=timestamp_str)
            
            if 'error' in anomalies_data:
                status_code = 400 if anomalies_data['error'] == 'Invalid timestamp format' else 500
                return web.json_response(anomalies_data, status=status_code)
                
            return web.json_response(anomalies_data)
        except ValueError:
            return web.json_response({"error": "Invalid limit parameter"}, status=400)
        except Exception as e:
            logger.error(f"Error fetching anomalies: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching anomalies: {str(e)}"},
                status=500
            )

    #################################################################
    # Add a remote agent
    #################################################################
    async def addAgent(self, request):
        logger.info("REST endpoint: add remote agent")
        try:
            # Get the URL of the agent to add from the request
            data = await request.json()
            if 'url' not in data:
                logger.error("No URL provided in add_remote_agent request")
                return web.json_response(
                    {"error": "No URL provided in request"},
                    status=400
                )
                
            url = data['url']
            logger.info(f"Adding agent with URL: {url}")
            
            # Get the HostAgent instance and add the remote agent
            agent = await HostAgent.get_instance()
            agent_data = await agent.add_remote_agent(url)
            
            if agent_data:
                logger.info(f"Successfully added agent: {agent_data}")
                
                # Return the agent data
                return web.json_response(agent_data)
            else:
                logger.error(f"Failed to add agent with URL: {url}")
                return web.json_response(
                    {"error": f"Failed to add agent with URL: {url}"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error adding remote agent: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error adding remote agent: {str(e)}"},
                status=500
            )
    
    #################################################################
    # List all added remote agents
    #################################################################
    async def listAgents(self, request):
        logger.info("REST endpoint: list all remote agents")
        try:
            agent = await HostAgent.get_instance()
            remote_agents = agent.list_all_remote_agents()
            logger.info(f"Returning {len(remote_agents)} remote agents: {remote_agents}")
            
            # Return the list of remote agents
            return web.json_response(remote_agents)
        except Exception as e:
            logger.error(f"Error listing remote agents: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error listing remote agents: {str(e)}"},
                status=500
            )

    #################################################################
    # Delete an agent
    #################################################################
    async def deleteAgent(self, request):
        logger.info("REST endpoint: delete remote agent")
        try:
            # Get the URL of the agent to delete from the request
            data = await request.json()
            if 'url' not in data:
                logger.error("No URL provided in delete_remote_agent request")
                return web.json_response(
                    {"error": "No URL provided in request"},
                    status=400
                )
                
            url = data['url']
            logger.info(f"Deleting agent with URL: {url}")
            
            # Get the HostAgent instance and delete the remote agent
            agent = await HostAgent.get_instance()
            success = await agent.delete_remote_agent(url)
            
            if success:
                # Get the updated list of remote agents
                remote_agents = agent.list_remote_agents()
                logger.info(f"Successfully deleted agent with URL: {url}")
                logger.info(f"Remaining agents: {remote_agents}")
                
                # Return the updated list of remote agents
                return web.json_response(remote_agents)
            else:
                logger.error(f"Failed to delete agent with URL: {url}")
                return web.json_response(
                    {"error": f"Failed to delete agent with URL: {url}"},
                    status=404
                )
        except Exception as e:
            logger.error(f"Error deleting remote agent: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error deleting remote agent: {str(e)}"},
                status=500
            )

    #################################################################
    # Callback for agent notifications
    #################################################################
    async def pushNotification(self, request):
        """
        Handle push notification requests from agents.

        notification states:
            'input_required': An agent needs information from the user to continue their task
            'new_incident': A fault has been sent to the resolver agent to be investigated
            'incident_update: There is a progress update from the resolver agent on a fault
        
        Notification request has the following structure:
        {
            "state": "<State>",
            "task_id": "<A2A Agent Task id>",
            "context_id": "<A2A Agent context id>",
            "content": "<Text Description of the notification>",
            "input_data": "<Any Meta data>"
        }
        
        Args:
            request: The HTTP request object
            
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("Received notification event")

        try:
            # Validate request has JSON content
            if not request.can_read_body:
                logger.error("Request has no body")
                return web.json_response(
                    {"error": "Request body is required"},
                    status=400
                )
                
            # Get the request data
            try:
                data = await request.json()
                logger.info(data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in request: {str(e)}")
                return web.json_response(
                    {"error": f"Invalid JSON in request: {str(e)}"},
                    status=400
                )
                
            # Validate required fields
            required_fields = ["name", "state", "task_id", "context_id", "content", "input_data"]
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                logger.error(f"Missing required fields: {missing_fields}")
                return web.json_response(
                    {"error": f"Missing required fields: {missing_fields}"},
                    status=400
                )

            # Send notification to all connected sockets
            success = await SocketEndpoint._instance.sendPushNotification(data)
            
            if success:
                logger.info("Successfully sent notification to all connected clients")
                return web.json_response({"status": "success"})
            else:
                logger.error("Failed to send notification to all connected clients")
                return web.json_response(
                    {"error": "Failed to send notification to all connected clients"},
                    status=500
                )

        except Exception as e:
            logger.error(f"Error processing push notification: {str(e)}", exc_info=True)
            
            # Return error response
            return web.json_response(
                {"error": f"Error processing push notification: {str(e)}"},
                status=500
            )
            
    #################################################################
    # Metrics endpoints
    #################################################################
    async def getAllLastMetrics(self, request):
        """
        Get the last metrics for all nodes
        
        Returns:
            aiohttp.web.Response: JSON response with the metrics data
        """
        logger.info("REST endpoint: get all last metrics")
        try:
            metrics = fetch_all_last_metrics()
            return web.json_response(metrics)
        except Exception as e:
            logger.error(f"Error fetching all last metrics: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching all last metrics: {str(e)}"},
                status=500
            )
    
    async def getAllMetrics(self, request):
        """
        Get all metrics for all nodes
        
        Returns:
            aiohttp.web.Response: JSON response with the metrics data
        """
        logger.info("REST endpoint: get all metrics")
        try:
            metrics = fetch_all_metrics()
            return web.json_response(metrics)
        except Exception as e:
            logger.error(f"Error fetching all metrics: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching all metrics: {str(e)}"},
                status=500
            )
    
    async def getLastMetricsForId(self, request):
        """
        Get the last metrics for a specific node
        
        Args:
            request: The HTTP request object with node_id in the URL path
            
        Returns:
            aiohttp.web.Response: JSON response with the metrics data
        """
        logger.info("REST endpoint: get last metrics for id")
        try:
            node_id = request.match_info.get('node_id')
            if not node_id:
                return web.json_response(
                    {"error": "No node ID provided"},
                    status=400
                )
                
            metrics = fetch_last_metrics_for_id(node_id)
            return web.json_response(metrics)
        except Exception as e:
            logger.error(f"Error fetching last metrics for id: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching last metrics for id: {str(e)}"},
                status=500
            )
    
    async def getAllMetricsForId(self, request):
        """
        Get all metrics for a specific node
        
        Args:
            request: The HTTP request object with node_id in the URL path
            
        Returns:
            aiohttp.web.Response: JSON response with the metrics data
        """
        logger.info("REST endpoint: get all metrics for id")
        try:
            node_id = request.match_info.get('node_id')
            if not node_id:
                return web.json_response(
                    {"error": "No node ID provided"},
                    status=400
                )
                
            metrics = fetch_all_metrics_for_id(node_id)
            return web.json_response(metrics)
        except Exception as e:
            logger.error(f"Error fetching all metrics for id: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching all metrics for id: {str(e)}"},
                status=500
            )
    
    async def getRoutingMetrics(self, request):
        """
        Get the latest underlay (Layer 2) routing-protocol metrics for a router.

        GET /metrics/routing/{node_id}

        Reads from Spanner NetworkMetrics (kind='ROUTING') for the given node,
        preserving per-label context so that per-interface OSPF counts, per-peer
        BGP uptimes, per-AFI route totals, and per-collector health are all
        returned correctly.

        Returns:
            JSON object with ospf, bgp_peers, routes, collectors, bfd_peers fields.
        """
        logger.info("REST endpoint: get routing metrics for node")
        try:
            node_id = request.match_info.get("node_id")
            if not node_id:
                return web.json_response({"error": "No node_id provided"}, status=400)

            data = fetch_routing_metrics(node_id)
            return web.json_response(data)
        except Exception as e:
            logger.error(f"Error fetching routing metrics: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching routing metrics: {str(e)}"},
                status=500,
            )

    async def resetMetrics(self, request):
        """
        Reset all metrics
        
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: reset metrics")
        try:
            success = clear_network_metrics()
            if success:
                return web.json_response({"status": "success"})
            else:
                return web.json_response(
                    {"error": "Failed to reset metrics"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error resetting metrics: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error resetting metrics: {str(e)}"},
                status=500
            )
    
    #################################################################
    # Logs endpoints
    #################################################################
    async def deleteLogs(self, request):
        """
        Delete all logs
        
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: delete logs")
        try:
            success = delete_logs()
            if success:
                return web.json_response({"status": "success"})
            else:
                return web.json_response(
                    {"error": "Failed to delete logs"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error deleting logs: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error deleting logs: {str(e)}"},
                status=500
            )

    #################################################################
    # Topology reset endpoint
    #################################################################
    async def resetTopology(self, request):
        """
        Clear all physical and logical topology tables from Spanner.

        POST /topology/reset

        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: reset topology")
        try:
            success = clear_topology()
            if success:
                return web.json_response({"status": "success"})
            else:
                return web.json_response(
                    {"error": "Failed to reset topology"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error resetting topology: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error resetting topology: {str(e)}"},
                status=500
            )

    #################################################################
    # VPN endpoints
    #################################################################

    async def getVpns(self, request):
        """
        Get all VyOSL3VPN CRDs from Kubernetes.

        GET /vpns?namespace=default

        Returns:
            JSON array of VPN summaries.
        """
        logger.info("REST endpoint: get VPNs")
        try:
            namespace = request.query.get("namespace", "network")
            vpns = fetch_vpns(namespace=namespace)
            return web.json_response(vpns)
        except Exception as e:
            logger.error(f"Error fetching VPNs: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching VPNs: {str(e)}"},
                status=500
            )

    async def getTrafficTests(self, request):
        """
        Get all TrafficTest CRDs from Kubernetes.

        GET /traffictests?namespace=default

        Returns:
            JSON array of TrafficTest summaries.
        """
        logger.info("REST endpoint: get TrafficTests")
        try:
            namespace = request.query.get("namespace", "network")
            tests = fetch_traffic_tests(namespace=namespace)
            return web.json_response(tests)
        except Exception as e:
            logger.error(f"Error fetching TrafficTests: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching TrafficTests: {str(e)}"},
                status=500
            )

    async def deleteVpn(self, request):
        """
        Delete a VyOSL3VPN and all its linked TrafficTests.

        POST /vpns/{name}/delete

        Returns HTTP 409 if a VPN delete is already in progress.
        Returns HTTP 202 (Accepted) and fires a background task otherwise.
        """
        logger.info("REST endpoint: delete VPN")
        try:
            vpn_name = request.match_info.get("name")
            if not vpn_name:
                return web.json_response({"error": "No VPN name provided"}, status=400)

            status = get_vpn_delete_status()
            if status["in_progress"]:
                return web.json_response(
                    {"error": "A VPN delete is already in progress",
                     "vpn_name": status["vpn_name"]},
                    status=409,
                )

            namespace = request.query.get("namespace", "network")
            asyncio.create_task(delete_vpn_with_tests_background(vpn_name, namespace))
            logger.info("Scheduled background VPN delete for '%s'", vpn_name)
            return web.json_response({"status": "deleting", "vpn_name": vpn_name})

        except Exception as e:
            logger.error(f"Error starting VPN delete: {str(e)}", exc_info=True)
            return web.json_response({"error": f"Error starting VPN delete: {str(e)}"}, status=500)

    async def getVpnDeleteStatus(self, request):
        """
        Return whether a VPN delete is currently in progress.

        GET /vpns/delete/status

        Returns:
            JSON object with { in_progress: bool, vpn_name: str | null }
        """
        logger.info("REST endpoint: get VPN delete status")
        try:
            return web.json_response(get_vpn_delete_status())
        except Exception as e:
            logger.error(f"Error getting VPN delete status: {str(e)}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def deleteTrafficTest(self, request):
        """
        Delete a single TrafficTest CRD.

        POST /traffictests/{name}/delete

        Returns HTTP 200 on success or HTTP 500 on failure.
        """
        logger.info("REST endpoint: delete TrafficTest")
        try:
            test_name = request.match_info.get("name")
            if not test_name:
                return web.json_response({"error": "No test name provided"}, status=400)

            namespace = request.query.get("namespace", "network")
            success = delete_traffic_test_crd(test_name, namespace)
            if success:
                return web.json_response({"status": "deleted", "name": test_name})
            else:
                return web.json_response(
                    {"error": f"Failed to delete TrafficTest/{test_name}"},
                    status=500,
                )
        except Exception as e:
            logger.error(f"Error deleting TrafficTest: {str(e)}", exc_info=True)
            return web.json_response({"error": f"Error deleting TrafficTest: {str(e)}"}, status=500)

    async def getTrafficTestMetrics(self, request):
        """
        Get the latest traffic-agent flow metrics for a single TrafficTest.

        GET /traffictests/{name}/metrics

        Reads from Spanner NetworkMetrics (kind='TRAFFIC') for all flows whose
        flow_id starts with ``{name}_``.  Returns an empty list when no data has
        been scraped yet (test not started or metrics collector not running).

        Returns:
            JSON array of flow-metric objects, one per (flow_id, role, protocol).
        """
        logger.info("REST endpoint: get TrafficTest metrics")
        try:
            test_name = request.match_info.get("name")
            if not test_name:
                return web.json_response({"error": "No test name provided"}, status=400)

            flows = fetch_traffic_test_metrics(test_name)
            return web.json_response(flows)
        except Exception as e:
            logger.error(f"Error fetching TrafficTest metrics: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching TrafficTest metrics: {str(e)}"},
                status=500,
            )

    async def getVyosInfrastructure(self, request):
        """
        Get all VyosInfrastructure CRDs from Kubernetes.

        GET /infrastructure?namespace=default

        Returns:
            JSON array of VyosInfrastructure summaries with phase and resource counts.
        """
        logger.info("REST endpoint: get VyosInfrastructure")
        try:
            namespace = request.query.get("namespace", "network")
            infra = fetch_vyos_infrastructure(namespace=namespace)
            return web.json_response(infra)
        except Exception as e:
            logger.error(f"Error fetching VyosInfrastructure: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching VyosInfrastructure: {str(e)}"},
                status=500
            )

    async def getInfrastructureState(self, request):
        """
        Return VPNs, TrafficTests, and VyosInfrastructure in a single response.

        GET /infrastructure/state?namespace=default

        Replaces three sequential REST calls from the dashboard VPN refresh timer
        (GET /vpns + GET /traffictests + GET /infrastructure) with one round-trip,
        reducing Kubernetes API fan-out by 2/3 on every 15-second poll cycle.

        Returns:
            JSON object with keys: vpns, traffic_tests, infrastructure
        """
        logger.info("REST endpoint: get combined infrastructure state")
        try:
            namespace = request.query.get("namespace", "network")
            state = fetch_infrastructure_state(namespace=namespace)
            return web.json_response(state)
        except Exception as e:
            logger.error(f"Error fetching infrastructure state: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching infrastructure state: {str(e)}"},
                status=500,
            )

    #################################################################
    # Get All Available agents
    #################################################################    
    async def getAvailableAgents(self, request):
        """
        Get available network agents running that can be added to the
        autonomous network agent UI
        
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: get available agents")
        try:
            agents = await get_available_agents()
            return web.json_response(agents)
        except Exception as e:
            logger.error(f"Error getting available agents: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error getting available agents: {str(e)}"},
                status=500
            )

    #################################################################
    # Network descriptor endpoints
    #################################################################

    async def listNetworkDescriptors(self, request):
        """
        List all stored network descriptors (summary only — no CRD bodies).

        GET /networks

        Returns:
            JSON array of { id, name, description, labels, updated_at }
        """
        logger.info("REST endpoint: list network descriptors")
        try:
            descriptors = list_network_descriptors()
            return web.json_response(descriptors)
        except Exception as e:
            logger.error(f"Error listing network descriptors: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error listing network descriptors: {str(e)}"},
                status=500
            )

    async def getNetworkDescriptor(self, request):
        """
        Get summary metadata for a single network descriptor.

        GET /networks/{network_id}

        The network_id path segment uses the Spanner primary key convention,
        e.g. ``network%3Adefault`` (URL-encoded form of ``network:default``).

        Returns:
            JSON object with { id, name, description, labels, updated_at,
                               vpn_count, traffic_test_count }
            or 404 if not found.
        """
        logger.info("REST endpoint: get network descriptor")
        try:
            network_id = request.match_info.get("network_id")
            if not network_id:
                return web.json_response(
                    {"error": "No network_id provided"},
                    status=400
                )

            summary = get_network_descriptor_summary(network_id)
            if summary is None:
                return web.json_response(
                    {"error": f"Network descriptor '{network_id}' not found"},
                    status=404
                )
            return web.json_response(summary)
        except Exception as e:
            logger.error(f"Error fetching network descriptor: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching network descriptor: {str(e)}"},
                status=500
            )

    async def teardownDeployment(self, request):
        """
        Tear down all existing network CRDs without deploying a replacement.

        POST /networks/teardown

        The teardown runs in the background (fire-and-forget).  Progress is
        streamed to all connected dashboard clients via ``deploy_progress``
        Socket.IO events.

        Returns:
            JSON object with { status: "tearing_down" }
        """
        logger.info("REST endpoint: teardown current deployment")
        try:
            asyncio.create_task(teardown_only_background())
            logger.info("Scheduled background teardown of current deployment")
            return web.json_response({"status": "tearing_down"})
        except Exception as e:
            logger.error(f"Error starting teardown: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error starting teardown: {str(e)}"},
                status=500
            )

    async def deployNetworkDescriptor(self, request):
        """
        Deploy a network descriptor by applying its CRDs to the k8s cluster.

        POST /networks/{network_id}/deploy

        The CRDs are applied in the background (fire-and-forget).  The
        response is returned immediately with status ``deploying``.

        Returns:
            JSON object with { status, network_id, name, vpn_count,
                               traffic_test_count }
            or 404 if the descriptor is not found.
        """
        logger.info("REST endpoint: deploy network descriptor")
        try:
            network_id = request.match_info.get("network_id")
            if not network_id:
                return web.json_response(
                    {"error": "No network_id provided"},
                    status=400
                )

            # Load the full descriptor (synchronous Spanner read)
            descriptor = get_descriptor_for_deploy(network_id)
            if descriptor is None:
                return web.json_response(
                    {"error": f"Network descriptor '{network_id}' not found"},
                    status=404
                )

            # Fire-and-forget: GitOps path — delete from git, wait for
            # VyOSInfrastructure CR to be gone, then commit new YAML files.
            asyncio.create_task(deploy_to_git_background(descriptor))

            logger.info(
                "Scheduled GitOps deploy for '%s' (%d VPN(s), %d test(s))",
                network_id,
                len(descriptor.get("vpns", [])),
                len(descriptor.get("traffic_tests", [])),
            )

            return web.json_response({
                "status":               "deploying",
                "network_id":           network_id,
                "name":                 descriptor.get("name"),
                "vpn_count":            len(descriptor.get("vpns", [])),
                "traffic_test_count":   len(descriptor.get("traffic_tests", [])),
            })

        except Exception as e:
            logger.error(f"Error deploying network descriptor: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error deploying network descriptor: {str(e)}"},
                status=500
            )
