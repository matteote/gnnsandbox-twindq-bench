# Spanner Studio Demo Queries - L3VPN Fault Analysis with GNN Embeddings

This document contains ready-to-execute queries for demonstrating network topology visualization, metrics analysis, and GNN embedding-based root cause analysis in Spanner Studio.

## Prerequisites

1. base demo deployed: `install.sh c && install.sh -s`
2. Network deployed: `kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml`
   - wait until all vyosrouters are ready `watch kubectl get vyosrouter -n default`
3. Traffic running: `kubectl apply -f environment/telco-lab/l3vpn-test.yaml`
4. GNN model training and inferencing deployed: `install.sh --deploy gnn`
5. Metrics being collected by metricscollector: `install.sh --deploy metricscollector`
---

## A) Show Live Topology Visualization

### Query A1: Hub-Spoke Topology Overview

Shows the complete L3VPN hub-spoke network with all PE routers and their connections.

```gql
GRAPH networkGraph
MATCH (router:PhysicalRouter)-[e:HasInterface]->(intf:PhysicalInterface)
WHERE router.role IN ('PE', 'pe')
  AND router.valid_end_ts IS NULL
  AND intf.valid_end_ts IS NULL
RETURN TO_JSON(router) AS router, TO_JSON(e) AS edge, TO_JSON(intf) AS interface
```

**Expected Result**: PE1 (Oxford), PE2 (Cambridge), PE3 (Brighton) with their interface counts.

---

### Query A2: Physical Link Topology (Graph Traversal)

Visualizes the physical connectivity between routers through interfaces and links.

```gql
GRAPH networkGraph
MATCH (r1:PhysicalRouter)-[e1:HasInterface]->(i1:PhysicalInterface)
      -[e2:ConnectsTo]->(link:PhysicalLink)
      -[e3:LinkedTo]->(i2:PhysicalInterface)
      <-[e4:HasInterface]-(r2:PhysicalRouter)
WHERE r1.valid_end_ts IS NULL 
  AND r2.valid_end_ts IS NULL
  AND i1.valid_end_ts IS NULL
  AND i2.valid_end_ts IS NULL
  AND link.valid_end_ts IS NULL
RETURN 
    TO_JSON(r1) AS router1,
    TO_JSON(e1) AS r1_to_i1,
    TO_JSON(i1) AS interface1,
    TO_JSON(e2) AS i1_to_link,
    TO_JSON(link) AS link,
    TO_JSON(e3) AS link_to_i2,
    TO_JSON(i2) AS interface2,
    TO_JSON(e4) AS r2_to_i2,
    TO_JSON(r2) AS router2
```

**Expected Result**: Shows connections like `pe1 (eth2) -[pe1-ce1-spoke]-> (eth1) ce1-spoke`.

---

### Query A3: VRF and L3VPN Service Topology

Shows the L3VPN service layer with VRFs and route targets.

```gql
GRAPH networkGraph
MATCH (router:PhysicalRouter)<-[e1:LocatedOn]-(vrf:VRF)-[e2:RealizesVPN]->(vpn:L3VPNService)
WHERE router.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND vpn.valid_end_ts IS NULL
RETURN 
    TO_JSON(router) AS router,
    TO_JSON(e1) AS router_to_vrf,
    TO_JSON(vrf) AS vrf,
    TO_JSON(e2) AS vrf_to_vpn,
    TO_JSON(vpn) AS vpn
```

**Expected Result**: Shows BLUE_SPOKE on PE1/PE3 and BLUE_HUB on PE2.

---

### Query A4: Complete Network Graph (All Node Types)

Full topology including routers, interfaces, links, VRFs, BGP sessions.

```sql
-- SQL query to show all current network elements
SELECT 
    'Router' AS element_type,
    name,
    role AS detail,
    location_city AS location
FROM PhysicalRouter
WHERE valid_end_ts IS NULL

UNION ALL

SELECT 
    'Interface' AS element_type,
    CONCAT(
        (SELECT name FROM PhysicalRouter WHERE id = router_id AND valid_end_ts IS NULL),
        '-',
        name
    ) AS name,
    status AS detail,
    NULL AS location
FROM PhysicalInterface
WHERE valid_end_ts IS NULL
```

---

## B) Network Metrics Analysis (SQL)

### Query B1: Current Interface Metrics Summary

Shows the latest metrics for all interfaces.

