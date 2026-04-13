# CLAUDE.md — AI Assistant Context for Claude

This file provides Claude with context about the **Autonomous Network Lab** repository to enable effective code assistance.

## Project Overview

**Autonomous Network Lab** is a sandbox demonstrating autonomous network lifecycle management. It uses Graph Neural Networks (GNNs) and AI agents to monitor, analyze, troubleshoot, and manage complex telecommunications networks. It includes a virtual network simulator with real-time topology understanding, automated fault detection, and intelligent incident resolution.

## Repository Structure

```
gnnsandbox/
├── gnn/                    # GNN training & serving (Python, Vertex AI)
│   ├── src/
│   │   ├── train_hetgnn.py     # Heterogeneous GNN training entrypoint
│   │   ├── serve.py            # Model serving entrypoint
│   │   ├── model/              # GNN model definitions
│   │   ├── pipeline/           # Training pipeline stages
│   │   └── utils/              # Shared utilities
│   └── tests/                  # Training/serving tests and notebooks
├── networkagents/          # A2A-compliant specialized network agents (Python)
│   ├── supervisor/             # Incident management & orchestration agent
│   ├── logs/                   # Log analysis agent
│   ├── tester/                 # Network testing agent
│   ├── chaos/                  # Chaos engineering agent
│   └── designer/               # Network design agent
├── tools/                  # MCP tool server (Python, Cloud Run)
│   └── src/
│       ├── main.py             # Tool server entrypoint
│       ├── tools/              # Individual MCP tools
│       └── utils/              # Shared utilities
├── operator/               # K8s operator for VNF lifecycle management (Python)
│   └── src/
│       ├── main.py             # Operator entrypoint
│       ├── device/             # Device management
│       ├── graph/              # Graph/topology management
│       ├── vyosrouter/         # VyOS router configuration
│       ├── vyosvpn/            # VPN configuration
│       └── ...
├── logservices/            # Log & metrics collection (Python, Cloud Functions)
│   ├── faultservice/           # Fault detection service
│   ├── logcollector/           # Log collection
│   └── metricscollector/       # Metrics collection
├── lib/                    # Shared Python library (agent_library)
│   └── src/agent_library/
├── ui/dashboard/           # Web dashboard (Flutter/Dart)
├── environment/            # GCP infrastructure manifests (Jinja2, YAML)
│   ├── configconnector.j2      # Config Connector setup
│   ├── spanner.j2              # Spanner database setup
│   ├── networkvm.yaml          # Network simulator VM
│   └── free5gc/                # 5G core network config
├── docs/                   # Documentation and architecture diagrams
├── install.sh              # Main installation/management script
└── setenv.sh               # Environment variable setup
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language (backend) | Python 3.13 |
| Language (frontend) | Flutter / Dart |
| Cloud Platform | Google Cloud Platform (GCP) |
| Container Orchestration | GKE (Google Kubernetes Engine) |
| Database | Google Cloud Spanner |
| ML Platform | Vertex AI |
| GNN Framework | JAX / PyTorch / TensorFlow (see `docs/gnn/examples/`) |
| Network Simulator | VyOS (transport), free5gc (5G core) |
| Agent Protocol | A2A (Agent-to-Agent) |
| Tool Protocol | MCP (Model Context Protocol) |
| Infrastructure | Config Connector, Config Sync, Ansible |
| CI/CD | Cloud Build (`.j2` Jinja2 templates → `cloudbuild.yaml`) |
| Serving | Google Cloud Run |

## Key Commands

### Environment Setup
```bash
# Edit and source environment variables before anything else
vi setenv.sh
source ./setenv.sh
```

### Installation
```bash
# Full install (recommended)
./install.sh --all -y

# Step-by-step
./install.sh -c    # Create environment config
./install.sh -s    # Start GCP runtime services
./install.sh -n all  # Deploy all agents and dashboard
```

### Selective Deployment
```bash
./install.sh --deploy operator        # Redeploy K8s operator
./install.sh --deploy logcapture      # Redeploy log capture
./install.sh --deploy metricscollector
./install.sh -n dashboard             # Redeploy dashboard only
./install.sh -n supervisor,tester     # Redeploy specific agents
```

### Environment Info
```bash
./install.sh -g    # Show active GCP environment
./install.sh -i    # Show deployed service URLs
```

### Cleanup
```bash
./install.sh -k    # Stop and delete runtime (keeps config)
./install.sh -d    # Delete environment config
./install.sh -k; ./install.sh -d   # Full cleanup
```

## Development Conventions

- **Cloud Build**: Each deployable component has a `cloudbuild.j2` Jinja2 template that is rendered into a `cloudbuild.yaml` at deploy time using environment variables from `setenv.sh`.
- **Dockerfiles**: Each component has its own `Dockerfile` at the component root.
- **Requirements**: Python dependencies are in `requirements.txt` per component; Vertex AI variants use `requirements.vertex.txt`.
- **Agents**: Each agent in `networkagents/` follows the same layout: `src/`, `Dockerfile`, `requirements.txt`, `cloudbuild.j2`, `deploy.sh`, `Readme.md`.
- **Shared Library**: Common agent functionality lives in `lib/src/agent_library/` — import from there rather than duplicating.
- **Config files**: Operator configuration YAML files live in `operator/config/`.

## Required GCP Environment Variables

```bash
export GOOGLE_PROJECT=<project-id>
export GOOGLE_USER=<user@domain.com>
export GOOGLE_VM_USER=<gce-vm-username>
export GOOGLE_REGION=<region>        # e.g. europe-west1
export GOOGLE_ZONE=<zone>            # e.g. europe-west1-c
export WEBAPPS_LOGIN=<login>
export WEBAPPS_PWD=<password>
```

## GCP Prerequisites

- Owner role on the GCP project
- Organization policies configured:
  - `compute.vmExternalIpAccess` → Allow All
  - `compute.requireShieldedVm` → Off
  - `iam.disableServiceAccountKeyCreation` → Off
  - `compute.vmCanIpForward` → Allow All
  - `iam.allowedPolicyMemberDomains` → Allow All

## Key Documentation

- [Architecture Overview](docs/drawings/architecture.drawio.svg)
- [Network Simulator](docs/network/Readme.md)
- [GNN Models](docs/gnn/Readme.md)
- [Spanner Schema](docs/spanner/Readme.md)
- [Agents](docs/agents/Readme.md)
- [Transport RCA Scenario](docs/scenarios/transport.md)
