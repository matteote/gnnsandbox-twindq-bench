# Spanner Network Graph

Spanner is used to represent the network topology and its history. [Linux components that realise a virtual network](/docs/network/Readme.md) and [logical network models](/docs/automation/Readme.md) are modelled in spanner.

This section details the Spanner network **table design**, the **Property Graph** definitions, and how to query the network model using **SQL** and **GQL**.

## 1. Core Data Model

The database tracks the history of every network entity using **Slowly Changing Dimensions (SCD) Type 2**. The  *current* state of a router or interface is stoed along with every version of it that has ever existed, defined by a validity time window.

### Schema Pattern

Every topological table (`PhysicalRouter`, `PhysicalInterface`, `PhysicalLink`, etc.) has these two timestamp columns:
- `valid_start_ts`: When this version of the entity became active.
- `valid_end_ts`: When this version was replaced or deleted. (NULL means it is currently active).

**Primary Key**: `(id, valid_start_ts DESC)`

### Active Row Logic
To find the state of an entity at any given time `T`, you must filter for rows where `T` falls within the validity window:

```sql
valid_start_ts <= @T AND (valid_end_ts > @T OR valid_end_ts IS NULL)
```

## 2. Data Dictionary & Relationships

### Physical Network Layer

The physical model represents the network infrastructure and connectivity patterns between routers and devices. Also capabilities available, e.g. Bandwidth and current configuration and state. 

#### Table Design

The relational model representing the physical network model is shown in the drawing and table below. 

![](/docs/drawings/spanner/physical-schema.drawio.svg)


| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `PhysicalRouter` | A physical router device. | `id`, `name`, `vendor`, `model`, `location_city`, `location_lat`, `location_lon`, `role`, `status`, `config` | **Parent** of `PhysicalInterface`. Hosts `VRF`s. |
| `PhysicalInterface` | A hardware interface on a router. | `id`, `router_id`, `name`, `speed`, `media_type`, `ip_address`, `mac_address`, `status` | **Child** of `PhysicalRouter`. Connects to `PhysicalLink`. Associated with `LogicalSubnet`. |
| `PhysicalLink` | A cable or fiber connecting two interfaces. | `id`, `name`, `bandwidth`, `status`, `properties` | Connects two `PhysicalInterface`s via `Interface_Link`. |
| `LogicalSubnet` | An IPv4/IPv6 subnet defined on an interface. | `id`, `cidr`, `network_type`, `description`, `operational_state`, `mtu`, `mac_address`, `bridge_ip`, `host_device_name`, `properties` | Associated with `PhysicalInterface` via `Subnet_Association`. |
| `Device` | A network device (e.g., VM or host) connected to a CE router interface. | `id`, `name`, `interface_id`, `network_name`, `ip_address`, `gateway`, `vlan`, `status`, `config` | Connected to `PhysicalInterface` via `ConnectedTo`. |
| `Interface_Link` | **Edge Table (Temporal)**: Resolves many-to-many between `PhysicalInterface` and `PhysicalLink`. | `interface_id`, `link_id` | Joins `PhysicalInterface` and `PhysicalLink`. |
| `Subnet_Association` | **Edge Table (Temporal)**: Maps `LogicalSubnet`s to interfaces or other entities. | `entity_id`, `subnet_id`, `entity_type` | Joins `PhysicalInterface` and `LogicalSubnet`. |


#### Property Graph

The following Spanner graph nodes and edges model is applied to the physical network schema. 

Edges are defined as SQL **Views** that dynamically join the underlying tables while respecting SCD Type 2 temporal constraints. Each view is then registered as a named edge type in the property graph.


![physical-graph](/docs/drawings/spanner/physical-graph.drawio.svg)

The following nodes are registered in the graph. 

| Node Type | Underlying Table |
| :--- | :--- |
| `PhysicalRouter` | `PhysicalRouter` |
| `PhysicalInterface` | `PhysicalInterface` |
| `PhysicalLink` | `PhysicalLink` |
| `Device` | `Device` |

The following edges are registered in the graph.

