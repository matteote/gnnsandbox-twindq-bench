# Network Agent Installation Guide

This guide describes how to set up the Network Agent GCP environment.

## Quick Start

Edit the file `setenv.sh`, set the values of the variables to suit your GCP environment. Then
```bash
source ./setenv.sh
```
Check the prerequisites below.
The for a complete, unattended (no question asked) installation, simply run:

```bash
./install.sh --all -y
```
The first time installation takes roughly 30-40 minutes

At the end of the installation you'll see a recap of the key URLs to access the demo, most notably the dashboard and the gitea server (git repo used for network gitops)

For your information, the full installation process goes through the following steps:
- Check that your GCP environment is set properly (project, org policies, permissions, APIs,...)
- Create the demo environment configuration if needed
- Start the runtime services
- Deploy all network agents and dashboard

## Prerequisites

The following packages are required before proceeding with the installation:

* [Google Cloud Command Line interface](https://cloud.google.com/sdk/docs/install)
* kubectl (on Debian: `sudo apt-get install kubectl`)
* Python3 pip installer (on Debian: `sudo apt-get install python3-pip`)
* jinja templating engine (`pip install jinja-cli`)
* ansible (`pip install ansible`)
* [flutter sdk](https://flutter.dev/)

**Note:** It is recommended to create your own [Python virtual environment](https://docs.python.org/3/library/venv.html) first prior to installing jinja or any other python packages. We recommend using Python 3.13.

### Update Organization Policies

The install script will do the GCP org policies check for you, but you may want to ensure the organization policy values below are set as follows:

* Set **constraints/compute.vmExternalIpAccess** to **Allow All**
* Set **constraints/compute.requireShieldedVm** to **Off**
* Set **constraints/iam.disableServiceAccountKeyCreation** to **Off**
* Set **constraints/compute.vmCanIpForward** to **Allow All**
* Set **constraints/iam.allowedPolicyMemberDomains** to **Allow All**

## Environment Setup

### Setup gcloud

[Install](https://cloud.google.com/sdk/docs/install) and initialize gcloud:

```bash
gcloud init --no-launch-browser
```

### Setup GCP Environment Variables

Setup and export the following environment variables. They are used throughout the setup docs and installation scripts:

```bash
export GOOGLE_PROJECT=<YOUR PROJECT>        # the GCP project name hosting the NW Agent demo (You MUST create it first on GCP)
export GOOGLE_USER=<GCP_USERNAME>           # the user you authenticate with on GCP. It MUST be the owner of the GOOGLE_PROJECT (e.g. john.doe@mydomain.com)
export GOOGLE_VM_USER=<GCE_VM_USERNAME>     # the default user name on GCE VMs (usually john_doe_mydomain_com but to be sure create a VM, SSH connect from the web console, type 'whoami', delete VM)
export GOOGLE_REGION=<YOUR_REGION>          # the GCP region to host the demo environment (e.g. europe-west1)
export GOOGLE_ZONE=<YOUR_ZONE>              # the GCP zone in the region to host the demo environment (e.g. europe-west1-c)
export WEBAPPS_LOGIN=<YOUR_WEB_LOGIN>       # the login name to access web apps like the NW Agent UI or the Gitops Web UI
export WEBAPPS_PWD=<YOUR_WEB_PWD>           # the password to access the web apps
```

## Installation Options

The **install.sh** script provides flexible installation options:

```bash
Autonomous Network Agent (ANA) environment manager.

Syntax: install.sh [-c|-s|-b|-o|-l|-f|-r|-n|-k|-d|-g|-i|-w|--all|--deploy] [-y|-N]

long options:
-------------
  --all  install everything (comprehensive setup: create env if needed, build image if needed, start runtime, deploy all agents)
         can be combined with -y or -N flags (e.g., ./install.sh -all -y)
  --deploy component1 component2
         (re)deploy specific components (valid components : spanner, operator, logcapture, git)

short options:
--------------
  -c     create network agent environment (keys, manifests,..)
  -s     build and start network agent runtime (incl. the operator)
  -o     build and deploy the network operator (same as --deploy operator)
  -l     build and deploy the logs capture function (same as --deploy logcapture)
  -f     build and deploy the fault capture and trigger service
  -n     build and deploy the network dashboard and network agents
         can be followed by a comma-separated list of agent names to (re)deploy selectively
         valid agent names: all, networktools, supervisor, engineer, dashboard, operations, test, resolver
         example: -n dashboard,operations or -n all (to deploy all agents)
  -k     stop and delete the network agent runtime (GKE cluster, VMS, DB, etc..)
  -d     delete the network agent environment (keys, manifests...).
  -g     display active GCP environment (user, project, GKE cluster,...)
  -i     display demo information
  -w     wipe out the entire autonomous network agent demo resources (ptp, mesh, uetest,...)
  -y     answer 'yes' to all questions (no ask for confirmation)
  -N     answer 'no' to all questions (no ask for confirmation)

Some typical use cases:
 - To install everything from scratch: ./install.sh --all
 - To install everything from scratch without prompts: ./install.sh --all -y
 - To install everything from scratch, skipping rebuilds: ./install.sh --all -N
 - To create and run a network agent environment including the operator: ./install.sh -c; ./install.sh -s
 - To redeploy the operator alone : ./install.sh -o (or --deploy operator)
 - To (re)deploy the network agent Web UI alone : ./install.sh -n dashboard
 - To regenerate the network agent runtime with the same environment setup: ./install.sh -k; ./install.sh -s
 - To recreate a complete environment and runtime from scratch: ./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s

```

## Installation Workflows

### Simple Installation (Recommended)

For most users, the comprehensive installation is the easiest approach:

```bash
# Set your environment variables first (see Environment Setup section above)
./install.sh --all -y
```

### Step-by-Step Installation

If you prefer more control over the installation process:

1. **Create the environment configuration:**
   ```bash
   ./install.sh -c
   ```

2. **Start the GCP services (VPCs, GKE Cluster, Network Agent K8s operator, Git repos, etc.):**
   ```bash
   ./install.sh -s
   ```

3. **Deploy all Network Agents and Dashboard:**
   ```bash
   ./install.sh -n all
   ```

### Selective Agent Deployment

You can deploy specific agents individually:

```bash
# Deploy only the dashboard and operations agent
./install.sh -n dashboard,operations

# Deploy network tools
./install.sh -n networktools

# Deploy all agents
./install.sh -n all
```

### Automated Installation

For CI/CD or automated deployments, use the confirmation flags:

```bash
# Answer 'yes' to all prompts automatically
./install.sh --all -y

# Answer 'no' to all prompts (skip optional steps)
./install.sh --all -N
```

## Environment Management

### Viewing Environment Information

```bash
# Display current GCP environment details
./install.sh -g

# Display demo information and URLs
./install.sh -i
```

### Rebuilding Components

```bash
# Rebuild and redeploy the operator only
./install.sh -o # or --deploy operator


# Rebuild and redeploy log capture function
./install.sh -l # or --deploy logcapture

# Rebuild and redeploy all components

# Regenerate the runtime with same environment setup
./install.sh -k; ./install.sh -s
```

### Clean Up

```bash
# Stop and delete runtime resources (keeps environment config)
./install.sh -k

# Delete environment configuration (keys, manifests)
./install.sh -d

# Complete cleanup (runtime + environment)
./install.sh -k; ./install.sh -d
```

## Troubleshooting

### Common Issues

1. **Permission Errors**: Ensure your GCP user has Owner role on the project and all organization policies are correctly set.

2. **Environment Variables**: The script will validate all required environment variables and provide clear error messages if any are missing.

3. **Network Connectivity**: If building the VNF image fails with SSH errors, ensure you can connect to GCP from your network (run `gcert` if on Google corporate network).

4. **Resource Quotas**: Ensure your GCP project has sufficient quotas for compute instances, GKE clusters, and other resources.

### Getting Help

- Use `./install.sh -g` to check your current environment configuration
- Use `./install.sh -i` to see all deployed service URLs
- Check the script output for specific error messages and suggested fixes

## What Gets Deployed

The complete installation creates:

- **GKE Cluster**: Kubernetes cluster with Config Connector and operators
- **Network Infrastructure**: VPCs, subnets, firewall rules, and NAT gateways  
- **Database**: Spanner instance for network topology storage
- **Agents**: Multiple specialized network agents (supervisor, engineer, operations, tester, logs, incident)
- **Dashboard**: Web-based network management interface
- **Git Repository**: Gitea server for network configuration management
- **Monitoring**: Log capture and processing functions

All services are deployed to Google Cloud Run for scalability and cost efficiency.
