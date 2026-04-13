# GNN Model for Root Cause Analysis

A HetGNN is trained on historical network topology and telemetry to pinpoint the root cause of failures. It learns what a healthy network looks like in an unsupervised manner, then flags nodes whose current behaviour deviates significantly from that learned baseline.

The model captures temporal signal through **pre-computed temporal features** (gradients, deltas, uptime) fed as node attributes — detecting trends such as rising CRC error rates or flapping BGP sessions without requiring a sequential model.

---

## Scenario

The end-to-end validation scenario:

1. Create the virtual L3VPN network described below.
2. Run traffic simulation for 100 minutes (happy path — no injected faults).
3. Train the GNN on those 100 snapshots.
4. Run the model every 5 minutes to establish a healthy embedding baseline.
5. Inject a failure scenario on a target router.
6. Show that the model's anomaly score spikes on the correct node and layer, identifying the root cause.

## Network Under Test

The topology and traffic patterns are the L3VPN hub-and-spoke example from the transport scenarios documentation.

![network](/docs/drawings/transport/l3vpn-example-traffic.drawio.svg)

Two constant 5 Mbps TCP streams run from Spoke1 and Spoke2 to a single host behind the Hub PE.

The GNN identifies anomalies by comparing the current state to the learned "normal" baseline. What is normal differs significantly by router role:

| Role | Normal Behaviour | Anomaly Signal |
|---|---|---|
| **P Router** | High throughput, high CPU, zero customer BGP sessions | Any BGP activity on P = security anomaly |
| **RR Router** | High BGP churn, low data throughput; single most critical control-plane node | Loss of iBGP sessions from all PEs simultaneously |
| **PE Router** | High memory (thousands of VRF entries), active BGP to CE and RR | Memory pressure without VRF growth; BGP peer loss |
| **CE Router** | Lower throughput, sensitive to local interface flaps | Interface drops, loss of eBGP to PE |

---

## Graph Schema

This section defines the exact heterogeneous graph structure. Every training snapshot and inference call must produce a graph conforming to this schema.

### Node Types

Three node types cover all failure scenarios. Router role differentiation is handled through a `role_id` one-hot feature rather than separate node types — this keeps the schema simple while still allowing the model to learn role-specific behaviour through the projection and attention layers.

| Node Type | Description | Source Table |
|---|---|---|
| `router` | Any router (P, PE, RR, or CE). Role encoded in `role_id` feature. | `PhysicalRouter` |
| `interface` | Physical interface on any router. | `PhysicalInterface` |
| `bgp_session` | A BGP neighbour relationship within a VRF. | `BGPSession` |

> **Why not separate node types per role?** With only a handful of routers in the lab topology, separate node types (e.g. `router_rr`) would have very few training examples per type, making it hard for the model to learn a stable role-specific baseline. A single `router` type with `role_id` as a feature gives the model enough context to distinguish roles while sharing the projection layer across all routers for better generalisation.

### Node Features

All raw counter metrics are converted to **per-second rates** before feature engineering. Log-transform (`log1p`) is applied to high-variance counts. A `StandardScaler` is fitted per feature per node type across the training snapshots and serialised to `scalers.pkl`. Applied identically at both training and inference time.

#### `router`

| Feature | Source Metric | Derivation | Description |
|---|---|---|---|
| `state` | `node_network_up` | `1.0` if any interface up, else `0.0` | Router operational state. |
| `cpu` | `node_load1` | `ALIGN_MEAN` over snapshot window | 1-minute CPU load. Values > 1.0 indicate overload. |
| `mem` | `node_memory_MemAvailable_bytes` | `min(bytes / 4 GiB, 1.0)` | Available memory normalised to [0, 1]; lower = more pressure. |
| `ospf_num_routes` | `frr_route_total` (afi=`ipv4`) | `log1p(count)` | Total IPv4 routes in FRR table. Sudden drop = convergence event. |
| `pfx_count_norm` | `frr_bgp_peer_prefixes_advertised_count_total` | Sum across all peers on router, then `log1p` | Total BGP prefixes advertised. Set to `0.0` for P routers. |
| `role_p` | `PhysicalRouter.role` | `1.0` if role = P, else `0.0` | One-hot role encoding — P router. |
| `role_pe` | `PhysicalRouter.role` | `1.0` if role = PE, else `0.0` | One-hot role encoding — Provider Edge. |
| `role_rr` | `PhysicalRouter.role` | `1.0` if role = RR, else `0.0` | One-hot role encoding — Route Reflector. |
| `role_ce` | `PhysicalRouter.role` | `1.0` if role = CE, else `0.0` | One-hot role encoding — Customer Edge. |

