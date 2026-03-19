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
import logging
import json
import time
from aiohttp_cors.cors_config import CorsConfig
import aiohttp_cors
from aiohttp import web
from agent.host_agent import HostAgent
from endpoints.socketendpoint import SocketEndpoint
from tools.topology import fetch_physical_topology, fetch_router_details, fetch_device_details, fetch_node_embeddings, fetch_snapshots, fetch_anomalies
from tools.metrics import (
    fetch_all_last_metrics,
    fetch_all_metrics,
    fetch_last_metrics_for_id,
    fetch_all_metrics_for_id,
    clear_network_metrics,
)
from tools.service_performance import clear_service_metrics
from tools.incidents import clear_incidents
from tools.agents import get_available_agents
from tools.logs import delete_logs
from tools.service_performance import get_active_users, get_user_session_details, get_average_performance_by_service_type
from tools.incidents import fetch_all_open_incidents

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

        deleteLogsRoute = self.app.router.add_post("/logs/delete", self.deleteLogs)
        self.cors.add(deleteLogsRoute, corsConfig)

        getAvailableAgentsRoute = self.app.router.add_get("/agents/available", self.getAvailableAgents)
        self.cors.add(getAvailableAgentsRoute, corsConfig)

        getAllOpenIncidentsRoute = self.app.router.add_get("/incidents", self.getAllOpenIncidents)
        self.cors.add(getAllOpenIncidentsRoute, corsConfig)

        deleteIncidentsRoute = self.app.router.add_post("/incidents/delete", self.resetIncidents)
        self.cors.add(deleteIncidentsRoute, corsConfig)

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
    
    async def resetMetrics(self, request):
        """
        Reset all metrics
        
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: reset metrics")
        try:
            success = clear_network_metrics()
            success = clear_service_metrics()
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
    # Incidents endpoints
    #################################################################
    async def getAllOpenIncidents(self, request):
        """
        Get all open incidents from the Spanner database
        
        Returns:
            aiohttp.web.Response: JSON response with the incidents data
        """
        logger.info("REST endpoint: get all open incidents")
        try:
            incidents = fetch_all_open_incidents()
            return web.json_response(incidents)
        except Exception as e:
            logger.error(f"Error fetching open incidents: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error fetching open incidents: {str(e)}"},
                status=500
            )

    async def resetIncidents(self, request):
        """
        Delete all incidents from the Spanner database
        
        Returns:
            aiohttp.web.Response: JSON response indicating success or failure
        """
        logger.info("REST endpoint: delete all incidents")
        try:
            success = clear_incidents()
            if success:
                return web.json_response({"status": "success"})
            else:
                return web.json_response(
                    {"error": "Failed to delete incidents"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error deleting incidents: {str(e)}", exc_info=True)
            return web.json_response(
                {"error": f"Error deleting incidents: {str(e)}"},
                status=500
            )
