# L3VPN Fault Injection for GNN Root Cause Analysis

This document describes the fault injection strategy for the L3VPN hub-spoke network to demonstrate GNN embedding-based root cause analysis.

## Overview

The L3VPN hub-spoke network provides five advanced fault injection scenarios that introduce misconfigurations or hardware-level issues in the VyOS router backbone. These faults are designed to be explicitly detectable through Graph Neural Network (GNN) embeddings stored in Spanner, specifically targeting different GNN architectures (STGNN, D-GAT, and HetGNN) allowing operators to trace the root cause of network issues using temporal, directed, and heterogeneous graph-based analysis.

## Network Architecture

### Hub-Spoke Topology
- **Provider Core (AS 65001)**: P1, P2, P3, P4 and Route Reflectors RR1, RR2.
- **Provider Edge (Hub)**: PE2 (Cambridge) - Aggregates traffic from all spokes
- **Provider Edge (Spokes)**: 
  - PE1 (Oxford) - Connected to CE1-spoke (Sheffield)
  - PE3 (Brighton) - Connected to CE2-spoke (Liverpool)

### Route Target Configuration (Correct)
- **Spokes (PE1, PE3)**: 
  - Export: `65035:1011` (spoke routes)
  - Import: `65035:1030` (hub routes)
- **Hub (PE2)**:
  - Export: `65035:1030` (hub routes)
  - Import: `65035:1011`, `65035:1030` (both spoke and hub routes)

## Fault Variants

### Fault 1: Silent Drop via MTU Mismatch (PE1 Uplink)

**File**: `l3vpn-hub-spoke-fault1-mtu.yaml`

**Misconfiguration**:
- **Location**: PE1 `eth1` (facing P1)
- **Change**: MTU changed to `1400` from `1500`.

**Impact**:
- **Severity**: Intermittent file transfer failures
- **Symptom**: VPNv4 control plane is healthy, OSPF is full, but large BGP UPDATEs and payload packets > 1400 bytes are silently aggregated and dropped.
- **Affected Traffic**: Customer file transfers from `dev1` exceeding 1400 bytes fail in one direction (PE1 → P1).

**GNN Detection Signature**:
- **D-GAT**: Flags high `drop_asymmetry` on the directed edge PE1(`eth1`) → P1(`eth3`).
- **STGNN**: Correlates step change in `tx_drops` on PE1 `eth1` with degraded VPNv4 prefix metrics.
- **HetGNN**: Isolates deviation to the `Interface` sub-embedding of PE1 `eth1` resulting directly from config drift on the MTU value.

### Fault 2: Hub CE Session Teardown

**File**: `l3vpn-hub-spoke-fault2-ce-down.yaml`

**Misconfiguration**:
- **Location**: PE2 router
- **Change**: eBGP session between PE2 and CE1-HUB is shut down (`delete protocols bgp vrf BLUE_HUB neighbor 10.80.80.2`).

**Impact**:
- **Severity**: Total spoke-to-spoke blackout.
- **Symptom**: PE2 withdraws customer routes. Spoke VRFs (`BLUE_SPOKE`) lose all imported routes.
- **Affected Traffic**: Spoke-to-spoke routing fails instantly.

**GNN Detection Signature**:
- **HetGNN**: `BGP_Session` sub-node for PE2↔CE1-HUB flips to `Established=0`.
- **STGNN**: Detects simultaneous collapse of `bgp_pfx_rcvd` on Spoke VRFs.
- **D-GAT**: Pinpoints PE2 as the origin node whose outgoing VPNv4 advertisement edges have collapsed.

### Fault 3: RR1 BGP Process Crash

**File**: `l3vpn-hub-spoke-fault3-rr1-crash.yaml`

**Misconfiguration**:
- **Location**: RR1
- **Change**: BGP process stopped or RR1's loopback disabled.

**Impact**:
- **Severity**: Route Reflection Instability.
- **Symptom**: All PE-to-RR1 BGP sessions drop. VPNv4 routes reflect via RR2, but reconvergence takes up to 90 seconds. If flapping, continuous churn occurs.
- **Affected Traffic**: Partial traffic black-holing on selective prefixes until backup RR2 covers all paths.

**GNN Detection Signature**:
- **STGNN**: Sees correlated `bgp_uptime` drop on PE1, PE2, and PE3 identically timed.
- **D-GAT**: Detects all inward-facing `PeersWith` edges around RR1 collapsing simultaneously.
- **HetGNN**: Multi-node isolation: highlights only RR1-facing BGP sessions failing while RR2-facing ones remain perfectly normal.

### Fault 4: Wrong Import RT on PE3

**File**: `l3vpn-hub-spoke-fault4-rt-import.yaml`

**Misconfiguration**:
- **Location**: PE3 `BLUE_SPOKE` VRF
- **Change**: `vrf BLUE_SPOKE` import route-target changed to `65035:9999` from `65035:1030`.

**Impact**:
- **Severity**: One-way reachability (Spoke2 silent isolation).
- **Symptom**: PE3 rejects all Hub routes. `dev2` cannot communicate outward, but incoming traffic from Hub still forwards to it until return routes fail.
- **Affected Traffic**: `dev2` isolated symmetrically but standard pings may appear one-way.

**GNN Detection Signature**:
- **HetGNN**: Config branch correctly identifies anomalous hash vector for `vrf_rt_import` value `[65035:9999]`.
- **D-GAT**: Detects asymmetric reachability, CE2 advertises properly but Hub→PE3 imports exactly zero.

### Fault 5: Degrading SFP on P1-P3 Link

**File**: Simulated via `tc` traffic manipulation on PE physical link. 

**Misconfiguration**:
- **Location**: P1 `eth2` (facing P3)
- **Change**: Progressive injection of CRC errors via traffic-control to simulate failing optical SFP.

**Impact**:
- **Severity**: Slow hardware failure leading to routing flaps.
- **Symptom**: Unnoticed errors for ~30m, transitioning to OSPF LSA and LDP drops over 55m until absolute link failure. 
- **Affected Traffic**: Slight retransmissions, microburst packet drops, eventually full convergence path switch.

**GNN Detection Signature**:
- **STGNN**: Observes an accelerating "Trajectory of Health" vector drift on `rx_errors_acceleration` 35+ minutes prior to terminal failure.
- **HetGNN**: Explicitly targets the hardware layer tracking `Interface Metric`, keeping the Config sub-embedding normal.
- **D-GAT**: Explains source hardware fault by mapping the directional nature of P1 TX corruption.

## How to Use

*(The standard Kubernetes applies and Spanner Inference steps remain the same, substitute specific fault YAMLs into `kubectl apply -f` commands)*