#### `interface`

| Feature | Source Metric | Derivation | Description |
|---|---|---|---|
| `state` | `node_network_up` | `1.0` = up, `0.0` = down | Interface operational state. |
| `rx_drops` | `node_network_receive_drop_total` | `ALIGN_RATE`, then `log1p` | Inbound packet drop rate. Indicates congestion or MTU mismatch. |
| `tx_drops` | `node_network_transmit_drop_total` | `ALIGN_RATE`, then `log1p` | Outbound packet drop rate. Indicates egress queue overflow. |
| `mtu_norm` | `node_network_mtu_bytes` | `bytes / 9000` | MTU normalised against 9 KB jumbo maximum (~0.167 for standard 1500 B). |
| `rx_err_gradient` | `node_network_receive_errs_total` | `(rate_t − rate_{t−1}) / interval_seconds` | **Engineered temporal feature.** Rate of change of inbound errors. Detects rising error trend before link failure. `0.0` for first snapshot. |
| `tx_util` | `node_network_transmit_bytes_total` | `ALIGN_RATE`, normalised by interface `speed` | Transmit utilisation in [0, 1]. |
| `rx_util` | `node_network_receive_bytes_total` | `ALIGN_RATE`, normalised by interface `speed` | Receive utilisation in [0, 1]. |

> **Traffic asymmetry via message passing:** Rather than an explicit edge feature, MTU mismatch is detected implicitly. When interface A aggregates messages from the physically connected interface B, it receives B's `rx_util`. The model learns that in a healthy link, `tx_util(A) ≈ rx_util(B)`. A mismatch — A transmitting at high `tx_util` while B reports low `rx_util` — becomes visible through 2-hop message passing without requiring edge feature support.

#### `bgp_session`

| Feature | Source | Derivation | Description |
|---|---|---|---|
| `bgp_state` | `BGPSession.status` (Spanner topology) | `1.0` = Established, `0.0` = all other states | Peering establishment state. Transition to `0.0` = session drop. |
| `pfx_count_norm` | `frr_bgp_peer_prefixes_advertised_count_total` | Per-session count, `log1p` | Prefixes advertised on this specific session. |
| `prefix_count_delta` | `frr_bgp_peer_prefixes_advertised_count_total` | `count_t − count_{t−1}` | **Engineered temporal feature.** Change in advertised prefixes between snapshots. Sudden drop = route withdrawal. |
| `session_uptime_norm` | `BGPSession.valid_start_ts` | `(snapshot_ts − valid_start_ts).total_seconds() / 86400` | **Engineered temporal feature.** Session age in days, capped at 1.0. Low value = recent re-establishment (flapping). |

### Edge Types

Edges are expressed as `(source_node_type, relation_name, target_node_type)` tuples. All edges are treated as **undirected** (both directions added).

| Edge Type Tuple | Layer | Source Table / View | Description |
|---|---|---|---|
| `(router, has_interface, interface)` | Physical | `PhysicalInterface.router_id` join | Router owns an interface. |
| `(interface, connected_to, interface)` | Physical | `Interface_Link` join | Two interfaces physically cabled together. |
| `(router, ospf_peer, router)` | Underlay | `PhysicalLink` endpoints joined to parent routers via `router_id` | OSPF adjacency between two routers. |
| `(router, bgp_peer, router)` | Overlay | `BGP_Peering` joined via `VRF.router_id` | BGP session between two routers (iBGP or eBGP — distinguished at inference by role features of endpoint nodes). |
| `(bgp_session, session_on, router)` | Overlay | `VRF.router_id` join | BGP session belongs to a router via its VRF. |

---

## Model Architecture

The HetGNN is a **Graph Autoencoder**: an encoder compresses the heterogeneous graph into a latent embedding per node; a decoder reconstructs the original features from that embedding. High reconstruction error at inference time = anomalous node.

```
Input: HeteroData (single snapshot)
         │
         ▼
┌──────────────────────────────────────────┐
│  Type-specific Input Projection          │
│  Linear(input_dim_type, hidden_dim)+ReLU │
│  One projection layer per node type      │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  Heterogeneous Message Passing           │
│  HeteroConv(SAGEConv, aggr='sum')        │
│  NUM_LAYERS=2                            │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  Type-specific Decoders                  │
│  One Linear(hidden_dim, input_dim) per   │
│  node type → reconstructed features     │
└──────────────────────────────────────────┘
         │
         ▼
Per-node, per-feature reconstruction error (MSE)
```

