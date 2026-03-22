# Failure Pinpointing

A Heterogeneous Graph Neural Network (HetGNN) is trained in an unsupervised manner to learn what good looks like and to detect anomalies that can indicate the root cause of issues. 

**HetGNN's supports multiple types of nodes and edges** such as physical devices, logical protocols, traffic metrics, and configurations are mapped to to distinct node/edge types, HetGNNs are trained on interconnected failures. 

For root cause analysis, HetGNNs can distinctly model the relationship between a `PhysicalRouter`'s configuration changes, an `PhysicalInterface`'s telemetry metrics, and a `BGPSession`'s state changes. When an anomaly occurs, analyzing which sub-embedding (Config, Protocol, or Metric) deviated the most isolates the root cause layer. 

## Network Features

The types of nodes and edges representing the state of the simulated network are shown in the diagram below. 

![Network Features](/docs/drawings/gnn/hetgnn-features.drawio.svg)

| Node type | Features |
|-----------|----------|
| `router` | ospf state, state |
| `interface` | tx_drops, rx_drops, mtu_norm |
| `bgp_session` | bgp_state, pfx_count_norm |

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