```sql
SELECT 
    r.name AS router_name,
    r.role AS router_role,
    m.interface,
    m.metric_name,
    ROUND(AVG(m.value), 2) AS avg_value,
    MAX(m.timestamp) AS latest_timestamp
FROM NetworkMetrics m
JOIN PhysicalRouter r ON m.node_name = r.name AND r.valid_end_ts IS NULL
WHERE m.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
  AND m.interface IS NOT NULL
  AND m.metric_name IN (
      'node_network_receive_bytes_total',
      'node_network_transmit_bytes_total',
      'node_network_receive_errs_total',
      'node_network_transmit_errs_total'
  )
GROUP BY r.name, r.role, m.interface, m.metric_name
ORDER BY r.name, m.interface, m.metric_name;
```

**Expected Result**: RX/TX bytes and errors for each router's interfaces.

---

### Query B2: Traffic Hotspots (Highest Throughput)

Identifies interfaces with highest traffic.

```sql
WITH interface_traffic AS (
    SELECT 
        node_name AS router,
        interface,
        SUM(CASE WHEN metric_name = 'node_network_receive_bytes_total' THEN value ELSE 0 END) AS total_rx,
        SUM(CASE WHEN metric_name = 'node_network_transmit_bytes_total' THEN value ELSE 0 END) AS total_tx
    FROM NetworkMetrics
    WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
      AND metric_name IN ('node_network_receive_bytes_total', 'node_network_transmit_bytes_total')
    GROUP BY node_name, interface
)
SELECT 
    router,
    interface,
    ROUND(total_rx / 1024 / 1024, 2) AS rx_mb,
    ROUND(total_tx / 1024 / 1024, 2) AS tx_mb,
    ROUND((total_rx + total_tx) / 1024 / 1024, 2) AS total_mb
FROM interface_traffic
WHERE total_rx + total_tx > 0
ORDER BY total_rx + total_tx DESC
LIMIT 20;
```

---

### Query B3: Error Rate by Router

Detects interfaces with errors.

```sql
SELECT 
    m.node_name AS router,
    m.interface,
    SUM(m.value) AS total_errors,
    COUNT(*) AS error_count,
    MAX(m.timestamp) AS latest_error_time
FROM NetworkMetrics m
WHERE m.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
  AND m.metric_name IN ('node_network_receive_errs_total', 'node_network_transmit_errs_total')
  AND m.value > 0
GROUP BY m.node_name, m.interface
ORDER BY total_errors DESC;
```

**Expected Result**: Should be empty in healthy network, shows errors during faults.

---

### Query B4: Time-Series Metrics (Last Hour)

Traffic trends over time for specific interface.

```sql
SELECT 
    TIMESTAMP_TRUNC(timestamp, MINUTE) AS time_bucket,
    metric_name,
    AVG(value) AS avg_value,
    MAX(value) AS max_value
FROM NetworkMetrics
WHERE node_name = 'pe1'
  AND interface = 'eth2'
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
  AND metric_name IN ('node_network_receive_bytes_total', 'node_network_transmit_bytes_total')
GROUP BY time_bucket, metric_name
ORDER BY time_bucket DESC, metric_name;
```

---

## C) Embeddings with Live Topology (GQL + SQL)

### Query C1: Latest Embeddings for All Routers (GQL)

Graph query showing routers and their most recent embeddings.

```gql
GRAPH networkGraph
MATCH (router:PhysicalRouter)-[:RouterHasEmbedding]->(emb:NodeEmbedding)
WHERE router.valid_end_ts IS NULL
  AND emb.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
RETURN 
    router.name AS router_name,
    router.role AS router_role,
    router.location_city AS city,
    emb.hetgnn_score AS hetgnn_score,
    emb.anomaly_explanation AS explanation,
    emb.timestamp AS embedding_time
ORDER BY emb.hetgnn_score DESC
```

**Expected Result**: All PE/P/CE routers with their anomaly scores.

---

### Query C2: Embeddings for Interfaces (GQL)

Shows interface-level embeddings and their anomalies.

```gql
GRAPH networkGraph
MATCH (router:PhysicalRouter)-[:HasInterface]->(intf:PhysicalInterface)
      -[:InterfaceHasEmbedding]->(emb:NodeEmbedding)
WHERE router.valid_end_ts IS NULL
  AND intf.valid_end_ts IS NULL
  AND emb.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
RETURN 
    router.name AS router_name,
    intf.name AS interface_name,
    intf.status AS interface_status,
    emb.hetgnn_score AS hetgnn_score,
    emb.anomaly_explanation AS explanation
ORDER BY emb.hetgnn_score DESC
LIMIT 50
```

**Expected Result**: Interface embeddings with state/errors/rx/tx feature attributions.

---