Separate decoders per node type enable reconstruction error to be isolated by layer (router vs. interface vs. BGP session), which is the basis for the RCA drill-down.

### Hyperparameters

| Parameter | Value | Notes |
|---|---|---|
| `HIDDEN_CHANNELS` | 64 | Shared hidden dimension across all node types |
| `NUM_LAYERS` | 2 | Message-passing layers |
| `SNAPSHOT_INTERVAL` | 5 min | Time between snapshots (inference cadence) |
| `TRAINING_SNAPSHOTS` | 100 | Snapshots used for training (~8 hours of data at 5-min intervals) |
| `LEARNING_RATE` | 0.001 | Adam optimizer initial LR |
| `EPOCHS` | 50 | Maximum training epochs |
| `EARLY_STOPPING_PATIENCE` | 10 | Epochs without improvement before stopping |

---

## Training Pipeline

### Loss Function

A weighted multi-task MSE loss computed independently per node type. Independent losses enable per-layer anomaly isolation:

```
L_total = α · L_router + γ · L_interface + δ · L_bgp_session + λ · L_diversity
```

Where:
- `L_<type> = MSE(x̂_type, x_type)` — reconstruction error per node type
- `L_diversity` — contrastive penalty (mean pairwise cosine similarity within a node type) to prevent embedding collapse
- Suggested defaults: `α=0.5`, `γ=0.3`, `δ=0.2`, `λ=0.1`

Learning rate is reduced on plateau (`ReduceLROnPlateau`, factor=0.5, patience=5, min_lr=1e-6).

### Snapshot Construction (Point-in-Time Spanner Query)

For each training timestamp `T`, the snapshot is assembled using the SCD Type 2 temporal filter across all topology tables:

```sql
-- Routers active at time T
SELECT r.id, r.name, r.role, r.status
FROM PhysicalRouter r
WHERE r.valid_start_ts <= @T
  AND (r.valid_end_ts > @T OR r.valid_end_ts IS NULL)
```

The same filter applies to `PhysicalInterface`, `BGPSession`, `VRF`, `BGP_Peering`, and `Interface_Link`. Metrics are averaged over the snapshot window using `ALIGN_MEAN` (gauges) or `ALIGN_RATE` (counters).

### Preprocessing Steps

1. **Rate conversion:** Counter metrics (`*_total`) are converted to per-second rates using `ALIGN_RATE`.
2. **Gradient / delta computation:** `rx_err_gradient` and `prefix_count_delta` are computed as finite differences between consecutive snapshots **at data preparation time** and stored as regular node features.
3. **Log transform:** `log1p` applied to all count/rate features before scaling.
4. **StandardScaler:** Fitted per feature per node type across all training snapshots. Serialised to `scalers.pkl`. Applied identically at training and inference time.
5. **Missing metrics:** If a metric is absent for a node at time `T`, substitute `0.0` for rate features and the last-known value for gauge features. Set `state = 0.5` to distinguish "no data" from "confirmed down."
6. **New nodes mid-window:** The global node index is built from the union of all node IDs seen across all training snapshots. Nodes absent in a given snapshot receive a zero feature vector.

### Train / Validation Split

- **Split:** 80% train / 20% validation on individual snapshots, chronological order (validation snapshots are always later than training snapshots).
- Scalers are fitted on training snapshots only and applied to validation snapshots (no data leakage).

### Model Registration Criteria

A model is registered to the serving endpoint only if all three conditions are met:

1. `best_val_loss < 0.05` (aggregate MSE across all node types)
2. No single node type dominates: `L_type / L_total < 0.8` for all types (guards against a collapsed model)
3. Healthy embedding standard deviation `< 0.3` per dimension in `cluster_stats` (stable healthy-state representation)

If the model fails, the previous registered model is retained.

---

## Inference and Anomaly Scoring

### Dynamic Anomaly Threshold

The threshold is derived from the healthy training distribution, stored in `model_stats.pth`:

```
threshold(node_type) = μ(node_type) + k · σ(node_type)
```

Where `μ` and `σ` are the mean and standard deviation of per-node MSE reconstruction error across all training snapshots. `k` is a configurable sensitivity parameter (default `k = 3.0`). Computed **per node type** because routers, interfaces, and BGP sessions have different natural error magnitudes.

