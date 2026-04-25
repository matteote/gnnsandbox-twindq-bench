# Autonomous Network Lab

**Autonomous Network Lab** is a sandbox to demonstrate autonomous network lifecycle management. Leveraging Graph Neural Networks (GNNs) and AI agents to intelligently monitor, analyze, troubleshoot, and manage complex telecommunications networks. 

This project provides a virtual network simulator with real-time network topology understanding, automated fault detection, intelligent incident correlation and resolution capabilities.

### Functional Components

The main components of the autonomous network lab are shown below:

![gcp architecture](/docs/drawings/architecture.drawio.svg)

- [**Network Simulator**](/docs/network/Readme.md): [VyOS](https://vyos.io/) & [free5gc](https://free5gc.org/) based virtual network simulator can deploy complex network topologies, run traffic patterns and generate network state and performance metrics. 
- [**GKE Network Automation**](/docs/automation/Readme.md): GKE operator deploys network topologies and traffic tests to the network simulator and updates the Spanner digital shadow with topology and state updates.
- [**Digital Shadow**](/docs/spanner/Readme.md): Google Cloud Spanner stores network topology graphs, temporal state and historical performance. 
- [**Network GNNs**](/docs/gnn/Readme.md): Training and serving infrastructure for graph neural network models that can pinpoint failures and predict the impact of network changes. 
- [**Network Agents**](/docs/agents/Readme.md): Specialized agents for network testing, log analysis, and incident management

## GCP Architecture

The lab GCP deployment architecture is shown in the diagram below. 

![GCP Architecture](/docs/drawings/gcp.drawio.svg)

| Component | Description | Location   |
|-----------|-------------|------------|
| Dashboard | User Interface showing network topology along with current and historical anomalies. Allows users to interact with network agents.  | /ui/dashboard  |
| Agents | A2A compliant network agents that run tests, analyse anomalies and root cause | /networkagents |
| Tools | MCP tool server brokers agent communications to network orchestration and spanner historical network state | /tools |
| Config Connector | K8s based GCP infrastrastructure orchestration. | /environment/configconnector.j2 |
| Orchestration Operator | Automation code for managing virtual network function lifecycles.  | /operator |
| Network VM | Network simulator virtual machine, lifecycle managed by Config Connector. | /environment/networkvm.yaml |
| Cloud Monitoring | All metrics and syslog for network functions sent to Cloud Monitoring. Eventarc and Cloud functions process and update Spanner in near real time. | /logservices |
| Spanner | Current and historical network topology and state used to train and run inference with GNNs. | /environment/spanner.j2 |
| Vertex AI | Run-time for training and inferencing GNNs | /gnn |

## Running the demo

* [Installation Instructions](/INSTALL.md)
* [Transport RCA Scenario](/docs/scenarios/transport.md)
* 5g Scenario coming

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE). All other artifacts such as images, video, audio and data as free/open material is provided under the [CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/).