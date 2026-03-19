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

from ag_ui.core import Tool

# UI tool to get feedback
approvalTool = Tool(
    name="requestTaskApproval",
    description="""
        Request user approval for one or more tasks before proceeding. Displays tasks with details and importance levels for user confirmation.

        The response from the tool is a dictionary representing the users decision. 

        Below is the structure of the response from the tool.
        {
            'approved': <boolean decision from the user on whether to proceed or not>,
            'timestamp': <current time>,
            'tasks': <list of tasks to approve>,
        }

        The 'approved' value determines whether to proceed or not. 
    """,
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the approval request (e.g., 'Task Approval Required')"
            },
            "tasks": {
                "type": "array",
                "description": "List of tasks requiring approval",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name or title of the task"
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description of what the task will do"
                        },
                    },
                    "required": ["name", "description"]
                }
            },
        },
        "required": ["title", "tasks"]
    }
)

# show a network topology widget in the chat UI
networkTopologyTool = Tool(
    name="displayNetworkTopology",
    description="""
    This tools displays a network topology visualization in the User Interface using nodes and edges structure. 
    Shows network components and their relationships in an interactive graph format.
    """,
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the network topology display"
            },
            "elements": {
                "type": "array",
                "description": "Array of network topology elements (nodes and edges)",
                "items": {
                    "type": "object",
                    "properties": {
                        "group": {
                            "type": "string",
                            "enum": ["nodes", "edges"],
                            "description": "Type of element: 'nodes' for network components, 'edges' for connections"
                        },
                        "data": {
                            "type": "object",
                            "description": "Element data structure",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Unique identifier for nodes, or edge identifier"
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Display label for the element"
                                },
                                "kind": {
                                    "type": "string",
                                    "description": "Type of network component (e.g., 'ComputeInstance', 'ComputeNetwork', 'ComputeFirewall')"
                                },
                                "name": {
                                    "type": "string",
                                    "description": "Name of the network component"
                                },
                                "status": {
                                    "type": "string",
                                    "description": "Status of the network component (e.g., 'RUNNING', 'STOPPED', 'PENDING')"
                                },
                                "source": {
                                    "type": "string",
                                    "description": "Source node ID for edges"
                                },
                                "target": {
                                    "type": "string",
                                    "description": "Target node ID for edges"
                                },
                                "src_kind": {
                                    "type": "string",
                                    "description": "Kind of source node for edges"
                                },
                                "tgt_kind": {
                                    "type": "string",
                                    "description": "Kind of target node for edges"
                                }
                            },
                            "required": ["id"]
                        },
                        "selectable": {
                            "type": "boolean",
                            "description": "Whether the element can be selected in the UI",
                            "default": True
                        }
                    },
                    "required": ["group", "data"]
                }
            },
            "layout": {
                "type": "string",
                "enum": ["hierarchical", "force", "circular", "grid", "breadthfirst", "cose"],
                "description": "Layout algorithm for the topology visualization",
                "default": "hierarchical"
            },
            "view": {
                "type": "string",
                "description": "Network view type (e.g., 'dataplane', 'service', 'physical')"
            },
            "showLabels": {
                "type": "boolean",
                "description": "Whether to show labels on nodes and edges",
                "default": True
            },
            "showStatus": {
                "type": "boolean",
                "description": "Whether to show status indicators on nodes",
                "default": True
            },
            "enableZoom": {
                "type": "boolean",
                "description": "Whether to enable zoom functionality",
                "default": True
            },
            "enablePan": {
                "type": "boolean",
                "description": "Whether to enable pan functionality",
                "default": True
            },
            "nodeColors": {
                "type": "object",
                "description": "Color mapping for different node types",
                "properties": {
                    "ComputeInstance": {"type": "string"},
                    "ComputeNetwork": {"type": "string"},
                    "ComputeSubnetwork": {"type": "string"},
                    "ComputeFirewall": {"type": "string"},
                    "ComputeRoute": {"type": "string"},
                    "WireguardAppliance": {"type": "string"}
                }
            },
            "edgeColors": {
                "type": "object",
                "description": "Color mapping for different edge types",
                "properties": {
                    "dataplane": {"type": "string"},
                    "service": {"type": "string"},
                    "physical": {"type": "string"}
                }
            },
            "height": {
                "type": "number",
                "description": "Height of the topology visualization in pixels",
                "default": 600
            },
            "width": {
                "type": "number",
                "description": "Width of the topology visualization in pixels (auto-sizing if not specified)"
            },
            "interactive": {
                "type": "boolean",
                "description": "Whether the topology should be interactive (clickable nodes/edges)",
                "default": True
            },
            "showLegend": {
                "type": "boolean",
                "description": "Whether to show a legend explaining node types and statuses",
                "default": True
            }
        },
        "required": ["title", "elements"]
    }
)

# call this to display a chart in the UI
chartTool = Tool(
    name="displayTimeSeriesChart",
    description="""
    Useful to visualise network performance data. 

    This tool presents time series information as graphical charts in the User Interface. 
    Supports line charts, area charts, bar charts, and scatter plots for time-based data.
    """,
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the chart"
            },
            "data": {
                "type": "array",
                "description": "Array of time series data points",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {
                            "type": "string",
                            "description": "ISO 8601 timestamp or date string (e.g., '2024-01-01T00:00:00Z' or '2024-01-01')"
                        },
                        "value": {
                            "type": "number",
                            "description": "Numeric value for this time point"
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional label for this data point"
                        },
                        "series": {
                            "type": "string",
                            "description": "Optional series name for multi-series charts"
                        }
                    },
                    "required": ["timestamp", "value"]
                }
            },
            "chartType": {
                "type": "string",
                "enum": ["line", "area", "bar", "scatter"],
                "description": "Type of chart to display",
                "default": "line"
            },
            "xAxisLabel": {
                "type": "string",
                "description": "Label for the X-axis (time axis)"
            },
            "yAxisLabel": {
                "type": "string",
                "description": "Label for the Y-axis (value axis)"
            },
            "timeFormat": {
                "type": "string",
                "description": "Format for displaying time labels (e.g., 'YYYY-MM-DD', 'HH:mm', 'MMM DD')"
            },
            "valueFormat": {
                "type": "string",
                "description": "Format for displaying values (e.g., '.2f' for 2 decimal places, '.0%' for percentage)"
            },
        },
        "required": ["title", "data"]
    }
)

tableTool=Tool(
    name="displayTable",
    description="""
    Used to visualise a tablular set of data. 
    """,
    parameters={}
)

actionTool= Tool(   
    name="displayActionResult",
    description="""
    Used to visualise the status of an action result in the user interface. 
    """,
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the task"
            },
            "action": {
                "type": "string",
                "description": "description of the task action"
            },
            "status": {
                "type": "string",
                "description": "the status of the task"
            },
        },
        "required": ["title", "action", "status"]
    }
)