### Per-Feature Anomaly Contribution

The inference pipeline computes reconstruction error **per feature** for each node, not just the aggregate MSE. The `anomaly_explanation` JSON column stores the breakdown:

```json
{
  "name": "pe-router-1",
  "node_type": "router",
  "role": "PE",
  "anomaly_score": 1.84,
  "threshold": 0.52,
  "top_feature": "pfx_count_norm",
  "feature_scores": {
    "state": 0.01,
    "cpu": 0.04,
    "mem": 0.07,
    "ospf_num_routes": 0.12,
    "pfx_count_norm": 1.60,
    "role_p": 0.00,
    "role_pe": 0.00,
    "role_rr": 0.00,
    "role_ce": 0.00
  }
}
```

### Spanner Output Contract

Each inference run writes one row to `NodeEmbedding` per node:

| Column | Type | Value |
|---|---|---|
| `id` | STRING(36) | UUID generated per row |
| `node_id` | STRING | FK to `PhysicalRouter.id` or `PhysicalInterface.id` |
| `node_type` | STRING | One of: `router`, `interface`, `bgp_session` |
| `hetgnn_embedding` | ARRAY\<FLOAT64\> | Latent embedding vector (length = 64) |
| `hetgnn_score` | FLOAT64 | Aggregate MSE reconstruction error |
| `anomaly_explanation` | JSON | Per-feature scores and top contributing feature |
| `timestamp` | TIMESTAMP | `PENDING_COMMIT_TIMESTAMP()` |

---

## Root Cause Analysis

When the GNN flags anomalies, a two-stage process identifies the root cause:

1. **GNN inference** writes per-node `hetgnn_score` and `anomaly_explanation` to `NodeEmbedding`
2. **Rule-based classifier** reads those outputs and applies a deterministic decision tree to classify the fault type

### Fault Classification Algorithm

The classifier is a decision tree applied over the aggregated `NodeEmbedding` results. The GNN does the anomaly detection; the rules do the classification of what type of failure it is.

```
INPUT: NodeEmbedding rows from the latest inference run

STEP 1 — Is there an anomaly?
  If no nodes exceed threshold → EXIT (no fault)

STEP 2 — Which layer has the highest error?
  Compute MAX(hetgnn_score) per node_type.
  Dominant layer = node_type with the highest max score.

STEP 3 — Apply layer-specific pattern

  CASE dominant layer = 'interface':
    IF top_feature = 'rx_err_gradient'
      → HARDWARE DEGRADATION
      → Root cause: interface with highest rx_err_gradient score
    ELSE IF top_feature IN ('mtu_norm', 'tx_util', 'rx_drops')
      → MTU MISMATCH / SILENT DROP
      → Root cause: interface with highest mtu_norm score
    ELSE IF top_feature = 'state'
      → INTERFACE DOWN

  CASE dominant layer = 'bgp_session':
    Join all anomalous bgp_session nodes → parent router (via VRF.router_id)
    Group by parent router, count failing sessions per router.
    IF router with most failing sessions has role = 'RR'
      → RR CRASH (common path analysis)
      → Root cause: the RR router node
    ELSE IF exactly 1 session is anomalous AND its parent has role = 'CE'
      → LOCAL ACCESS FAILURE (PE-CE link)
      → Root cause: the specific bgp_session
    ELSE IF top_feature = 'session_uptime_norm' across sessions on 2+ different routers
      → IP OVERLAP / DUPLICATE IP
      → Root cause: bgp_session with the most recent valid_start_ts

  CASE dominant layer = 'router':
    IF top_feature = 'ospf_num_routes' → ROUTING CONVERGENCE ISSUE
    IF top_feature IN ('cpu', 'mem')   → RESOURCE EXHAUSTION
```

#### Step 1 — Check for Any Anomaly

```sql
SELECT
  node_id,
  node_type,
  hetgnn_score,
  JSON_VALUE(anomaly_explanation, '$.name')        AS node_name,
  JSON_VALUE(anomaly_explanation, '$.role')        AS role,
  JSON_VALUE(anomaly_explanation, '$.top_feature') AS top_feature
FROM NodeEmbedding
WHERE timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND hetgnn_score > @threshold
ORDER BY hetgnn_score DESC
LIMIT 20;
```

#### Step 2 — Identify the Dominant Failure Layer