| Edge Name | View | Source Node | Destination Node | Description |
| :--- | :--- | :--- | :--- | :--- |
| `HasInterface` | `HasInterface_Edge` | `PhysicalRouter` | `PhysicalInterface` | Router owns an interface. |
| `ConnectsTo` | `ConnectsTo_Edge` | `PhysicalInterface` | `PhysicalLink` | Interface connects to a physical link. |
| `LinkedTo` | `LinkedTo_Edge` | `PhysicalLink` | `PhysicalInterface` | Link connects back to an interface (reverse of ConnectsTo). |
| `ConnectedTo` | `ConnectedTo_Edge` | `Device` | `PhysicalInterface` | A device is connected to the specific CE router interface (port) it attaches to. |


### Logical / Service Layer

The logical layer models the l3vpn service that can be configured across the physical network infrastructure. 

#### Table Design

These tables represent the virtualized network services (L3VPN, BGP).

![logical schema](/docs/drawings/spanner/logical-schema.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `L3VPNService` | A logical VPN instance (e.g., "Finance VPN"). | `id`, `customer_id`, `name`, `service_type`, `topology`, `status`, `config` | Contains `VRF`s. (`customer_id` is a plain string field — no Customer table.) |
| `VRF` | Virtual Routing and Forwarding instance on a router. | `id`, `router_id`, `vpn_id`, `name`, `rd`, `status`, `config` | Belongs to `L3VPNService`. Located on `PhysicalRouter`. Contains `BGPSession`s. |
| `BGPSession` | One side of an eBGP neighbour relationship — specifically the **CE↔PE eBGP sessions** scoped to a VRF. One row is written per VRF neighbour. iBGP sessions between PEs and the route reflectors are part of the underlay and are stored in `PhysicalRouter.config`, not here. | `id`, `vrf_id`, `local_as`, `remote_as`, `peer_ip`, `status`, `config` | Belongs to `VRF`. Peers with other routers (resolved at query time via `peer_ip → PhysicalInterface`). |

`BGPSession` captures the **structural, configuration-time view** of each CE↔PE eBGP session — which peer, which VRF, which AS, and whether the operator last saw it as `Established` or `Idle`. This is complemented by two time-series metrics scraped from `frr_exporter` on each router and written to `NetworkMetrics` every 20 seconds:

| Metric | Labels | What it tells you |
| :--- | :--- | :--- |
| `frr_bgp_peer_uptime_seconds` | `afi`, `neighbor`, `vrf` | How long the session to a specific peer has been continuously up. Drops to zero (or disappears) when the peer is `Idle` or unreachable — a continuous health signal at 20-second resolution. |
| `frr_bgp_peer_prefixes_advertised_count_total` | `afi`, `neighbor`, `vrf` | How many prefixes the router is actively advertising to that peer. Catches the subtle failure mode of **session up but no routes** — for example, a spoke PE whose hub RT import policy is misconfigured will show `Established` status but zero prefixes advertised. |

The `neighbor` label matches `BGPSession.peer_ip` and the `vrf` label matches the VRF name encoded in `BGPSession.vrf_id`, making them directly joinable. For example, pe1's BLUE_SPOKE session produces:

```
NetworkMetrics:
  node_name   = "pe1"
  metric_name = "frr_bgp_peer_uptime_seconds"
  kind        = "ROUTING"
  value       = 3600.0
  labels      = {"afi": "ipv4", "neighbor": "10.50.50.2", "vrf": "BLUE_SPOKE"}
```

which maps directly to `BGPSession` row `bgp:pe1:BLUE_SPOKE:10.50.50.2`.

The `BGPSession.status` column (written by the operator's 60-second monitor via SCD Type 2) records **when** the session changed state and preserves the full history. The `frr_bgp_peer_uptime_seconds` metric provides the **continuous signal** between those snapshots, allowing queries like "how long has this session been flapping?" without reconstructing it from SCD history.

**iBGP / core health** is not surfaced in `BGPSession` rows. Instead, failures in the iBGP control plane (PE↔RR sessions) or MPLS dataplane show up indirectly via:
- `frr_route_total` / `frr_route_total_fib` — route counts drop on a PE if it loses its RR sessions and can no longer receive VPN-IPv4 prefixes from other PEs.
- `frr_ospf_neighbor_adjacencies` — if OSPF breaks, LDP loses its LSPs and forwarding fails even if BGP sessions remain technically established.

**Example: join BGP session state with live uptime metrics**

```sql
SELECT
  b.id           AS session_id,
  b.peer_ip,
  b.status       AS configured_status,
  nm.value       AS uptime_seconds,
  nm.timestamp
FROM BGPSession b
JOIN NetworkMetrics nm
  ON nm.node_name = SPLIT(b.id, ':')[OFFSET(1)]
  AND JSON_VALUE(nm.labels, '$.neighbor') = b.peer_ip
  AND JSON_VALUE(nm.labels, '$.vrf')      = SPLIT(b.vrf_id, ':')[OFFSET(2)]
WHERE nm.metric_name = 'frr_bgp_peer_uptime_seconds'
  AND b.valid_end_ts IS NULL
  AND nm.timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
ORDER BY nm.timestamp DESC
```

#### Property Graph

Graph nodes and edges model for the logical VPN is below.

![logical-graph](/docs/drawings/spanner/logical-graph.drawio.svg)

The following tables are registered as node types in the graph:

| Node Type | Underlying Table |
| :--- | :--- |
| `PhysicalRouter` | `PhysicalRouter` |
| `L3VPNService` | `L3VPNService` |
| `VRF` | `VRF` |
| `BGPSession` | `BGPSession` |
| `LogicalSubnet` | `LogicalSubnet` |

The following edges are registered in the graph 

| Edge Name | View | Source Node | Destination Node | Description |
| :--- | :--- | :--- | :--- | :--- |
| `RealizesVPN` | `RealizesVPN_Edge` | `VRF` | `L3VPNService` | VRF realizes (implements) the VPN service. |
| `LocatedOn` | `LocatedOn_Edge` | `VRF` | `PhysicalRouter` | VRF is configured on a physical router. |
| `BelongsToVRF` | `BelongsToVRF_Edge` | `BGPSession` | `VRF` | BGP session belongs to a VRF. |

> **Note:** BGP peering relationships between routers are not stored as a static edge table. The GNN derives `bgp_peer` edges at query time by joining `BGPSession.peer_ip` against `PhysicalInterface.ip_address`. This approach works correctly even when only one side of the peering (the PE) has a `BGPSession` row — which is the case in CE↔PE L3VPN topologies where the CE router's session is not stored in Spanner.

### Network Flows

Traffic flows provisioned by the traffic-agent via `TrafficTest` Kubernetes CRDs are modelled using the same SCD Type 2 pattern as other topology entities. A new row is written whenever the flow phase changes, preserving the full lifecycle history of every test.

#### Table Design

![flow schema](/docs/drawings/spanner/flow-schema.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `TrafficFlow` | A traffic flow provisioned between two devices. Tracks phase transitions (starting → running → completed/failed/stopped). | `id`, `name`, `src_device_id`, `dst_device_id`, `phase`, `config` | Source linked to `Device` via `FlowFromDevice`. Destination linked to `Device` via `FlowToDevice`. |

**Primary Key**: `(id, valid_start_ts DESC)` — same SCD Type 2 pattern as all other topology tables.

**Flow ID scheme**: `flow:<TrafficTest-name>` — stable across operator restarts.

**SCD lifecycle**:
| Event | Action |
|---|---|
| `TrafficTest` created | Insert first row (`phase = pending/deploying`) |
| Phase changes (e.g. Running → Completed) | Close current row + insert new row |
| `TrafficTest` deleted | Close current row |

**Example: query active flow metrics from Spanner**

```sql
SELECT
  node_name AS flow_id,
  metric_name,
  value,
  timestamp
FROM NetworkMetrics
WHERE kind = 'flow'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
ORDER BY timestamp DESC
LIMIT 50
```

**Example: join flow topology with its metrics**

```sql
SELECT
  f.name        AS flow_name,
  f.phase,
  f.src_device_id,
  f.dst_device_id,
  nm.metric_name,
  nm.value,
  nm.timestamp
FROM TrafficFlow f
JOIN NetworkMetrics nm ON nm.node_name = f.id
WHERE f.valid_end_ts IS NULL
  AND nm.kind = 'flow'
  AND nm.timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
ORDER BY nm.timestamp DESC
```

#### Property Graph

Two graph edge views connect `TrafficFlow` to its source and destination `Device` nodes, respecting temporal overlap between the flow and device validity windows.

![flow graph](/docs/drawings/spanner/flow-graph.drawio.svg)

| Node Type | Underlying Table |
| :--- | :--- |
| `TrafficFlow` | `TrafficFlow` |

| Edge Name | View | Source Node | Destination Node | Description |
| :--- | :--- | :--- | :--- | :--- |
| `FlowFromDevice` | `FlowFromDevice_Edge` | `TrafficFlow` | `Device` | Flow originates from this device. |
| `FlowToDevice` | `FlowToDevice_Edge` | `TrafficFlow` | `Device` | Flow terminates at this device. |

Since `Device` already has a `ConnectedTo` edge to `PhysicalInterface`, the full path `TrafficFlow → Device → PhysicalInterface → PhysicalRouter` can be traversed with a single GQL query.

### Network Descriptors

The `NetworkDescriptor` table is a simple named-document store for complete network configurations. It is not part of the property graph and has no temporal history — it is a plain keyed record used by the UI to save and load network blueprints.

#### Table Design

| Table | Description | Key Fields |
| :--- | :--- | :--- |
| `NetworkDescriptor` | A saved network configuration bundling all component CRDs for a named network. | `id`, `name`, `description`, `infrastructure`, `underlay`, `vpns`, `traffic_tests`, `labels`, `created_at`, `updated_at` |

```sql
CREATE TABLE NetworkDescriptor (
  id             STRING(MAX) NOT NULL,  -- "network:<name>", e.g. "network:l3vpn-infra"
  name           STRING(MAX) NOT NULL,  -- human-readable name
  description    STRING(MAX),           -- optional description
  infrastructure JSON NOT NULL,         -- VyOSInfrastructure CRD body
  underlay       JSON,                  -- VyOSUnderlay CRD body
  vpns           JSON,                  -- JSON array of VyOSL3VPN CRD bodies
  traffic_tests  JSON,                  -- JSON array of TrafficTest CRD bodies
  labels         JSON,                  -- freeform key-value metadata
  created_at     TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true),
  updated_at     TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp=true)
) PRIMARY KEY (id)
```

**ID convention**: `network:<infrastructure-name>` — e.g. `network:l3vpn-infra`.

Each record bundles the four CRD layers that together define a complete deployable network:

| Field | CRD kind | Cardinality |
| :--- | :--- | :--- |
| `infrastructure` | `VyOSInfrastructure` | exactly one |
| `underlay` | `VyOSUnderlay` | exactly one (nullable until set) |
| `vpns` | `VyOSL3VPN` | JSON array — zero or more (e.g. `[blue-vpn, red-vpn]`) |
| `traffic_tests` | `TrafficTest` | JSON array — zero or more |

**Usage notes**
- Written and read by the supervisor UI backend; the network operator does not touch this table.
- `INSERT OR UPDATE` semantics on save — calling save with the same `id` replaces the existing record and bumps `updated_at`.
- The `vpns` and `traffic_tests` arrays store the full CRD body as parsed JSON objects, preserving every `spec` field so the UI can reconstruct and re-apply them via `kubectl apply` without loss of configuration.

**Example: save a complete network**

```sql
INSERT OR UPDATE NetworkDescriptor (id, name, description, infrastructure, underlay, vpns, traffic_tests, labels, created_at, updated_at)
VALUES (
  'network:l3vpn-infra',
  'l3vpn-infra',
  'UK hub-spoke L3VPN lab with BLUE and RED VPNs',
  @infrastructure_json,  -- full VyOSInfrastructure body
  @underlay_json,        -- full VyOSUnderlay body
  @vpns_json,            -- [blue-vpn body, red-vpn body]
  @traffic_tests_json,   -- [d1-to-hub-tcp body, d2-to-hub-udp body, ...]
  JSON '{"environment": "lab", "type": "telco-lab"}',
  PENDING_COMMIT_TIMESTAMP(),
  PENDING_COMMIT_TIMESTAMP()
)
```

**Example: load a network by name**

```sql
SELECT id, name, description, infrastructure, underlay, vpns, traffic_tests, labels, updated_at
FROM NetworkDescriptor
WHERE id = 'network:l3vpn-infra'
```

**Example: list all saved networks**

```sql
SELECT id, name, description, labels, updated_at
FROM NetworkDescriptor
ORDER BY updated_at DESC
```

---

### Observability, Metrics & AI

Tables tracking events, metrics, embeddings, and performance data.

#### Metrics

Selected Network metrics scraped from each Vyos Router are stored in spanner periodically as shown in the schema below. 

![Network Metrics Graph](/docs/drawings/spanner/metrics.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `NetworkMetrics` | Time-series metrics (throughput, error rates, etc.). | `id`, `timestamp`, `kind`, `node_name`, `metric_name`, `metric_type`, `value`, `labels`, `interface` | Linked to routers via `node_name`. |

The `NetworkMetrics` table collects specific metrics from the underlying VyOS routers using Prometheus. These are grouped into two categories:

**System Metrics**
These metrics track the underlying machine's hardware and network interface utilization:
- `node_load1` (Gauge): 1-minute load average
- `node_memory_SwapFree_bytes` (Gauge)
- `node_memory_MemTotal_bytes` (Gauge)
- `node_network_up` (Gauge): Interface operational state
- `node_network_carrier` (Gauge): Link status
- `node_network_carrier_changes_total` (Counter)
- `node_network_receive_bytes_total` (Counter)
- `node_network_receive_drop_total` (Counter)
- `node_network_receive_errs_total` (Counter)
- `node_network_receive_packets_total` (Counter)
- `node_network_transmit_bytes_total` (Counter)
- `node_network_transmit_drop_total` (Counter)
- `node_network_transmit_errs_total` (Counter)
- `node_network_transmit_packets_total` (Counter)

**Routing Metrics**
These metrics track the FRR routing daemon's operation and BGP/OSPF/BFD statuses:
- `frr_bfd_peer_count` (Gauge)
- `frr_collector_up` (Gauge)
- `frr_ospf_neighbor_adjacencies` (Gauge)
- `frr_ospf_neighbors` (Gauge)
- `frr_route_total` (Gauge)
- `frr_route_total_fib` (Gauge)
- `process_open_fds` (Gauge): File descriptors opened by the routing process
- `process_network_receive_bytes_total` (Counter)
- `process_network_transmit_bytes_total` (Counter)

**Flow Metrics**

Flow metrics are produced by the traffic-agent's `/metrics` Prometheus endpoint.

| Metric Name | Type | Description | Transport |
| :--- | :--- | :--- | :--- |
| `traffic_agent_bytes_sent_total` | Counter | Total bytes sent by this flow | TCP + UDP |
| `traffic_agent_bytes_received_total` | Counter | Total bytes received by this flow | TCP + UDP |
| `traffic_agent_throughput_bps` | Gauge | Instantaneous throughput in bits/s (computed as delta since last scrape) | TCP + UDP |
| `traffic_agent_active_sessions` | Gauge | Number of currently active concurrent sessions within the flow | TCP + UDP |
| `traffic_agent_flow_running` | Gauge | `1` if the flow is in `running` phase, `0` otherwise | TCP + UDP |
| `traffic_agent_latency_ms` | Gauge | Mean round-trip latency in milliseconds | UDP only |
| `traffic_agent_packet_loss_pct` | Gauge | Packet loss percentage (derived from sequence-number accounting) | UDP only |
| `traffic_agent_jitter_ms` | Gauge | Mean inter-arrival jitter in milliseconds | UDP only |

#### Logs

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `KgLogEntryNode` | Raw log entries stored as knowledge graph nodes with embeddings. | `id`, `timestamp`, `severity`, `source`, `message`, `content`, `embedding` | Self-contained; indexed by `KgLogEntryNodeIdx1`. |

#### Embeddings

Embeddings from GNNs are stored in Spanner with node and graph properties as shown in the diagram below. 

![Embeddings Model](/docs/drawings/spanner/embeddings.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `NodeEmbedding` | GNN-generated vector embeddings and anomaly scores for graph nodes. | `id`, `node_id`,  `hetgnn_embedding`, `hetgnn_score`, `timestamp` | Linked to `PhysicalRouter` via `RouterHasEmbedding`. Linked to `PhysicalInterface` via `InterfaceHasEmbedding`. |

## 3. Querying the Data

You can query the data using standard GoogleSQL or the Graph Query Language (GQL).

### SQL: Fetching Topology at Time T

To reconstruct the graph manually (e.g., for ML training pipelines), you select from tables implementing the time-slice logic.

**Example: Get all active Routers and their Interfaces at a given timestamp**

```sql
-- Parameters: @ts = TIMESTAMP('2025-01-01 10:00:00')

SELECT 
  r.name AS router_name,
  i.name AS interface_name,
  i.ip_address
FROM PhysicalRouter r
JOIN PhysicalInterface i ON r.id = i.router_id
WHERE 
  -- Router is valid at T
  r.valid_start_ts <= @ts AND (r.valid_end_ts > @ts OR r.valid_end_ts IS NULL)
  -- Interface is valid at T
  AND i.valid_start_ts <= @ts AND (i.valid_end_ts > @ts OR i.valid_end_ts IS NULL)
```

**Example: Get all VRFs for a VPN service with their router locations**

```sql
SELECT
  vpn.name AS vpn_name,
  vrf.name AS vrf_name,
  r.name AS router_name,
  r.location_city
FROM L3VPNService vpn
JOIN VRF vrf ON vpn.id = vrf.vpn_id
JOIN PhysicalRouter r ON vrf.router_id = r.id
WHERE
  vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
  AND vpn.customer_id = @customer_id
```

### GQL: Graph Traversal

GQL allows for more expressive path traversals over the `networkGraph` property graph.

**Example: Find all Interfaces connected to a Router**

```sql
GRAPH networkGraph
MATCH (r:PhysicalRouter)-[e:HasInterface]->(i:PhysicalInterface)
WHERE r.name = "router-1"
  AND r.valid_end_ts IS NULL 
  AND i.valid_end_ts IS NULL
RETURN i.name, i.speed, i.ip_address
```

**Example: Find End-to-End Path (Router → Interface → Link → Interface → Router)**

```sql
GRAPH networkGraph
MATCH (src:PhysicalRouter)-[:HasInterface]->(i1:PhysicalInterface)
      -[:ConnectsTo]->(l:PhysicalLink)
      -[:LinkedTo]->(i2:PhysicalInterface)<-[:HasInterface]-(dst:PhysicalRouter)
WHERE src.name = "edge-router-a"
RETURN dst.name AS connected_router, l.bandwidth
```

**Example: Trace full L3VPN service topology**

```sql
GRAPH networkGraph
MATCH (vpn:L3VPNService)<-[:RealizesVPN]-(vrf:VRF)-[:LocatedOn]->(r:PhysicalRouter)
WHERE vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
RETURN vpn.name, vrf.name, vrf.rd, r.name, r.location_city
```

**Example: Find anomalous nodes via NodeEmbedding scores**

```sql
GRAPH networkGraph
MATCH (r:PhysicalRouter)-[:RouterHasEmbedding]->(e:NodeEmbedding)
WHERE e.hetgnn_score > 0.8
RETURN r.name, e.hetgnn_score, e.anomaly_explanation, e.timestamp
ORDER BY e.hetgnn_score DESC
```


## 4. Derived Edges without Foreign Keys

In our schema, we removed rigid Foreign Key constraints to increase write throughput and flexibility for the SCD model. Relationships are maintained via:

1. **Logical IDs**: e.g., `router_id` in `PhysicalInterface` contains the ID of the parent router; `vpn_id` in `VRF` references `L3VPNService`.
2. **Edge Views**: The `CREATE VIEW ... AS SELECT ... JOIN ...` statements in `spanner.j2` define how logical IDs resolve to graph edges, handling the timestamp overlap logic automatically.

When querying the **Graph** (GQL), these Views are used transparently — you do not need to manually write the complex time-window JOINs for every query.
