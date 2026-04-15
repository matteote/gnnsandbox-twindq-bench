import torch
import torch.nn as nn
from torch_geometric.nn import HeteroConv, SAGEConv

class HetGNN(nn.Module):
    """
    Heterogeneous Graph Neural Network
    Employs designated processing pathways and independent reconstruction decoders 
    for entirely disparate node schemas (e.g., config lines, protocol states, metrics).
    Useful for root cause segregation.
    """
    def __init__(self, metadata, hidden_channels=64, out_channels=64, num_layers=2, dropout=0.3):
        super(HetGNN, self).__init__()
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.dropout = nn.Dropout(p=dropout)

        # Spatial Graph Convolutions
        self.convs = nn.ModuleList()
        for i in range(num_layers):
            conv_dict = {}
            for edge_type in metadata[1]:
                # SAGEConv supports bipartite message passing for heterogeneous graphs
                if i == 0:
                    conv_dict[edge_type] = SAGEConv((-1, -1), hidden_channels)
                else:
                    conv_dict[edge_type] = SAGEConv(hidden_channels, hidden_channels)
            self.convs.append(HeteroConv(conv_dict, aggr='mean'))

        # Projections
        self.lin_dict = nn.ModuleDict()

        # Type-specific decoders — one Linear(hidden_dim, input_dim) per node type.
        # Each decoder reconstructs the original features for its node type, enabling
        # reconstruction error to be isolated by layer (router / interface / bgp_session
        # / vrf / flow).  Node types and input dims are registered via set_input_dims().
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims):
        """Initializes projection matrices once input feature dimensions are known"""
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type] = nn.Linear(dim, self.hidden_channels)
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(self, x_dict, edge_index_dict):
        """
        Forward pass processes a snapshot containing highly disparate heterogeneous nodes.
        Returns segregated reconstructions which allows the training loop to apply
        weighted Loss functions (`alpha * L_config + beta * L_protocol + gamma * L_metrics`).
        """
        # Initial schema-specific projection into latent space
        # Dropout applied after ReLU to regularize the input projections
        h_dict = {}
        h_dict_initial = {}  # Keep track of initial projections
        for nt, x in x_dict.items():
            if x is not None and x.size(0) > 0:
                h_dict[nt] = self.dropout(self.lin_dict[nt](x).relu())
                h_dict_initial[nt] = h_dict[nt].clone()  # Store initial state

        # Filter edge_index_dict to only include edges where both node types have features
        filtered_edge_index_dict = {}
        for edge_type, edge_index in edge_index_dict.items():
            src_type, rel, tgt_type = edge_type
            if src_type in h_dict and tgt_type in h_dict and edge_index.size(1) > 0:
                filtered_edge_index_dict[edge_type] = edge_index

        # Heterogeneous Message Passing
        # Dropout applied after each conv layer to regularize intermediate representations
        for conv in self.convs:
            h_dict_updated = conv(h_dict, filtered_edge_index_dict)
            # For nodes that didn't get updated (no incoming edges), keep previous representation
            for nt in h_dict.keys():
                if nt in h_dict_updated:
                    h_dict[nt] = self.dropout(h_dict_updated[nt].relu())
                # Else: h_dict[nt] remains unchanged from previous layer
            
        # Reconstruct the specific feature vectors for anomaly scoring per type
        # Use the final h_dict which includes both updated and non-updated nodes
        recon_dict = {}
        out_embeddings = {}
        
        for nt in x_dict.keys():
            if nt in h_dict:
                out_embeddings[nt] = h_dict[nt]
                recon_dict[nt] = self.decoder_dict[nt](h_dict[nt])
            
        return recon_dict, out_embeddings