```sql
SELECT
  node_type,
  COUNT(*)          AS anomalous_node_count,
  MAX(hetgnn_score) AS max_score,
  AVG(hetgnn_score) AS avg_score
FROM NodeEmbedding
WHERE timestamp = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND hetgnn_score > @threshold
GROUP BY node_type
ORDER BY max_score DESC;
```

#### Step 3 — BGP Common Path Analysis (RR Crash vs. Local Failure)

```sql
-- Group failing bgp_session nodes by their parent router.
-- The router with the most failing sessions is the common-path root cause.
SELECT
  r.name                   AS parent_router,
  r.role,
  COUNT(e.node_id)         AS failing_session_count,
  MAX(e.hetgnn_score)      AS max_session_score
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id   AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id        AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id        AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
GROUP BY r.name, r.role
ORDER BY failing_session_count DESC, max_session_score DESC
LIMIT 5;
-- role = 'RR' AND count > 1  →  RR Crash
-- role = 'CE' AND count = 1  →  Local Access Failure
```

---

### Failure Scenario Analysis

#### MTU Mismatch (Silent Drop)

**Symptom:** BGP and OSPF adjacencies remain "Up" but TCP/UDP traffic fails or slows severely.

- **Signal:** `tx_util` on one side of a link is high while the connected interface's `rx_util` is low. After 2-hop message passing, the model reconstructs what `tx_util` *should* predict for the neighbour's `rx_util` — the mismatch raises the reconstruction error.
- **GNN Observation:** High `interface` reconstruction error; `bgp_session` and `router` nodes healthy.
- **Top Feature:** `mtu_norm` on the specific `PhysicalInterface`.
- **Conclusion:** Physical Layer — MTU mismatch between core and edge.

```sql
SELECT
  i.name                                                                      AS interface_name,
  r.name                                                                      AS router_name,
  r.role,
  e.hetgnn_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.mtu_norm')  AS FLOAT64) AS mtu_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.tx_util')   AS FLOAT64) AS tx_util_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.rx_drops')  AS FLOAT64) AS rx_drops_score
FROM NodeEmbedding  e
JOIN PhysicalInterface i ON i.id        = e.node_id AND i.valid_end_ts IS NULL
JOIN PhysicalRouter    r ON i.router_id = r.id      AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'interface'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY mtu_score DESC
LIMIT 5;
```

#### BGP Session Down / Service Outage

**Symptom:** Total loss of connectivity between specific customer sites.

- **Signal:** `bgp_state` drops to `0.0`; `prefix_count_delta` drops sharply; `pfx_count_norm` approaches zero.
- **GNN Observation:** High `bgp_session` reconstruction error; physical and OSPF layers healthy.
- **Isolation:** If error is on a session linked to a router with `role = 'CE'` → **Local Access Failure**. If error is on sessions linked to a router with `role = 'RR'` → **Route Reflector root cause** (use common path query above).
- **Conclusion:** Overlay layer failure; suppress per-PE alerts, raise single RR alert.

```sql
-- Show each failing session with its parent router role
SELECT
  s.peer_ip,
  r.name                                                                           AS parent_router,
  r.role,
  e.hetgnn_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.bgp_state')       AS FLOAT64) AS bgp_state_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.pfx_count_norm')  AS FLOAT64) AS pfx_count_score
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id  AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id       AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id       AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY e.hetgnn_score DESC;
-- Multiple rows with role = 'RR' → run the common path query to confirm RR crash
-- Single row with role = 'CE'   → local PE-CE access failure
```

#### Hardware Degradation (CRC Errors)

**Symptom:** Intermittent packet loss and increasing latency before any routing failover.

- **Signal:** `rx_err_gradient` is positive on a specific `interface` node — errors are increasing even if the absolute count is still low.
- **GNN Observation:** Increasing `interface` reconstruction error over successive inference cycles.
- **Conclusion:** Physical Layer Impairment (failing SFP or fiber bend) — alert raised proactively before link failure.

