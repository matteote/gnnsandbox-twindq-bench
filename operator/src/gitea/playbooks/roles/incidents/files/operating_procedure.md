# 5G Virtual Network Operating Procedure

## Troubleshooting and Problem Resolution Guide

This operating procedure provides a systematic approach to identifying, diagnosing, and resolving problems in the virtual 5G network deployed by the NetworkAgent project.

## 1. Network Architecture Overview

The virtual 5g network architecture contains a set of network services. Network services are composed of virtual cloud infrastructure and networking software. Each network service lifecycle is managed by a custom resource in kubernetes. 

Network services are modelled in kubernetes as a hierarchy of custom resources. Network service instances have child k8s resource instances managing virtual compute (hosting the network software) and networking (connecting to other network locations and services). Kubernetes manages the lifecycle of the entire network service hierarchy. 

The following network services can be deployed:

- **Core Network Services**: Free5gc-based 5G Core network services that can be deployed are as follows:
  - Control Plane Network Service. Kubernetes Kind = ControlPlane
  - User Plane Function Network Service. Kubernetes Kind = UserPlaneFunction
  - Data Network. Kubernetes Kind = DataNetwork
- **Radio Sites**: UERANSIM gNB Radio Network Simulator. Kubernetes Kind = UERanSIM
- **VPN Services**: Wireguard network service instances can be deployed to deliver mesh or point-to-point connectivity network services. Kubernetes Kinds = WireguardAppliance, MeshService and PointToPointService respectively

Network service instances can have other network services as children (e.g. mesh/ptp have wireguardappliances as children) or children virtual infrastructure such as Compute (k8s kind = ComputeInstance), networks (k8s kind = ComputeNetwork), subnetworks (k8s kind = ComputeSubnetwork), and IP addresses (k8s kind = ComputeAddress)

## 2. Incident Workflow

For each incident follow the steps below to investigate and attempt resolution.

### Step 1: Define Investigation Strategy

Identify a list of network service instances to investigate. Connected network services to the originating network services may be the root cause of the issue. 

Use the patterns below to figure out which network services connected to the originating network service to to investigate:
- For User traffic connection errors, check all network service instances between the affected radio site and the target DNN the UE trying to send traffic to.
- For Radio errors, check the Control Plane for any clues or indication of what could cause the error.
- For other network service errors, check components that are 1 hop away to see if this issue is affecting nearby connected network services.

### Step 2: Investigate Root Cause

The error reported will contain the name of the ComputeInstance it orignated from. The ComputeInstance name can be used to determine its network service parent and this can in turn be used to investigate further infrastructure or software details, e.g. IP address/CIDR or infrastructure status.

Work through the network service troubleshooting procedures below for each network services identified in the investigation strategy earlier. Identify the node and issue causing the incident. 

### Step 3: Implement incident resolution

Use troubleshooting procedures to test for resolution to common known issues. Also use past post mortems to identify resolutions that worked for similar incidents. 

### Step 4: Verify resolution

Run available tests to validate the network services are operational. 

## 3 Network Service Troubleshooting Procedures

In general check the status of the network service instances to investigate along with their children compute, network and address components. 

You should ignore 'UpdateFailed' status messages on ComputeInstances. This is because updates have been disabled, it is not an error. 

### 3.1 Control Plane Problems

**Symptoms:**
- UE registration failures
- Session establishment failures
- Authentication errors

**Troubleshooting Steps:**
1. Use Logs Agent to analyze control plane logs
3. Verify core network connectivity by running ping tests from a radio simulator site

**Common Resolutions:**
- Restart Free5gc control plane services
- Check the UE authentication credentials attached to the UERanSIM are correct

### 3.2 Radio Site Issues

**Symptoms:**
- Radio link establishment failures, e.g. nr-gnb is not running on host
- Site-specific performance issues

**Troubleshooting Steps:**
1. Check UERANSIM gNB is running. You can check if the uesimtun0 network interface is included in the performance metrics for the UERanSIM ComputeInstance. This will be there is the gNB software is running.
2. Verify cellsite network connectivity by running a ping test from the UERanSIM ComputeInstance to the control plane network service

**Common Resolutions:**
- Restart UERANSIM gNB VNF

### 3.5 VPN Connectivity Issues

**Symptoms:**
- UE or User connection failures, e.g. "URL is not accessible - connection failed" type errors
- Inter-site connectivity failures
- Mesh connectivity degradation
- Point-to-point link failures

**Troubleshooting Steps:**
2. Verify wg0 network interface exists and is active by querying the network performance statistics for this wireguard appliance. 
5. Analyze logs

**Common Resolutions:**
- Restart Wireguard Network Service

*This document should be reviewed and updated quarterly or after any major network changes.*
