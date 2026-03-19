import torch
import torch.nn as nn
from torch_geometric.nn import HeteroConv, GATv2Conv

class DGAT(nn.Module):
    """
    Directed Graph Attention Network (D-GAT)
    Focuses heavily on edge directionality and asymmetric attention weights.
    Primary use case: Silent black-holing, one-way reachability faults (like Route Target mismatches),
    where A -> B traffic succeeds but B -> A drops.
    """
    def __init__(self, metadata, hidden_channels=64, out_channels=64, num_heads=4, num_layers=2):
        super(DGAT, self).__init__()
        self.hidden_channels = hidden_channels
        self.num_heads = num_heads
        self.num_layers = num_layers
        
        self.convs = nn.ModuleList()
        # Initialize Heterogenous Graph Attention Convolutions
        for i in range(num_layers):
            conv_dict = {}
            for edge_type in metadata[1]:
                # GATv2Conv computes dynamic attention coefficients
                # We specifically want to allow attention weights to differ bidirectionally,
                # which GAT naturally supports for directed edge_indexes.
                if i == 0:
                    conv_dict[edge_type] = GATv2Conv((-1, -1), hidden_channels, heads=num_heads, add_self_loops=False)
                else:
                    conv_dict[edge_type] = GATv2Conv(hidden_channels * num_heads, hidden_channels, heads=num_heads, add_self_loops=False)
            self.convs.append(HeteroConv(conv_dict, aggr='sum'))

        self.lin_dict = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims):
        """Initializes projection matrices dynamically based on input feature dimensions"""
        for node_type, dim in input_dims.items():
            self.lin_dict[node_type] = nn.Linear(dim, self.hidden_channels)
            # Decoder for structural anomaly scoring from concatenated heads
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels * 4, dim)

    def forward(self, x_dict, edge_index_dict, edge_attr_dict=None):
        """
        Forward pass processes a single static snapshot with directed edges.
        Expects D-GAT specific edge definitions (forward and reverse distinct edges) 
        and asymmetric edge_attr features (traffic_asymmetry, drop_asymmetry) for detecting
        directional faults like silent blackholing.
        
        Args:
            x_dict: Node feature dictionary
            edge_index_dict: Edge index dictionary  
            edge_attr_dict: Optional edge attribute dictionary with asymmetry features
        """
        
        # Initial projection
        h_dict = {}
        h_dict_initial = {}  # Keep track of initial projections
        for nt, x in x_dict.items():
            if x is not None and x.size(0) > 0:
                h_dict[nt] = self.lin_dict[nt](x).relu()
                h_dict_initial[nt] = h_dict[nt].clone()  # Store initial state
        
        # Filter edge_index_dict to only include edges where both node types have features
        filtered_edge_index_dict = {}
        filtered_edge_attr_dict = {}
        for edge_type, edge_index in edge_index_dict.items():
            src_type, rel, tgt_type = edge_type
            if src_type in h_dict and tgt_type in h_dict and edge_index.size(1) > 0:
                filtered_edge_index_dict[edge_type] = edge_index
                # Include edge attributes if provided
                if edge_attr_dict and edge_type in edge_attr_dict:
                    filtered_edge_attr_dict[edge_type] = edge_attr_dict[edge_type]
            
        # Message Passing with edge directionality driving Attention weights
        for layer_idx, conv in enumerate(self.convs):
            # GAT internally learns \alpha_{i,j} for edge j->i. 
            # In D-GAT, we want to capture traffic asymmetries, so the graph pipeline
            # must emit separate forward/backward edges containing interface drop metrics.
            h_dict_updated = conv(h_dict, filtered_edge_index_dict)
            
            # For nodes that didn't get updated (no incoming edges), keep previous representation
            # but expand dimensions to match GAT multi-head output (concatenates heads)
            expected_dim = self.hidden_channels * self.num_heads
            for nt in h_dict.keys():
                if nt in h_dict_updated:
                    h_dict[nt] = h_dict_updated[nt].relu()
                else:
                    # Node didn't receive messages - expand its representation to match output dimension
                    # GAT concatenates heads: output_dim = hidden_channels * num_heads
                    current_h = h_dict[nt]
                    current_dim = current_h.size(1)
                    
                    # Only expand if current dimension doesn't match expected
                    if current_dim < expected_dim:
                        # Repeat the representation across heads to maintain the same effective embedding
                        repeat_factor = expected_dim // current_dim
                        h_dict[nt] = current_h.repeat(1, repeat_factor)
                    # else: already at correct dimension, keep as is
            
        # Output extraction and reconstruction
        # Use the final h_dict which includes both updated and non-updated nodes
        recon_dict = {}
        out_embeddings = {}
        
        for nt in x_dict.keys():
            if nt in h_dict:
                out_embeddings[nt] = h_dict[nt]
                recon_dict[nt] = self.decoder_dict[nt](h_dict[nt])
            
        return recon_dict, out_embeddings