```sql
-- Show the trend of anomaly score over the last 30 minutes.
-- A steadily rising score confirms gradual degradation rather than a transient blip.
SELECT
  i.name                                                                                AS interface_name,
  r.name                                                                                AS router_name,
  r.role,
  e.timestamp,
  e.hetgnn_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.rx_err_gradient') AS FLOAT64) AS err_gradient_score
FROM NodeEmbedding  e
JOIN PhysicalInterface i ON i.id        = e.node_id AND i.valid_end_ts IS NULL
JOIN PhysicalRouter    r ON i.router_id = r.id      AND r.valid_end_ts IS NULL
WHERE e.node_type = 'interface'
  AND JSON_VALUE(e.anomaly_explanation, '$.top_feature') = 'rx_err_gradient'
  AND e.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
ORDER BY i.name, e.timestamp;
-- Steadily increasing hetgnn_score across rows = hardware degradation
-- Single spike then recovery = transient noise
```

#### IP Address Overlap / Duplicate IP

**Symptom:** Flapping sessions — connectivity works briefly, drops, returns.

- **Signal:** `session_uptime_norm` repeatedly low (session keeps resetting); `bgp_state` oscillates between `0.0` and `1.0`.
- **GNN Observation:** High `bgp_session` reconstruction error on sessions belonging to two different routers.
- **Isolation:** The `bgp_session` node with the most recent `valid_start_ts` before the flapping began is flagged as the rogue.
- **Conclusion:** Logical Identity Conflict — duplicate peer IP.

```sql
-- Find sessions with the same peer_ip on different routers.
-- The row with MAX(valid_start_ts) for a given peer_ip is the rogue session.
SELECT
  s.peer_ip,
  r.name                                                                                   AS parent_router,
  s.valid_start_ts                                                                         AS session_established,
  e.hetgnn_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.session_uptime_norm') AS FLOAT64) AS uptime_score,
  CAST(JSON_VALUE(e.anomaly_explanation, '$.feature_scores.bgp_state')           AS FLOAT64) AS bgp_state_score
FROM NodeEmbedding  e
JOIN BGPSession     s ON s.id         = e.node_id  AND s.valid_end_ts IS NULL
JOIN VRF            v ON s.vrf_id     = v.id       AND v.valid_end_ts IS NULL
JOIN PhysicalRouter r ON v.router_id  = r.id       AND r.valid_end_ts IS NULL
WHERE e.node_type    = 'bgp_session'
  AND e.hetgnn_score > @threshold
  AND e.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
ORDER BY s.peer_ip, s.valid_start_ts DESC;
-- Duplicate peer_ip values across different parent_router rows = IP overlap confirmed
```

#### Route Reflector (RR) Process Crash

**Symptom:** Network core physically healthy; no new routes learned; VPNv4 table empties.

- **Signal:** `frr_collector_up = 0` on the RR router; `frr_route_total` drops; all PE BGP sessions lose prefixes simultaneously.
- **GNN Observation:** High reconstruction error on the `router` node where `role_rr=1.0`; all `bgp_session` nodes linked to it via `session_on` are also anomalous.
- **Common Path Analysis:** The only router node shared across all failing `bgp_session` nodes is the RR. Suppress individual session alerts; issue one RR root-cause alert.
- **Conclusion:** Control Plane failure — RR process crash or FRR daemon failure.

```sql
-- Confirm the RR as the common cause: show the anomalous RR router node alongside
-- the count of bgp_sessions that share it as their parent.
SELECT
  r.name                                                   AS rr_router,
  r.role,
  e_router.hetgnn_score                                    AS rr_anomaly_score,
  JSON_VALUE(e_router.anomaly_explanation, '$.top_feature') AS rr_top_feature,
  COUNT(DISTINCT e_bgp.node_id)                            AS affected_session_count
FROM NodeEmbedding  e_router
JOIN PhysicalRouter r       ON r.id          = e_router.node_id AND r.valid_end_ts IS NULL
JOIN VRF            v       ON v.router_id   = r.id             AND v.valid_end_ts IS NULL
JOIN BGPSession     s       ON s.vrf_id      = v.id             AND s.valid_end_ts IS NULL
JOIN NodeEmbedding  e_bgp  ON e_bgp.node_id  = s.id
WHERE e_router.node_type    = 'router'
  AND e_router.hetgnn_score > @threshold
  AND e_router.timestamp    = (SELECT MAX(timestamp) FROM NodeEmbedding)
  AND e_bgp.node_type       = 'bgp_session'
  AND e_bgp.hetgnn_score    > @threshold
  AND e_bgp.timestamp       = e_router.timestamp
  AND r.role                = 'RR'
GROUP BY r.name, r.role, rr_anomaly_score, rr_top_feature
ORDER BY affected_session_count DESC;
-- High affected_session_count from a single RR = confirmed RR crash root cause
```