### Query C3: High Anomaly Score Nodes (SQL)

Quickly find nodes with anomalies using SQL for fast filtering.

```sql
SELECT 
    ne.node_id,
    ne.node_type,
    ne.hetgnn_score,
    JSON_VALUE(ne.anomaly_explanation, '$.primary_feature') AS primary_issue,
    JSON_VALUE(ne.anomaly_explanation, '$.error_value') AS error_value,
    ne.timestamp
FROM NodeEmbedding ne
WHERE ne.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
  AND ne.hetgnn_score > 0.1
ORDER BY ne.hetgnn_score DESC, ne.timestamp DESC
LIMIT 20;
```

**Expected Result**: During faults, shows PE1 or PE2 with high scores and "Config (Semantic)" as primary issue.

---

### Query C4: Embedding Visualization Data

Extract embedding vectors for visualization tools.

```sql
SELECT 
    node_id,
    node_type,
    hetgnn_score,
    hetgnn_embedding,
    timestamp
FROM NodeEmbedding
WHERE timestamp = (
    SELECT MAX(timestamp)
    FROM NodeEmbedding
)
ORDER BY node_type, node_id;
```

**Use Case**: Export this to visualize embeddings in 2D/3D using UMAP or t-SNE.

---

# Introduce the Fault

## Network Architecture

### Hub-Spoke Topology
- **Hub Router**: PE2 (Cambridge) - Aggregates traffic from all spokes
- **Spoke Routers**: 
  - PE1 (Oxford) - Connected to CE1-spoke (Sheffield)
  - PE3 (Brighton) - Connected to CE2-spoke (Liverpool)

### Route Target Configuration (Correct)
- **Spokes (PE1, PE3)**: 
  - Export: `65035:1011` (spoke routes)
  - Import: `65035:1030` (hub routes)
- **Hub (PE2)**:
  - Export: `65035:1030` (hub routes)
  - Import: `65035:1011`, `65035:1030` (both spoke and hub routes)

### Route Distinguishers (Correct)
- **PE1**: `10.50.50.1:1011`
- **PE2**: `10.80.80.1:1011`
- **PE3**: `10.60.60.1:1011`

### Fault 1: RT Import Misconfiguration (Severe)

**File**: `l3vpn-hub-spoke-fault1-rt-import.yaml`

**Misconfiguration**:
- **Location**: PE1 router, BLUE_SPOKE VRF
- **Change**: `rt_import` changed from `["65035:1030"]` to `["65035:9999"]`
- **Line**: ~970 in the VyOSL3VPN section

**Impact**:
- **Severity**: Complete connectivity failure between spoke1 and hub
- **Symptom**: PE1 cannot import routes from the hub (PE2)
- **Affected Traffic**: 
  - dev1 (10.100.1.10) ❌ devhub (10.100.2.10)
  - dev1 ✅ dev2 (via hub) - works if hub can still reach dev1's routes
- **BGP Behavior**: PE1 will reject all VPNv4 routes with RT `65035:1030`

**GNN Detection Signature**:
- **Router Embedding**: PE1 config embedding shows high reconstruction error
- **Feature Attribution**: Config (semantic) feature is primary anomaly driver
- **Temporal Signal**: Embedding diverges at fault injection time
- **Interface Metrics**: pe1-eth2 shows dropped traffic/zero throughput
- **Graph Propagation**: Anomaly localizes to PE1 node and connected interfaces

## Misconfigure the system

```
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault1-rt-import.yaml
```

Wait a minute for the embeddings to make their way through

---

## D) Historical Embedding Comparison (Before/After Fault)

### Query D1: Baseline vs Current Comparison

Compares embeddings before and after a specific time (fault injection).

**Replace `'2026-02-19 08:00:00 UTC'` with your fault injection timestamp.**

```sql
WITH baseline AS (
    SELECT 
        node_id,
        node_type,
        hetgnn_score AS baseline_score,
        hetgnn_embedding AS baseline_embedding,
        timestamp AS baseline_time
    FROM NodeEmbedding
    WHERE timestamp < TIMESTAMP('2026-02-19 08:00:00 UTC')  -- BEFORE FAULT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp DESC) = 1
),
current AS (
    SELECT 
        node_id,
        node_type,
        hetgnn_score AS current_score,
        hetgnn_embedding AS current_embedding,
        anomaly_explanation,
        timestamp AS current_time
    FROM NodeEmbedding
    WHERE timestamp >= TIMESTAMP('2026-02-19 08:00:00 UTC')  -- AFTER FAULT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp ASC) = 1
)
SELECT 
    c.node_id,
    c.node_type,
    b.baseline_score,
    c.current_score,
    ROUND(c.current_score - b.baseline_score, 4) AS score_delta,
    CASE 
        WHEN c.current_score > b.baseline_score * 2 THEN 'CRITICAL'
        WHEN c.current_score > b.baseline_score * 1.5 THEN 'WARNING'
        ELSE 'NORMAL'
    END AS status,
    JSON_VALUE(c.anomaly_explanation, '$.primary_feature') AS root_cause_feature,
    b.baseline_time,
    c.current_time
FROM current c
LEFT JOIN baseline b ON c.node_id = b.node_id
ORDER BY score_delta DESC;
```

