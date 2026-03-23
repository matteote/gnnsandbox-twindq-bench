import json
import os
import glob
import numpy as np
import torch
from torch_geometric.data import HeteroData
from sklearn.preprocessing import StandardScaler
import joblib
import logging

logger = logging.getLogger(__name__)

# Constants
HIDDEN_CHANNELS = 64
OUT_CHANNELS = 32
INTERVAL_MINUTES = 1
NUM_HEADS = 4
NUM_LAYERS = 2

# Shared Environment Configuration
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "networktopology-db")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts")

class GraphBuilder:
    def __init__(self, scaler_path="scalers.pkl"):
        logger.info(f"Initializing GraphBuilder with scaler_path: {scaler_path}")
        self.scaler_path = scaler_path
        self.scalers = {}
        
        self.global_id_map = {
            "router": {}, "interface": {}
        }
        
        logger.debug(f"Global ID map initialized with node types: {list(self.global_id_map.keys())}")
        
    def init_config_encoder(self):
        # Stub for legacy compatibility. No longer used.
        pass

    def fit_scalers(self, snapshot_objects):
        logger.info(f"Fitting scalers on {len(snapshot_objects)} snapshot objects")
        
        # pfx_count_norm is now a router feature (BGP prefix count aggregated across
        # all peers on the router), not a separate bgp_session node feature.
        all_metrics = {
            "router": {"ospf_num_routes": [], "cpu": [], "mem": [], "pfx_count_norm": []},
            "interface": {"tx_drops": [], "rx_drops": [], "mtu_norm": []},
        }
        
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                
                if ntype == "router":
                    all_metrics["router"]["ospf_num_routes"].append(np.log1p(float(node.get("ospf_num_routes") or 0.0)))
                    all_metrics["router"]["cpu"].append(float(node.get("cpu") or 0.0))
                    all_metrics["router"]["mem"].append(float(node.get("mem") or 0.0))
                    all_metrics["router"]["pfx_count_norm"].append(np.log1p(float(node.get("pfx_count_norm") or 0.0)))
                    
                elif ntype == "interface":
                    all_metrics["interface"]["tx_drops"].append(np.log1p(float(node.get("tx_drops") or 0.0)))
                    all_metrics["interface"]["rx_drops"].append(np.log1p(float(node.get("rx_drops") or 0.0)))
                    all_metrics["interface"]["mtu_norm"].append(float(node.get("mtu_norm") or 0.0))

        for map_type, metrics in all_metrics.items():
            if map_type not in self.scalers:
                self.scalers[map_type] = {}
            for metric, values in metrics.items():
                if values:
                    scaler = StandardScaler()
                    scaler.fit(np.array(values).reshape(-1, 1))
                    self.scalers[map_type][metric] = scaler
                    logger.debug(f"Fitted scaler for {map_type}.{metric} with {len(values)} values")
        
        logger.info("Building global ID map")
        self.global_id_map = {k: {} for k in self.global_id_map.keys()}
        
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                if ntype in self.global_id_map:
                    if nid not in self.global_id_map[ntype]:
                        self.global_id_map[ntype][nid] = len(self.global_id_map[ntype])
        
        for ntype, id_map in self.global_id_map.items():
            logger.info(f"Global ID map for {ntype}: {len(id_map)} unique nodes")

    def save_scalers(self):
        logger.info(f"Saving scalers and ID map to {self.scaler_path}")
        joblib.dump({"scalers": self.scalers, "id_map": self.global_id_map}, self.scaler_path)
        
    def load_scalers(self):
        if os.path.exists(self.scaler_path):
            data = joblib.load(self.scaler_path)
            self.scalers = data["scalers"]
            self.global_id_map = data["id_map"]
            return True
        return False

    def process_snapshot(self, data):
        logger.info("Processing network snapshot")
        hetero_data = HeteroData()
        features_dict = {}
        input_dims = {}
        
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            
            dim = 0
            if ntype == "router": dim = 5  # state, ospf_num_routes, cpu, mem, pfx_count_norm
            elif ntype == "interface": dim = 4  # state, tx_drops, rx_drops, mtu_norm

            if count > 0:
                features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
                input_dims[ntype] = dim

        def get_scaled(map_type, metric, val):
            if map_type in self.scalers and metric in self.scalers[map_type]:
                return self.scalers[map_type][metric].transform([[val]])[0][0]
            return val

        id_to_type = {}

        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            id_to_type[nid] = ntype
            
            if ntype not in self.global_id_map or nid not in self.global_id_map[ntype]:
                continue
                
            idx = self.global_id_map[ntype][nid]
            state = node.get("state", 0.0)
            
            if ntype == "router":
                ospf = np.log1p(float(node.get("ospf_num_routes") or 0.0))
                cpu  = float(node.get("cpu") or 0.0)
                mem  = float(node.get("mem") or 0.0)
                pfx  = np.log1p(float(node.get("pfx_count_norm") or 0.0))
                features_dict[ntype][idx] = np.array([
                    state,
                    get_scaled("router", "ospf_num_routes", ospf),
                    get_scaled("router", "cpu", cpu),
                    get_scaled("router", "mem", mem),
                    get_scaled("router", "pfx_count_norm", pfx),
                ])
                
            elif ntype == "interface":
                tx_drops = np.log1p(float(node.get("tx_drops") or 0.0))
                rx_drops = np.log1p(float(node.get("rx_drops") or 0.0))
                mtu      = float(node.get("mtu_norm") or 0.0)
                features_dict[ntype][idx] = np.array([
                    state,
                    get_scaled("interface", "tx_drops", tx_drops),
                    get_scaled("interface", "rx_drops", rx_drops),
                    get_scaled("interface", "mtu_norm", mtu),
                ])

        for ntype, feat_array in features_dict.items():
            feat_array = np.nan_to_num(feat_array, nan=0.0)
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            
        # Edge Processing
        edge_indices = {}
        
        for edge in data["edges"]:
            src, tgt, rel = edge["source"], edge["target"], edge["relation"]
            
            if src not in id_to_type or tgt not in id_to_type: continue
            
            src_type, tgt_type = id_to_type[src], id_to_type[tgt]
            edge_type = (src_type, rel, tgt_type)
            
            if edge_type not in edge_indices:
                edge_indices[edge_type] = [[], []]
                
            src_idx = self.global_id_map[src_type].get(src)
            tgt_idx = self.global_id_map[tgt_type].get(tgt)
            
            if src_idx is not None and tgt_idx is not None:
                edge_indices[edge_type][0].append(src_idx)
                edge_indices[edge_type][1].append(tgt_idx)
            
        for etype, indices in edge_indices.items():
            if indices[0]:  # Only add if edges exist
                hetero_data[etype].edge_index = torch.tensor(indices, dtype=torch.long)
            
        return hetero_data, input_dims
