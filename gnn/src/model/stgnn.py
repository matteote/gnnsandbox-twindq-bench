import torch
import torch.nn as nn
from torch_geometric.nn import HeteroConv, GATv2Conv, SAGEConv

class STGNN(nn.Module):
    """
    Spatio-Temporal Graph Neural Network
    Combines spatial message passing with temporal recurrent sequences (GRU/LSTM).
    Useful for detecting degrading faults like SFPs, temporal bursts, and traffic spikes.
    """
    def __init__(self, metadata, hidden_channels=64, out_channels=64, num_layers=2, rnn_type='gru', time_steps=12):
        super(STGNN, self).__init__()
        self.hidden_channels = hidden_channels
        self.num_layers = num_layers
        self.time_steps = time_steps
        
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

        # Temporal Sequence Processing (Per-node-type)
        # Using module dict mapping node types to RNN cells to capture temporal histories
        self.rnns = nn.ModuleDict()
        for node_type in metadata[0]:
            if rnn_type.lower() == 'gru':
                self.rnns[node_type] = nn.GRU(input_size=hidden_channels, 
                                             hidden_size=hidden_channels, 
                                             num_layers=1, 
                                             batch_first=True)
            elif rnn_type.lower() == 'lstm':
                self.rnns[node_type] = nn.LSTM(input_size=hidden_channels, 
                                              hidden_size=hidden_channels, 
                                              num_layers=1, 
                                              batch_first=True)

        # Output projection for structural reconstruction (Anomaly detection)
        self.lin_dict = nn.ModuleDict()
        self.decoder_dict = nn.ModuleDict()

    def set_input_dims(self, input_dims):
        """Initializes projection matrices once input feature dimensions are known"""
        for node_type, dim in input_dims.items():
            # Initial projection into hidden space
            self.lin_dict[node_type] = nn.Linear(dim, self.hidden_channels)
            # Decoder for structural anomaly scoring
            self.decoder_dict[node_type] = nn.Linear(self.hidden_channels, dim)

    def forward(self, x_dict_seq, edge_index_dict, hidden_state_dict=None):
        """
        Forward pass processes a sequence of graph snapshots.
        x_dict_seq: dict of lists of tensors `[T, (N, F)]` OR dict of tensors `(N, T, F)`
        edge_index_dict: Assume static topology across the T snapshots for simplicity.
        """
        # Assume input x_dict_seq is formatted per node as standard [N, T, F] 
        # But HeteroConv expects [N, F]. We need to iterate over T.
        
        batch_h_histories = {nt: [] for nt in x_dict_seq.keys()}
        
        # Determine temporal length from the first tensor found
        T = list(x_dict_seq.values())[0].size(1) 
        
        # Step 1: Spatial Message Passing per temporal step
        for t in range(T):
            # Extract step t for all node types: x_t is [N, F]
            x_t_dict = {nt: x[:, t, :] for nt, x in x_dict_seq.items() if x.dim() == 3}
            
            # Initial projection
            h_t_dict = {}
            for nt in x_t_dict:
                if x_t_dict[nt] is not None and x_t_dict[nt].size(0) > 0:
                    h_t_dict[nt] = self.lin_dict[nt](x_t_dict[nt]).relu()
            
            # Filter edge_index_dict to only include edges where both node types have features
            filtered_edge_index_dict = {}
            for edge_type, edge_index in edge_index_dict.items():
                src_type, rel, tgt_type = edge_type
                if src_type in h_t_dict and tgt_type in h_t_dict and edge_index.size(1) > 0:
                    filtered_edge_index_dict[edge_type] = edge_index
            
            # Spatial Convolutions
            for conv in self.convs:
                h_t_dict_updated = conv(h_t_dict, filtered_edge_index_dict)
                # For nodes that didn't get updated (no incoming edges), keep previous representation
                for nt in h_t_dict.keys():
                    if nt in h_t_dict_updated:
                        h_t_dict[nt] = h_t_dict_updated[nt].relu()
                    # Else: h_t_dict[nt] remains unchanged from previous layer
            
            # Save history - use all node types from input
            for nt in x_t_dict.keys():
                if nt in h_t_dict:
                    batch_h_histories[nt].append(h_t_dict[nt])
                
        # Step 2: Temporal Processing (RNN)
        out_embeddings = {}
        recon_dict = {}
        new_hidden_states = {}
        
        for nt in batch_h_histories:
            # Stack histories to [N, T, hidden]
            h_seq = torch.stack(batch_h_histories[nt], dim=1) 
            
            # RNN expects [Batch, SeqLen, Features], where Batch is Nodes N.
            h_state = hidden_state_dict[nt] if hidden_state_dict and nt in hidden_state_dict else None
            
            rnn_out, h_n = self.rnns[nt](h_seq, h_state)
            
            # Extract the final temporal embedding (last step)
            # rnn_out is [N, T, hidden]
            final_embedding = rnn_out[:, -1, :] 
            out_embeddings[nt] = final_embedding
            new_hidden_states[nt] = h_n
            
            # Reconstruct the feature vector from the final embedding for anomaly scoring
            recon_dict[nt] = self.decoder_dict[nt](final_embedding)
            
        return recon_dict, out_embeddings, new_hidden_states
