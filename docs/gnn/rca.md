# Failure Pinpointing

A HetGNN is trained on historical network topology and state to identify anomalies that could point to the root cause of an issue. The HetGNN is trained in an unsupervised manner to learn what good looks like.

[The following faults can be introduced to the system](/docs/network/FAULT_INJECTION.md)

For root cause analysis, HetGNNs can distinctly model the relationship between a `PhysicalRouter`'s configuration changes, an `PhysicalInterface`'s telemetry metrics, and a `BGPSession`'s state changes. When an anomaly occurs, analyzing which deviated the most isolates the root cause layer. 

## Network Features

The following types of nodes and edges capture the state of the vyos virtual network.

![Network Features](/docs/drawings/gnn/hetgnn-features.drawio.svg)

Features are populated by joining two data sources at snapshot time: the **topology tables** (SCD Type 2 rows from the operator) and the **`NetworkMetrics` table** (time-series data written by `logservices/metricscollector`). Each feature is averaged over the 5-minute snapshot window before being log-transformed and `StandardScaler`-normalised.

#### `router` node

| Feature | Source metric | Derivation | Description |
|---------|---------------|------------|-------------|
| `cpu` | `node_load1` | Mean value over snapshot window (`ALIGN_MEAN`) | 1-minute CPU load average reported by `node_exporter`. Not bounded — values >1 indicate the router is under load. |
| `mem` | `node_memory_MemAvailable_bytes` | `min(bytes / 4 GiB, 1.0)` | Available memory normalised to [0, 1] against a 4 GiB baseline. Higher values mean more memory is free; lower values indicate memory pressure. |
| `ospf_num_routes` | `frr_route_total` (afi=`ipv4`) | Raw count from `frr_exporter` | Total IPv4 routes currently in the FRR routing table. A sudden drop signals a routing convergence event or protocol failure. |

#### `interface` node

| Feature | Source metric | Derivation | Description |
|---------|---------------|------------|-------------|
| `rx_drops` | `node_network_receive_drop_total` | Per-second drop rate (`ALIGN_RATE`) | Rate of inbound packets dropped at the interface. Non-zero values indicate congestion, buffer exhaustion, or MTU mismatches. |
| `tx_drops` | `node_network_transmit_drop_total` | Per-second drop rate (`ALIGN_RATE`) | Rate of outbound packets dropped at the interface. Typically caused by egress queue overflow. |
| `mtu_norm` | `node_network_mtu_bytes` | `bytes / 9000` | Interface MTU normalised against a 9000-byte jumbo-frame maximum. A value of ~0.167 represents a standard 1500-byte MTU; a change across a link boundary is a common fault trigger. |

#### `bgp_session` node

| Feature | Source | Derivation | Description |
|---------|--------|------------|-------------|
| `bgp_state` | `BGPSession.status` (Spanner topology table) | `1.0` = Established, `0.0` = all other states | Session establishment state maintained by the network operator. A transition to 0.0 indicates the peering has dropped. |
| `pfx_count_norm` | `frr_bgp_peer_prefixes_advertised_count_total` | Raw prefix count (log-transformed at scaler-fit time) | Number of prefixes the local router is advertising to the peer. An unexpected drop or spike is a strong fault indicator for route-leak or withdrawal events. |

## GNN Model Architecture

The figure below shows the HetGNN model architecture.

![HetGNN Model](/docs/drawings/gnn/hetgnn-model.drawio.svg)

#### Features 

The scalar strategy ensures that diverse numerical features—such as interface traffic counters, metric velocities, and protocol state counts—are consistently normalized before being fed into the model. 

Raw metrics (e.g., `rx_bytes`, `tx_drops`) are log-transformed to compress long-tailed distributions. A `StandardScaler` is then fitted for each specific metric and node type across a history of network snapshots to standardize the values. 

The fitted scalers are serialized as `scalers.pkl` and stored in Cloud Storage. Both the model training pipeline and the real-time inference service load this to transform incoming graph data, ensuring that new features are always normalized against the same historical baseline distribution that the HetGNN was trained on.

#### Projection

Initial schema-specific projection layers map highly disparate features of varying dimensions into a shared, unified latent space using a linear transformation followed by a ReLU activation.

#### Edge Passing

The model utilizes `SAGEConv` within a `HeteroConv` wrapper to perform bipartite message passing over the heterogeneous graph structure. Nodes update their latent representations by aggregating structural information from their connected neighbors, linking different domains like a Physical Router to its Interfaces.

#### Decoder

Independent, schema-specific linear decoders attempt to reconstruct the exact original input feature vectors from the latent embeddings. By having dedicated pathways instead of a generic decoder, the model isolates reconstruction for Root Cause specific domains.

#### Error Calculation

Root causes are isolated by segregating the Mean Squared Error (MSE) loss. Losses are calculated individually per branch enabling a weighted multi-task objective. A contrastive diversity penalty is also applied. High reconstruction error in a specific branch clearly pinpoints which layer of the network is responsible for a failure.

## Spanner Snapshots

Spanner queries collect the spanshot information needed to train or run inference. 

![snapshot](/docs/drawings/gnn/snapshots.drawio.svg)


## Model Training

The training pipeline is shown in the figure below. 

![pipeline](/docs/drawings/gnn/train_pipeline.drawio.svg)

* **Ingest Snapshots**: the last 100 snapshots of the network are built from Spanner. Snapshots currently spane 5 minute periods. These snapshots are then stored in a Storage bucket ready to be used in training. 
* **Fit scalars**: The feature scaling weights to convert snapshot feature data are calculated and stored in the storage bucket. 
* **Train Model**: The snapshot data is used to train the HetGNN model. 
* **Evaluate Model**: Evaluate the model error and reject if it is not accurate enough. 
* **Register Model**: If accurate register the model. 

## Model Inferencing

Periodically, the current state of the network is run through the failure pinpointing GNN. Embeddings for each node in the network model is generated and stored back in spanner for further analysis. 

![inferencing](/docs/drawings/gnn/inference.drawio.svg)

The flow below is run every 5 minutes to analyse the current state of the network:

* **Load Scalars**: Load the feature scaling weights from storage bucket. 
* **Load Weights**: Load training weights from storage bucket
* **Get Latest Snapshot**: Build the latest snapshot of the current state of the network from Spanner
* **Run Inference**: Run the snapshot through the model to calculate embeddings for all nodes. 
* **Update Embeddings**: Store embeddings for each node back in Spanner for further analysis