**Expected Result**: PE1 shows large score_delta with "Config (Semantic)" as root cause after Fault 1 injection.

---

### Query D2: Embedding Evolution Over Time

Shows how a specific node's embedding changed over time.

```sql
SELECT 
    timestamp,
    hetgnn_score,
    JSON_VALUE(anomaly_explanation, '$.primary_feature') AS primary_feature,
    JSON_VALUE(anomaly_explanation, '$.error_value') AS error_value,
    -- Show first 5 dimensions of embedding for trending
    hetgnn_embedding[OFFSET(0)] AS emb_dim_0,
    hetgnn_embedding[OFFSET(1)] AS emb_dim_1,
    hetgnn_embedding[OFFSET(2)] AS emb_dim_2,
    hetgnn_embedding[OFFSET(3)] AS emb_dim_3,
    hetgnn_embedding[OFFSET(4)] AS emb_dim_4
FROM NodeEmbedding
WHERE node_id = 'pe1'  -- Focus on specific router
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
ORDER BY timestamp ASC;
```

**Use Case**: Plot anomaly_score over time to see when fault occurred.

---

### Query D3: Root Cause Identification via SQL Traversal

Starting from a failing device, trace back through the topology to find the misconfigured PE
router. BGP peering is derived by matching `BGPSession.peer_ip` against
`PhysicalInterface.ip_address` — there is no `PeersWith` graph edge.

```sql
-- Trace: failing device → CE router → PE BGP session (via peer_ip match) → VRF → PE embedding
SELECT
    d.name                                                        AS failing_device,
    ce.name                                                       AS ce_router,
    pe.name                                                       AS pe_router,
    vrf.name                                                      AS vrf_name,
    vrf.rd                                                        AS route_distinguisher,
    pe_emb.hetgnn_score                                           AS pe_hetgnn_score,
    pe_emb.anomaly_explanation                                    AS root_cause
FROM Device d
-- Device connects directly to a specific CE router interface (the gateway interface)
JOIN PhysicalInterface ce_intf ON d.interface_id = ce_intf.id
-- Navigate up to the CE router
JOIN PhysicalRouter ce ON ce_intf.router_id = ce.id
-- CE router has an interface whose IP matches a PE BGP session's peer_ip
JOIN PhysicalInterface ce_pe_intf ON ce_pe_intf.router_id = ce.id
JOIN BGPSession bgp_pe ON bgp_pe.peer_ip = ce_pe_intf.ip_address
-- The PE BGP session belongs to a VRF on a PE router
JOIN VRF vrf ON bgp_pe.vrf_id = vrf.id
JOIN PhysicalRouter pe ON vrf.router_id = pe.id
-- Latest embedding for that PE router within the last 10 minutes
JOIN NodeEmbedding pe_emb
    ON pe_emb.node_id = pe.id
    AND pe_emb.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
WHERE d.name = 'dev1'           -- Device with connectivity issue
  AND d.valid_end_ts IS NULL
  AND ce.valid_end_ts IS NULL
  AND ce_intf.valid_end_ts IS NULL
  AND bgp_pe.valid_end_ts IS NULL
  AND vrf.valid_end_ts IS NULL
  AND pe.valid_end_ts IS NULL
  AND pe_emb.hetgnn_score > 0.1
ORDER BY pe_emb.hetgnn_score DESC
```

**Expected Result**: Traces from dev1 → ce1-spoke → pe1, showing PE1 has high anomaly in config.

---

### Query D4: Before/After Config Change Detection

Detects which routers had configuration changes by comparing config embeddings.

