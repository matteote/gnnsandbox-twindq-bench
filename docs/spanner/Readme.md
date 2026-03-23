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
| `Device` | A network device (e.g., VM or host) connected to a router. | `id`, `name`, `router_id`, `network_name`, `ip_address`, `gateway`, `vlan`, `status`, `config` | Connected to `PhysicalRouter` via `ConnectedTo`. |
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
| `ConnectedTo` | `ConnectedTo_Edge` | `Device` | `PhysicalRouter` | A device is connected to a router. |


### Logical / Service Layer

The logical layer models the l3vpn service that can be configured across the physical network infrastructure. 

#### Table Design

These tables represent the virtualized network services (L3VPN, BGP).

![logical schema](/docs/drawings/spanner/logical-schema.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `Customer` | The entity owning a VPN service. | `id`, `name`, `type`, `properties` | Owns `L3VPNService`. Places `Orders`. |
| `L3VPNService` | A logical VPN instance (e.g., "Finance VPN"). | `id`, `customer_id`, `name`, `service_type`, `topology`, `status`, `config` | Owned by `Customer`. Contains `VRF`s. |
| `VRF` | Virtual Routing and Forwarding instance on a router. | `id`, `router_id`, `vpn_id`, `name`, `rd`, `status`, `config` | Belongs to `L3VPNService`. Located on `PhysicalRouter`. Contains `BGPSession`s. |
| `BGPSession` | A BGP neighbor configuration within a VRF. | `id`, `vrf_id`, `local_as`, `remote_as`, `peer_ip`, `status`, `config` | Belongs to `VRF`. Peers with another `BGPSession`. |
| `BGP_Peering` | **Edge Table (Temporal)**: Represents an active BGP session between two neighbors. | `session_id_a`, `session_id_b` | Joins two `BGPSession`s. |
| `Orders` | Customer orders placed against the system. | `id`, `timestamp`, `customer_id`, `customer` | Linked to `Customer` via `PlacedBy`. |

#### Property Graph

Graph nodes and edges model for the logical VPN is below.

![logical-graph](/docs/drawings/spanner/logical-graph.drawio.svg)

The following tables are registered as node types in the graph:

| Node Type | Underlying Table |
| :--- | :--- |
| `PhysicalRouter` | `PhysicalRouter` |
| `Customer` | `Customer` |
| `L3VPNService` | `L3VPNService` |
| `VRF` | `VRF` |
| `BGPSession` | `BGPSession` |
| `LogicalSubnet` | `LogicalSubnet` |

The following edges are registered in the graph 

| Edge Name | View | Source Node | Destination Node | Description |
| :--- | :--- | :--- | :--- | :--- |
| `OwnedBy` | `OwnedBy_Edge` | `L3VPNService` | `Customer` | VPN service is owned by a customer. |
| `RealizesVPN` | `RealizesVPN_Edge` | `VRF` | `L3VPNService` | VRF realizes (implements) the VPN service. |
| `LocatedOn` | `LocatedOn_Edge` | `VRF` | `PhysicalRouter` | VRF is configured on a physical router. |
| `BelongsToVRF` | `BelongsToVRF_Edge` | `BGPSession` | `VRF` | BGP session belongs to a VRF. |
| `PeersWith` | `PeersWith_Edge` | `BGPSession` | `BGPSession` | BGP session peers with another BGP session. |


### Observability, Metrics & AI

Tables tracking events, metrics, embeddings, and performance data.

#### Metrics

Selected Network metrics scraped from each Vyos Router are stored in spanner periodically as shown in the schema below. 

![Network Metrics Graph](/docs/drawings/spanner/metrics.drawio.svg)

| Table | Description | Key Fields | Key Relationships |
| :--- | :--- | :--- | :--- |
| `NetworkMetrics` | Time-series metrics (throughput, error rates, etc.). | `id`, `timestamp`, `kind`, `name`, `metrics`, `interface_id`, `node_name`, `metric_name`, `metric_type`, `value`, `labels`, `interface` | Linked to `PhysicalInterface` via `interface_id`. |

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
MATCH (c:Customer)<-[:OwnedBy]-(vpn:L3VPNService)<-[:RealizesVPN]-(vrf:VRF)-[:LocatedOn]->(r:PhysicalRouter)
WHERE c.name = "acme-corp"
  AND vpn.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
RETURN vpn.name, vrf.name, vrf.rd, r.name, r.location_city
```

**Example: Find anomalous nodes via NodeEmbedding scores**

```sql
GRAPH networkGraph
MATCH (r:PhysicalRouter)-[:RouterHasEmbedding]->(e:NodeEmbedding)
WHERE e.dgat_score > 0.8
RETURN r.name, e.dgat_score, e.anomaly_explanation, e.timestamp
ORDER BY e.dgat_score DESC
```


## 4. Derived Edges without Foreign Keys

In our schema, we removed rigid Foreign Key constraints to increase write throughput and flexibility for the SCD model. Relationships are maintained via:

1. **Logical IDs**: e.g., `router_id` in `PhysicalInterface` contains the ID of the parent router; `vpn_id` in `VRF` references `L3VPNService`.
2. **Edge Views**: The `CREATE VIEW ... AS SELECT ... JOIN ...` statements in `spanner.j2` define how logical IDs resolve to graph edges, handling the timestamp overlap logic automatically.

When querying the **Graph** (GQL), these Views are used transparently — you do not need to manually write the complex time-window JOINs for every query.
