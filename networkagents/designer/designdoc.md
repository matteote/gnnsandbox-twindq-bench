# Network Design Guide

This document describes the architectural design principles for deploying a VyOS L3VPN network. It provides enough knowledge for an agent to take high-level network lifecycle instructions and decompose them into a concrete network design expressed as Kubernetes Custom Resources (CRDs).

---

## Table of Contents

1. [Design Process Overview](#design-process-overview)
2. [L3VPN Layered Architecture](#l3vpn-layered-architecture)
3. [Router Types and Roles](#router-types-and-roles)
4. [VPN Topology Patterns](#vpn-topology-patterns)
5. [IP Addressing Strategy](#ip-addressing-strategy)
6. [VRF and Route Target Design](#vrf-and-route-target-design)
7. [Assembling a Network Design](#assembling-a-network-design)
8. [CRD Reference and Dependency Chain](#crd-reference-and-dependency-chain)
9. [Design Rules and Constraints](#design-rules-and-constraints)
10. [Example: Hub-and-Spoke L3VPN](#example-hub-and-spoke-l3vpn)
11. [Querying Current Topology from Spanner](#querying-current-topology-from-spanner)

---

## Design Process Overview

An agent receiving a high-level natural language request (e.g., "connect three UK branch offices to a central hub over a private VPN") must follow this decomposition process:

```
High-Level Intent
      │
      ▼
1. Identify sites, roles, and connectivity requirements
      │
      ▼
2. Choose VPN topology (hub-and-spoke, any-to-any)
      │
      ▼
3. Assign router roles (P, PE, RR, CE) and count
      │
      ▼
4. Design physical topology and IP address plan
      │
      ▼
5. Define underlay protocols (OSPF areas, BGP AS, MPLS)
      │
      ▼
6. Define VRF, Route Distinguisher and Route Target plan
      │
      ▼
7. Emit CRD YAML: VyOSInfrastructure → VyOSUnderlay → VyOSL3VPN → Device
```

At each step, consult the current topology stored in Spanner to check which routers and networks already exist before adding new resources. Never duplicate a router or network name that already exists in the live topology.

---

## L3VPN Layered Architecture

The network is built in three clearly separated layers. Each layer has its own CRD and its own lifecycle.

```
┌──────────────────────────────────────────────┐
│  Customer / Overlay Layer (VRFs, BGP VPNv4)  │  ← VyOSL3VPN CRD
│  Route Distinguishers, Route Targets, VRFs   │
├──────────────────────────────────────────────┤
│  Underlay / Core Layer (OSPF + MPLS + iBGP)  │  ← VyOSUnderlay CRD
│  P routers, RR routers, LSPs via LDP         │
├──────────────────────────────────────────────┤
│  Physical / Infrastructure Layer             │  ← VyOSInfrastructure CRD
│  Routers, Links, Interfaces, IP addresses    │
└──────────────────────────────────────────────┘
```

### Layer 1 – Physical Infrastructure

Defines all routers and the point-to-point or multi-access networks connecting them. This is the only layer that knows about hardware (or virtual hardware) — container images, port assignments, IP addresses on links, MTU, VLAN IDs, and physical location.

**Key concepts:**
- Every router gets a unique loopback IP (e.g., `10.0.0.x/32`) used as its stable router ID.
- Point-to-point (P2P) links use `/24` subnets in the `172.16.x.0/24` range (only `.1` and `.2` are used).
- The management network (`192.168.122.0/24`) connects `eth0` of every router for out-of-band access.
- Each P2P link is assigned a unique VLAN ID (301+) to isolate traffic on Linux bridges.

### Layer 2 – Underlay Routing

Configures OSPF (for link-state flooding and loopback reachability), LDP/MPLS (for label-switched paths between PEs), and iBGP (for distributing VPNv4 routes between PEs via Route Reflectors).

**Key concepts:**
- OSPF backbone area `0.0.0.0` is used for all P and PE routers. All loopbacks must be reachable via OSPF before MPLS can signal LSPs.
- LDP uses the loopback interface as its router ID and is enabled on all P-to-P and P-to-PE links. It is **not** enabled on PE-to-CE links.
- iBGP runs in a single AS (e.g., `65001`). All PEs peer only with Route Reflectors — never directly with each other.
- Route Reflectors do not participate in MPLS data-plane forwarding; they only reflect VPNv4 routes.

### Layer 3 – L3VPN Overlay

Configures VRFs on PE routers, assigns customer-facing interfaces to VRFs, and sets up eBGP sessions between each PE and its attached CE router. MP-BGP carries VPNv4 prefixes (customer routes + RD/RT labels) between PEs via the Route Reflectors.

**Key concepts:**
- Each customer site is represented by a VRF on its PE router.
- Route Distinguishers (RD) make customer routes globally unique across PEs.
- Route Targets (RT) control which VRFs import which routes, implementing the hub-and-spoke or any-to-any policy.
- CE routers run eBGP in a customer AS (e.g., `65035`) and peer with the PE's VRF interface IP.

---

## Router Types and Roles

### P – Provider Core Router

**Role:** MPLS label forwarding only. Carries customer traffic inside LSPs without inspecting the IP payload.

**Protocols required:**
- OSPF (backbone area, all P-to-P interfaces)
- MPLS/LDP (all P-to-P interfaces)

**Protocols NOT required:**
- BGP (P routers do not run BGP)
- VRFs

**Interface pattern:**
- `eth0` → management network
- `eth1`, `eth2`, … → P-to-P links to other P routers or PE/RR routers
- `lo` → loopback network (router ID)

**When to add more P routers:** Add P routers to increase core capacity or redundancy. A minimal core needs at least one P router; for resilience design two parallel P-router paths between any PE pair.

---

### PE – Provider Edge Router

**Role:** L3VPN service delivery. Maintains VRFs for customer traffic, runs VPNv4 MP-BGP with RRs, and eBGP with attached CE routers.

**Protocols required:**
- OSPF (backbone area, P-facing interfaces only)
- MPLS/LDP (P-facing interfaces only — **not** CE-facing interfaces)
- iBGP (peers with Route Reflectors only, using loopback addresses)
- eBGP per VRF (peers with CE router)

**Protocols NOT required:**
- LDP on CE-facing interfaces

**Interface pattern:**
- `eth0` → management network
- `eth1` (or `eth1`/`eth2`) → P-to-PE link(s) into the core
- `eth2` (or next available) → PE-to-CE link (customer-facing, inside a VRF)
- `lo` → loopback network (router ID)

**Rule:** A PE must connect to **at least one** P router in the core. For redundancy, connect to two different P routers on different paths through the core.

---

### RR – Route Reflector

**Role:** iBGP route reflection. Receives VPNv4 routes from PE clients and re-advertises them to all other PE clients, eliminating the need for a full iBGP mesh.

**Protocols required:**
- OSPF (backbone area, links to P routers)
- MPLS/LDP (links to P routers — needed for loopback reachability via the MPLS core)
- iBGP with `route_reflector: true`, listing all PE routers as `route_reflector_client: true`

**Protocols NOT required:**
- VRFs
- eBGP

**Interface pattern:**
- `eth0` → management network
- `eth1`, `eth2` → links to P routers (for OSPF/LDP reachability)
- `lo` → loopback network (router ID)

**Sizing rule:** Deploy at least **two RRs** for redundancy. Every PE must peer with **all** RRs. RRs do not need to peer with each other; they are peers of the PE clients only.

---

### CE – Customer Edge Router

**Role:** Customer premises router. Connects the customer LAN to the provider PE over an eBGP session. The CE is aware only of the customer's own AS and the routes the VPN policy allows.

**Protocols required:**
- eBGP (peers with PE's VRF interface IP)

**Protocols NOT required:**
- OSPF
- MPLS
- VRFs

**Interface pattern:**
- `eth0` → management network
- `eth1` → CE-to-PE link (provider-facing, eBGP peer)
- `eth2` → LAN / customer site network
- `lo` → loopback network

**Rule:** CE router's BGP AS must be **different** from the provider iBGP AS. A common pattern is `65001` for provider iBGP and `65035` for all CE routers.

---

## VPN Topology Patterns

### Hub-and-Spoke

The most common telco/enterprise pattern. One site (hub) can communicate with all spoke sites; spoke sites communicate **only** through the hub — they cannot reach each other directly.

**Use when:** Central services, internet breakout, security inspection, or regulatory compliance require all traffic to pass through a central point.

**VRF policy:**
| Role | VRF Name | RT Export | RT Import |
|------|----------|-----------|-----------|
| Hub PE | `BLUE_HUB` | `65035:1030` | `65035:1011`, `65035:1030` |
| Spoke PE | `BLUE_SPOKE` | `65035:1011` | `65035:1030` |

- The hub imports its **own** export target (self-import of `65035:1030`) to allow spoke-to-spoke traffic to hairpin through the hub.
- Spoke PEs import only the hub's routes (`65035:1030`) — they cannot see each other's prefixes directly.

### Any-to-Any (Full Mesh)

All sites can communicate with all other sites directly.

**Use when:** Latency-sensitive peer-to-peer applications (e.g., distributed databases, real-time collaboration).

**VRF policy:** All PE VRFs share a single RT — all export and all import the same community. Example: `RT export: 65035:1000`, `RT import: 65035:1000`.

### Choosing a Topology

| Requirement | Recommended Topology |
|-------------|---------------------|
| Internet/security hub | Hub-and-Spoke |
| Centralised application server | Hub-and-Spoke |
| Branch-to-branch collaboration | Any-to-Any |
| Mixed: hub services + branch direct | Hub-and-Spoke with selected spoke import overrides |

---

## IP Addressing Strategy

Use a disciplined allocation plan. The reference lab uses these ranges — maintain the same scheme for consistency:

| Purpose | Range | Example |
|---------|-------|---------|
| Loopbacks (Router IDs) | `10.0.0.0/24` | `10.0.0.1` – `10.0.0.254` |
| P-to-P core links | `172.16.x.0/24` | `172.16.30.0/24` – `172.16.140.0/24` |
| PE-to-CE links | `10.50–90.x.0/24` | `10.50.50.0/24` (PE1↔CE1) |
| Customer LANs | `10.100.x.0/24` | `10.100.1.0/24` (spoke1 LAN) |
| Management | `192.168.122.0/24` | `192.168.122.11` – `.254` |

**Loopback allocation rules:**
- RR routers: start at `10.0.0.1` (e.g., rr1=`.1`, rr2=`.2`)
- P routers: start at `10.0.0.3` (e.g., p1=`.3`, p2=`.4`, p3=`.5`, p4=`.6`)
- PE routers: start at `10.0.0.7` (e.g., pe1=`.7`, pe2=`.8`, pe3=`.10`)
- CE routers: start at `10.0.0.80` (e.g., ce1-spoke=`.80`, ce2-spoke=`.90`, ce1-hub=`.100`)

**VLAN allocation rules:**
- Assign one unique VLAN per P2P network segment starting at VLAN 301.
- PE-to-CE links start at VLAN 401.
- Management network has no VLAN tag.
- Loopback network has no VLAN tag.

**BGP AS numbers:**
- Provider iBGP: `65001`
- Customer eBGP (all CEs): `65035`

---

## VRF and Route Target Design

### Route Distinguisher (RD)

Every VRF on every PE must have a **globally unique** RD. Use the convention:

```
<pe-loopback-ip>:<service-id>
```

Example: PE1 (`10.0.0.7`) hosting BLUE_SPOKE with service ID `1011`:
```
rd: "10.50.50.1:1011"   # Use the PE-to-CE interface IP, not loopback
```

The PE-to-CE link IP is used as the RD IP component (not the loopback) to ensure uniqueness when a PE hosts multiple VRFs for the same customer.

### Route Target (RT)

Route Targets implement the traffic policy between sites. Use the convention:

```
<customer-as>:<policy-id>
```

| Policy | RT Value | Purpose |
|--------|----------|---------|
| Spoke export | `65035:1011` | Routes exported by spoke sites |
| Hub export | `65035:1030` | Routes exported by hub site |

**Hub VRF RT configuration:**
```
rt_export: ["65035:1030"]
rt_import: ["65035:1011", "65035:1030"]
```

**Spoke VRF RT configuration:**
```
rt_export: ["65035:1011"]
rt_import: ["65035:1030"]
```

### VRF Table IDs

Each VRF requires a unique Linux routing table ID. Use:
- Spoke VRFs: `200`
- Hub VRFs: `400`
- Additional VRFs: increment by 100

---

## Assembling a Network Design

Follow these steps to translate a customer intent into a complete set of CRDs.

### Step 1: Identify Sites and Roles

From the customer description, extract:
- Number of sites
- Which site is the hub (central services, security)
- Which sites are spokes (branches, remote offices)
- Geographic locations (for Spanner metadata)

### Step 2: Map Sites to Router Roles

For each site:
- One **CE** router per customer site (speaks eBGP to its PE)
- One **PE** router per customer site attachment point (may serve multiple customers)
- Shared **P** routers in the core (typically 2–4 for resilience)
- Shared **RR** routers (exactly 2 for redundancy)

**Minimum viable topology:** 1 P + 2 RR + 2 PE + 2 CE (one hub, one spoke)

**Production resilient topology:** 4 P + 2 RR + N PE + N CE

### Step 3: Plan Physical Links

Every router pair that needs connectivity requires a dedicated P2P network entry in `VyOSInfrastructure.spec.networks`.

Required links:
- P↔P: form the core mesh
- P↔RR: for RR loopback reachability via OSPF/LDP
- P↔PE: attach PE to core
- PE↔CE: customer-facing link (outside MPLS domain)

Each CE also needs a LAN network (`network_type: multi-access`) for attached Devices.

### Step 4: Assign Interface Names

Interface naming is sequential per router:
- `eth0` → always management
- `eth1` → first P2P link (usually into the core for PE/RR; first P neighbor for P)
- `eth2`, `eth3`, … → additional links in the order they appear in `spec.routers[].interfaces`
- `lo` → always loopback

Interface names in `connected_routers` must exactly match the names in `spec.routers[].interfaces`.

### Step 5: Write the VyOSInfrastructure

Declare all networks and all routers. The infrastructure CRD is self-contained — it does not reference underlay protocols.

### Step 6: Write the VyOSUnderlay

Reference the infrastructure with `infrastructureRef`. Configure OSPF and LDP on all P, PE, and RR routers. Configure iBGP on RR routers (as reflectors) and PE routers (as RR clients). CE routers are **not** listed in the underlay — they have no OSPF/MPLS.

### Step 7: Write the VyOSL3VPN

Reference the underlay with `underlayRef`. For each PE router, declare its VRF(s) with RD, RT import/export, the CE-facing interface, and the eBGP neighbour entry.

### Step 8: Write Device Resources

For each customer site, create a `Device` attached to the CE's LAN network with a static IP and the CE's LAN interface IP as its gateway.

---

## CRD Reference and Dependency Chain

Resources must be applied and reach `Ready` in this strict order:

```
VyOSInfrastructure  (kind: VyOSInfrastructure, group: google.dev/v1)
        │   generates VyOSRouter and LinuxNetwork children automatically
        ▼
VyOSUnderlay        (kind: VyOSUnderlay, group: google.dev/v1)
        │   spec.infrastructureRef → VyOSInfrastructure name
        ▼
VyOSL3VPN           (kind: VyOSL3VPN, group: google.dev/v1)
        │   spec.underlayRef → VyOSUnderlay name
        ▼
Device              (kind: Device, group: google.dev/v1)
        │   spec.network_name → LinuxNetwork (generated by VyOSInfrastructure)
        ▼
TrafficTest         (kind: TrafficTest, group: google.dev/v1)
        │   spec.source_devices / spec.destination_device → Device names
```

The operator enforces these dependencies automatically. A resource will wait (retrying every 10 seconds) until its parent is `Ready`.

### VyOSInfrastructure Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `spec.networks[].name` | string | Unique network segment name |
| `spec.networks[].subnet` | CIDR | IPv4 subnet for the segment |
| `spec.networks[].vlan` | int (1-4094) | 802.1Q VLAN ID for isolation |
| `spec.networks[].network_type` | enum | `p2p`, `multi-access`, `management`, `loopback` |
| `spec.networks[].mtu` | int (68-9000) | MTU, default 1500 |
| `spec.networks[].connected_routers[].router_name` | string | Must match a router name in `spec.routers` |
| `spec.networks[].connected_routers[].interface` | `eth[0-9]+` | Interface name on the router |
| `spec.networks[].connected_routers[].ip_address` | IPv4 | Host IP on this segment (no mask) |
| `spec.routers[].name` | string | Unique router name |
| `spec.routers[].router_id` | IPv4 | Loopback/router ID address |
| `spec.routers[].role` | enum | `P`, `PE`, `CE`, `RR` |
| `spec.routers[].location` | object | `latitude`, `longitude`, `city`, `country`, `site` |
| `spec.routers[].interfaces[].name` | `eth[0-9]+` or `lo` | Interface name |
| `spec.routers[].interfaces[].network` | string | Must match a network name in `spec.networks` |

### VyOSUnderlay Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `spec.infrastructureRef` | string | Name of the VyOSInfrastructure |
| `spec.routing.ospf.router_id_source` | string | `loopback` (use loopback IP as OSPF router ID) |
| `spec.routing.ospf.areas[]` | array | Global area definitions (`area_id`, `type`) |
| `spec.routing.bgp.as_number` | int | Provider iBGP AS number |
| `spec.routing.bgp.route_reflectors[]` | string[] | Loopback IPs of RR routers |
| `spec.mpls.enabled` | bool | Enable MPLS globally |
| `spec.mpls.ldp.router_id_interface` | string | `loopback` |
| `spec.routers[].name` | string | Must match a router in the infrastructure |
| `spec.routers[].protocols.ospf.router_id` | IPv4 | Router's loopback IP |
| `spec.routers[].protocols.ospf.areas[]` | array | `area` (dotted-quad), `type` |
| `spec.routers[].protocols.bgp.as_number` | int | BGP AS for this router |
| `spec.routers[].protocols.bgp.router_id` | IPv4 | Router's loopback IP |
| `spec.routers[].protocols.bgp.route_reflector` | bool | `true` for RR routers |
| `spec.routers[].protocols.bgp.neighbors[]` | array | `peer` (loopback IP), `remote_as`, `route_reflector_client` |
| `spec.routers[].protocols.mpls.enabled` | bool | Enable MPLS on this router |
| `spec.routers[].protocols.mpls.ldp.router_id` | IPv4 | Router's loopback IP |
| `spec.routers[].protocols.mpls.ldp.interfaces[]` | string[] | Interfaces to enable LDP on (P-facing only) |

### VyOSL3VPN Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `spec.underlayRef` | string | Name of the VyOSUnderlay |
| `spec.services[].name` | string | Service/VRF template name (e.g., `BLUE_HUB`) |
| `spec.services[].topology` | enum | `hub`, `spoke`, `any-to-any` |
| `spec.routers[].name` | string | PE router name |
| `spec.routers[].vrfs[].name` | string | VRF name |
| `spec.routers[].vrfs[].table` | int | Linux routing table ID |
| `spec.routers[].vrfs[].rd` | string | Route Distinguisher (`ip:id`) |
| `spec.routers[].vrfs[].rt_export[]` | string[] | Exported route targets |
| `spec.routers[].vrfs[].rt_import[]` | string[] | Imported route targets |
| `spec.routers[].vrfs[].interfaces[]` | string[] | CE-facing interface names to place in this VRF |
| `spec.routers[].bgp.vrfs[].name` | string | VRF name for per-VRF BGP |
| `spec.routers[].bgp.vrfs[].neighbors[]` | array | `peer` (CE IP), `remote_as` (CE AS) |

---

## Design Rules and Constraints

These rules must always be satisfied in a valid design. Validate against them before emitting CRDs.

### Physical / Infrastructure Rules

1. Every router must have exactly one `eth0` interface connected to the `management` network.
2. Every router must have exactly one `lo` interface connected to the `loopback` network.
3. Every P2P network must have exactly **two** `connected_routers` entries.
4. A `multi-access` LAN network must have exactly **one** connected router (the CE gateway interface).
5. Interface names must match between `spec.networks[].connected_routers[].interface` and the router's `spec.routers[].interfaces[].name`.
6. Each P2P network must have a unique VLAN ID. VLAN IDs must not overlap across any networks in the same infrastructure.
7. No two routers may share the same `router_id` (loopback IP).
8. No two `connected_routers` entries in the same network may share the same IP address.
9. Router names must be globally unique within the infrastructure.

### Underlay Rules

10. Only P, PE, and RR routers are listed in `VyOSUnderlay.spec.routers`. CE routers are excluded.
11. OSPF must be enabled on all P, PE, and RR routers with area `0.0.0.0`.
12. LDP `interfaces` for each router must list only interfaces connected to P2P core links (never the management `eth0`, CE-facing interfaces, or `lo`).
13. Every PE router must have at least one BGP neighbor entry pointing to an RR loopback IP with `remote_as` equal to the provider AS.
14. Every RR router must list **all** PE router loopback IPs as `route_reflector_client: true` neighbors.
15. The `route_reflectors` list in `spec.routing.bgp` must contain the loopback IPs of all RR routers.

### L3VPN / VRF Rules

16. Only PE routers are listed in `VyOSL3VPN.spec.routers`.
17. Every VRF `rd` must be unique across all VRFs in all PE routers.
18. Hub VRFs must import **both** spoke RT and hub RT (to enable spoke-to-spoke hairpin via the hub).
19. Spoke VRFs must import **only** hub RT (spokes cannot see each other directly).
20. The `interfaces` list in a VRF must reference the PE's CE-facing interface (the one connected to the PE-to-CE network), not any core-facing interface.
21. The BGP neighbor `peer` IP in a VRF must be the CE's IP on the PE-to-CE link.
22. The BGP neighbor `remote_as` in the L3VPN spec must match the CE router's AS number declared in the underlay.

### Addressing Rules

23. All loopback IPs (`10.0.0.x`) must be unique across all routers.
24. All P2P link subnets (`172.16.x.0/24`) must not overlap.
25. All PE-to-CE link subnets (`10.x.x.0/24`) must not overlap with each other or with the core.
26. All customer LAN subnets (`10.100.x.0/24`) must not overlap.
27. Management IPs (`192.168.122.x`) must be unique per router.
28. The `ip_address` in `connected_routers` must fall within the network's declared `subnet`.

---

## Example: Hub-and-Spoke L3VPN

The canonical example is the `l3vpn-hub-spoke` topology in `environment/telco-lab/l3vpn-hub-spoke.yaml`. It provides a full working reference. Key design decisions made in this example:

### Topology Summary

```
         [ce1-spoke]──(10.50.50.0/24)──[pe1]
                                          │
                           (172.16.90.0/24 via p1)
                                          │
[ce2-spoke]──(10.60.60.0/24)──[pe3]    [p1]──[p2]──[rr1]
                                  │      │
                        (p4-pe3)  │      │(p1-p3)
                               [p4]──[p3]──[rr2]
                                          │
                           (p3-pe2 / p1-pe2)
                                          │
                                        [pe2]
                                          │
                               (10.80.80.0/24)
                                          │
                                     [ce1-hub]
```

### Router Role Assignments

| Router | Role | Loopback | Connected To |
|--------|------|----------|--------------|
| p1 | P | 10.0.0.3 | p2, p3, rr1, pe1, pe2 |
| p2 | P | 10.0.0.4 | p1, p4, rr1 |
| p3 | P | 10.0.0.5 | p1, p4, pe2, rr2 |
| p4 | P | 10.0.0.6 | p2, p3, pe3, rr2 |
| rr1 | RR | 10.0.0.1 | p1, p2 |
| rr2 | RR | 10.0.0.2 | p3, p4 |
| pe1 | PE | 10.0.0.7 | p1, ce1-spoke |
| pe2 | PE | 10.0.0.8 | p1, p3, ce1-hub |
| pe3 | PE | 10.0.0.10 | p4, ce2-spoke |
| ce1-spoke | CE | 10.0.0.80 | pe1 |
| ce1-hub | CE | 10.0.0.100 | pe2 |
| ce2-spoke | CE | 10.0.0.90 | pe3 |

### VRF Design

| PE | VRF | Topology | RD | RT Export | RT Import |
|----|-----|----------|----|-----------|-----------|
| pe1 | BLUE_SPOKE | spoke | 10.50.50.1:1011 | 65035:1011 | 65035:1030 |
| pe2 | BLUE_HUB | hub | 10.80.80.1:1011 | 65035:1030 | 65035:1011, 65035:1030 |
| pe3 | BLUE_SPOKE | spoke | 10.60.60.1:1011 | 65035:1011 | 65035:1030 |

### CRD Skeleton

```yaml
# Layer 1: Physical topology
apiVersion: google.dev/v1
kind: VyOSInfrastructure
metadata:
  name: <infra-name>
  namespace: default
spec:
  networks:
    # Core P2P links (one entry per link pair)
    - name: p1-p2
      subnet: "172.16.30.0/24"
      vlan: 301
      network_type: "p2p"
      connected_routers:
        - router_name: "p1"
          interface: "eth1"
          ip_address: "172.16.30.1"
        - router_name: "p2"
          interface: "eth1"
          ip_address: "172.16.30.2"
    # ... (more P2P links) ...
    # Management network
    - name: mgmt
      subnet: "192.168.122.0/24"
      gateway: "192.168.122.1"
      network_type: "management"
      connected_routers:
        - router_name: "p1"
          interface: "eth0"
          ip_address: "192.168.122.11"
        # ... (all routers) ...
    # Loopback network (no connected_routers; loopbacks are auto-assigned)
    - name: loopbacks
      subnet: "10.0.0.0/24"
      network_type: "loopback"
    # PE-to-CE links
    - name: pe1-ce1-spoke
      subnet: "10.50.50.0/24"
      vlan: 401
      network_type: "p2p"
      connected_routers:
        - router_name: "pe1"
          interface: "eth2"
          ip_address: "10.50.50.1"
        - router_name: "ce1-spoke"
          interface: "eth1"
          ip_address: "10.50.50.2"
    # CE LAN networks
    - name: lan-spoke1
      subnet: "10.100.1.0/24"
      gateway: "10.100.1.1"
      network_type: "multi-access"
      connected_routers:
        - router_name: "ce1-spoke"
          interface: "eth2"
          ip_address: "10.100.1.1"
  routers:
    - name: "p1"
      hostname: "p1"
      router_id: "10.0.0.3"
      role: "P"
      location:
        latitude: 51.5074
        longitude: -0.1278
        city: "London"
        country: "United Kingdom"
        site: "London-DC1"
      interfaces:
        - name: "eth0"
          network: "mgmt"
        - name: "eth1"
          network: "p1-p2"
        - name: "lo"
          network: "loopbacks"
    # ... (remaining routers) ...
---
# Layer 2: Underlay routing protocols
apiVersion: google.dev/v1
kind: VyOSUnderlay
metadata:
  name: <underlay-name>
  namespace: default
spec:
  infrastructureRef: <infra-name>
  routing:
    ospf:
      router_id_source: "loopback"
      areas:
        - area_id: "0.0.0.0"
          type: "backbone"
    bgp:
      as_number: 65001
      router_id_source: "loopback"
      route_reflectors: ["10.0.0.1", "10.0.0.2"]  # RR loopback IPs
  mpls:
    enabled: true
    ldp:
      router_id_interface: "loopback"
  routers:
    # P router: OSPF + MPLS only
    - name: "p1"
      protocols:
        ospf:
          router_id: "10.0.0.3"
          areas:
            - area: "0.0.0.0"
              type: "backbone"
        mpls:
          enabled: true
          ldp:
            router_id: "10.0.0.3"
            interfaces: ["eth1", "eth2"]  # All core-facing interfaces
    # RR router: OSPF + MPLS + BGP (route_reflector: true)
    - name: "rr1"
      protocols:
        ospf:
          router_id: "10.0.0.1"
          areas:
            - area: "0.0.0.0"
              type: "backbone"
        bgp:
          as_number: 65001
          router_id: "10.0.0.1"
          route_reflector: true
          neighbors:
            - peer: "10.0.0.7"    # pe1 loopback
              remote_as: 65001
              route_reflector_client: true
            - peer: "10.0.0.8"    # pe2 loopback
              remote_as: 65001
              route_reflector_client: true
        mpls:
          enabled: true
          ldp:
            router_id: "10.0.0.1"
            interfaces: ["eth1", "eth2"]
    # PE router: OSPF + MPLS + BGP (peers with RRs)
    - name: "pe1"
      protocols:
        ospf:
          router_id: "10.0.0.7"
          areas:
            - area: "0.0.0.0"
              type: "backbone"
        bgp:
          as_number: 65001
          router_id: "10.0.0.7"
          neighbors:
            - peer: "10.0.0.1"   # rr1 loopback
              remote_as: 65001
            - peer: "10.0.0.2"   # rr2 loopback
              remote_as: 65001
        mpls:
          enabled: true
          ldp:
            router_id: "10.0.0.7"
            interfaces: ["eth1"]  # Core-facing interfaces only
    # CE router: eBGP only (no OSPF, no MPLS)
    - name: "ce1-spoke"
      protocols:
        bgp:
          as_number: 65035
          router_id: "10.0.0.80"
          neighbors:
            - peer: "10.50.50.1"  # PE's IP on the PE-to-CE link
              remote_as: 65001
---
# Layer 3: L3VPN overlay (VRFs and eBGP to CE)
apiVersion: google.dev/v1
kind: VyOSL3VPN
metadata:
  name: <l3vpn-name>
  namespace: default
spec:
  underlayRef: <underlay-name>
  services:
    - name: "BLUE_SPOKE"
      type: "l3vpn"
      topology: "spoke"
    - name: "BLUE_HUB"
      type: "l3vpn"
      topology: "hub"
  routers:
    # Spoke PE
    - name: "pe1"
      vrfs:
        - name: "BLUE_SPOKE"
          table: 200
          rd: "10.50.50.1:1011"   # <pe-ce-link-ip>:<service-id>
          rt_export: ["65035:1011"]
          rt_import: ["65035:1030"]
          interfaces: ["eth2"]     # CE-facing interface
      bgp:
        vrfs:
          - name: "BLUE_SPOKE"
            neighbors:
              - peer: "10.50.50.2"   # CE IP on PE-to-CE link
                remote_as: 65035
    # Hub PE
    - name: "pe2"
      vrfs:
        - name: "BLUE_HUB"
          table: 400
          rd: "10.80.80.1:1011"
          rt_export: ["65035:1030"]
          rt_import: ["65035:1011", "65035:1030"]  # Import both to enable hairpin
          interfaces: ["eth3"]
      bgp:
        vrfs:
          - name: "BLUE_HUB"
            neighbors:
              - peer: "10.80.80.2"
                remote_as: 65035
---
# Device: simulated end-host on a customer LAN
apiVersion: google.dev/v1
kind: Device
metadata:
  name: dev1
  namespace: default
spec:
  network_name: "lan-spoke1"      # Must match a LinuxNetwork name
  ip_address: "10.100.1.10"       # Must be within the LAN subnet
  gateway: "10.100.1.1"           # CE router's LAN interface IP
```

---

## Querying Current Topology from Spanner

Before producing a design, always query Spanner to understand what already exists. This prevents naming conflicts and allows the design to extend the existing topology.

### Check existing routers

```sql
SELECT name, role, location_city, status
FROM PhysicalRouter
WHERE valid_end_ts IS NULL
ORDER BY role, name
```

### Check existing networks (logical subnets)

```sql
SELECT name, cidr, network_type, operational_state
FROM LogicalSubnet
WHERE valid_end_ts IS NULL
ORDER BY cidr
```

### Check existing VPN services

```sql
SELECT vpn.name, vpn.topology, vrf.name AS vrf_name, vrf.rd, r.name AS router
FROM L3VPNService vpn
JOIN VRF vrf ON vpn.id = vrf.vpn_id
JOIN PhysicalRouter r ON vrf.router_id = r.id
WHERE vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
```

### Check router connectivity (graph traversal)

```sql
GRAPH networkGraph
MATCH (r1:PhysicalRouter)-[:HasInterface]->(i1:PhysicalInterface)
      -[:ConnectsTo]->(l:PhysicalLink)
      -[:LinkedTo]->(i2:PhysicalInterface)<-[:HasInterface]-(r2:PhysicalRouter)
WHERE r1.valid_end_ts IS NULL
  AND r2.valid_end_ts IS NULL
RETURN r1.name AS router_a, r2.name AS router_b, l.bandwidth
```

### Check VLAN and subnet allocation (to avoid conflicts)

```sql
SELECT name, cidr, network_type
FROM LogicalSubnet
WHERE valid_end_ts IS NULL
  AND cidr LIKE '172.16.%'
ORDER BY cidr
```

Use these queries to:
- Identify existing loopback IPs already in use before picking a new one
- Find the next available VLAN ID (highest existing + 1)
- Confirm PE routers are in `Ready` status before adding a new VRF
- Verify RR router loopback IPs for reference in new `VyOSUnderlay` BGP configs
