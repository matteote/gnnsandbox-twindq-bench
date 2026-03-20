# Graph Neural Networks

Graph Neural Networks are trained on the [temporal network state data in Spanner](/docs/spanner/Readme.md) to identify the root cause of failures and predict the future state of the network. 

More information in this project can be found below:

* [Network GNN Research](/docs/gnn/research/Readme.md)
* [GNN Examples](/docs/gnn/examples/Readme.md)

## Failure Pinpointing

Heterogeneous GNNs (HetGNN) apply typed message passing: a message travelling over a `Physical_Link` edge is processed differently from one over a `BGP_Peering` edge, and a `Router_Config` node is encoded separately from an `Interface_Metrics` node. 

This decomposition produces branch-specific sub-embeddings (config, protocol, metrics) that can be inspected independently at inference time. 

When an anomaly is detected, the branch whose reconstruction error is highest directly identifies the fault layer: a config branch spike points to a fat-fingered route-map or RT misconfiguration; a protocol branch spike points to a BGP or OSPF control-plane failure; a metrics branch spike points to a hardware or traffic-level data-plane problem. 

### GNN Model

The figure below shows the HetGNN architecture. A snapshot of network state is translated into a set of features

![HetGNN Model](/docs/drawings/gnn/hetgnn-model.drawio.svg)


#### Features


#### Projection


#### Edge


#### Decoder


#### Error


### Spanner Snapshots

Spanner queries collect the spanshot information needed to train or run inference. 

![snapshot](/docs/drawings/gnn/snapshots.drawio.svg)


### Model Training

Training pipeline...

![pipeline](/docs/drawings/gnn/train_pipeline.drawio.svg)