```sql
WITH config_changes AS (
    SELECT 
        node_id,
        timestamp,
        hetgnn_score,
        JSON_VALUE(anomaly_explanation, '$.feature_errors."Config (Semantic)"') AS config_error,
        LAG(hetgnn_score) OVER (PARTITION BY node_id ORDER BY timestamp) AS prev_score
    FROM NodeEmbedding
    WHERE node_type IN ('PE Router', 'P Router', 'CE Router')
      AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
)
SELECT 
    node_id,
    timestamp AS change_detected_at,
    hetgnn_score AS current_score,
    prev_score,
    ROUND(hetgnn_score - COALESCE(prev_score, 0), 4) AS score_jump,
    CAST(config_error AS FLOAT64) AS config_error_magnitude
FROM config_changes
WHERE hetgnn_score - COALESCE(prev_score, 0) > 0.2  -- Significant change
ORDER BY score_jump DESC;
```

**Expected Result**: Shows timestamp when PE1's config changed (fault injection moment).

---

### Query D5: Embedding Distance Calculation

Calculate L2 distance between baseline and current embeddings to quantify change.

```sql
WITH baseline AS (
    SELECT 
        node_id,
        hetgnn_embedding AS baseline_emb
    FROM NodeEmbedding
    WHERE timestamp < TIMESTAMP('2026-02-19 08:00:00 UTC')  -- BEFORE FAULT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp DESC) = 1
),
current AS (
    SELECT 
        node_id,
        hetgnn_embedding AS current_emb,
        hetgnn_score
    FROM NodeEmbedding
    WHERE timestamp >= TIMESTAMP('2026-02-19 08:00:00 UTC')  -- AFTER FAULT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp ASC) = 1
)
SELECT 
    c.node_id,
    c.hetgnn_score,
    -- Calculate Euclidean distance between embeddings
    SQRT(
        (SELECT SUM(POW(b_val - c_val, 2))
         FROM UNNEST(b.baseline_emb) AS b_val WITH OFFSET AS idx
         JOIN UNNEST(c.current_emb) AS c_val WITH OFFSET AS idx2
         ON idx = idx2)
    ) AS embedding_distance
FROM current c
JOIN baseline b ON c.node_id = b.node_id
ORDER BY embedding_distance DESC;
```

**Expected Result**: PE1 shows largest embedding distance after misconfiguration.

---

## Demo Flow Recommendations

### Initial State (Healthy Network)
1. Run **Query A1-A3** to show topology
2. Run **Query B1-B2** to show healthy metrics
3. Run **Query C1-C2** to show baseline embeddings (low anomaly scores)

### Inject Fault
```bash
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault1-rt-import.yaml
```

Wait 2-3 minutes for operator to reconfigure and metrics to collect.

### Trigger GNN Inference
```bash
curl -X POST http://<gnn-serve-endpoint>:8080/inference
```

### Post-Fault Analysis
1. Run **Query C3** - Should show PE1 with high anomaly score
2. Run **Query D1** - Shows before/after comparison with PE1 delta
3. Run **Query D3** - Graph traversal from dev1 to PE1 root cause
4. Run **Query D4** - Pinpoints exact timestamp of config change

### Restore and Verify
```bash
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml
```

Run Query C3 again - anomaly scores should drop back to baseline.

---

## Tips for Spanner Studio

1. **Enable Query Profiling**: Click the "Explain" button to see query execution plans
2. **Use LIMIT**: Add `LIMIT 100` to exploratory queries to avoid timeouts
3. **Time Range Filtering**: Adjust timestamps in WHERE clauses to match your deployment time
4. **Export Results**: Use "Download as CSV" for further analysis in notebooks
5. **Save Queries**: Bookmark frequently used queries in Spanner Studio

## Troubleshooting

### No Embeddings Found
- Verify GNN serve is running: `kubectl get pods -l app=gnn-serve`
- Check if inference was triggered: `curl http://<endpoint>:8080/inference`
- Verify NodeEmbedding table has data: `SELECT COUNT(*) FROM NodeEmbedding`

### Graph Queries Return Empty
- Ensure property graph is created: Check `environment/spanner.j2` was applied
- Verify edge views exist: `SELECT * FROM RouterHasEmbedding_Edge LIMIT 5`
- Check that routers have `valid_end_ts IS NULL` (current version)

### Metrics Not Showing
- Verify metricscollector is running
- Check NetworkMetrics table: `SELECT COUNT(*) FROM NetworkMetrics WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)`
- Ensure interface names match between topology and metrics

---

## Additional Resources

- **Spanner Graph Documentation**: https://cloud.google.com/spanner/docs/graph/overview
- **GQL Syntax**: https://cloud.google.com/spanner/docs/graph/gql-language
- **Temporal Query Patterns**: See `docs/spanner_model.md`
- **Fault Injection Guide**: `environment/telco-lab/FAULT_INJECTION.md`
