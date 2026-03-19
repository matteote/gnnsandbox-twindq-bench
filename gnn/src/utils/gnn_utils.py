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
        self.tokenizer = None
        self.text_model = None
        self.text_embed_dim = 128
        
        # Updated to include semantic sub-nodes for HetGNN and BGP_Session
        self.global_id_map = {
            "PE Router": {}, "P Router": {}, "CE Router": {},
            "Router_Config": {}, "Protocol_State": {}, "Interface_Metrics": {},
            "Interface": {}, "BGP_Session": {}
        }
        
        # Keep track of previous snapshot metrics for derivative (velocity/acceleration) calculation
        self.previous_metrics = {}
        
        logger.debug(f"Global ID map initialized with node types: {list(self.global_id_map.keys())}")
        
    def init_config_encoder(self):
        # Using structured encoding instead of NetBERT
        # Dimensions: 128 hash buckets + 4 explicit RT features (import AS/val, export AS/val)
        self.text_embed_dim = 132
        logger.info(f"Using Structured Config Encoder with dimension: {self.text_embed_dim}")
        
    def _parse_vyos_commands(self, config_text):
        """
        Extracts key configuration items from VyOS text commands.
        Returns a list of feature strings like 'rt_import:65035:1030', 'neighbor:10.0.0.1'.
        """
        features = []
        if not config_text:
            return features
            
        lines = config_text.split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'): continue
            parts = line.split()
            
            # Parse specific features as outlined by research doc
            if "interfaces ethernet" in line:
                if "mtu" in line:
                    try:
                        val = int(parts[-1].strip("'\""))
                        normalized_mtu = min(val / 9000.0, 1.0)
                        features.append(f"if_mtu:{normalized_mtu:.2f}")
                    except ValueError: pass
                elif "address" in line:
                    features.append("if_has_address:1")
            
            elif "protocols bgp" in line:
                if "local-as" in line:
                    try:
                        val = int(parts[-1].strip("'\""))
                        features.append(f"bgp_local_as:{(val/65535.0):.4f}")
                    except ValueError: pass
                elif "neighbor" in line and "remote-as" in line:
                    try:
                        val = int(parts[-1].strip("'\""))
                        features.append(f"bgp_remote_as:{(val/65535.0):.4f}")
                    except ValueError: pass
            
            elif "protocols ospf" in line:
                if "area" in line and "network" in line:
                    try:
                        idx = parts.index("area")
                        if idx + 1 < len(parts): 
                            features.append(f"ospf_area_id:{parts[idx + 1]}")
                    except ValueError: pass
                elif "parameters router-id" in line:
                    try:
                        router_id = parts[-1].strip("'\"")
                        features.append(f"ospf_router_id:{router_id}")
                    except IndexError: pass
            
            elif "vrf name" in line and "protocols bgp" in line:
                if "rd" in line:
                    try:
                        rd = parts[-1].strip("'\"")
                        features.append(f"vrf_rd:{rd}")
                    except IndexError: pass
                elif "route-target" in line:
                    try:
                        target = parts[-1].strip("'\"")
                        if "import" in line: features.append(f"vrf_rt_import:{target}")
                        elif "export" in line: features.append(f"vrf_rt_export:{target}")
                    except IndexError: pass
                    
            elif "policy route-map" in line:
                try:
                    idx = parts.index("route-map")
                    if idx + 1 < len(parts): 
                        features.append(f"has_route_map:{parts[idx + 1]}")
                except ValueError: pass

        return features

    def get_config_embedding(self, text):
        """
        Generates a fixed-size embedding using hashing of configuration features.
        Plus explicit RT features (last 4 dimensions).
        Robust to new/unseen values.
        """
        if text is None or text == "":
            return np.zeros(self.text_embed_dim)
        
        content_to_embed = text
        data_dict = None
        
        if isinstance(text, (dict, list)):
            data_dict = text
        elif hasattr(text, '__class__') and 'JsonObject' in text.__class__.__name__:
             try: data_dict = dict(text)
             except: pass
        elif isinstance(text, str):
            try:
                if text.strip().startswith('{'): data_dict = json.loads(text)
            except: pass
        
        if isinstance(data_dict, dict):
            if 'status' in data_dict and isinstance(data_dict['status'], dict) and 'applied_config' in data_dict['status']:
                content_to_embed = data_dict['status']['applied_config']
            elif 'spec' in data_dict:
                content_to_embed = json.dumps(data_dict['spec'])
            else:
                content_to_embed = json.dumps(data_dict)
        
        if not isinstance(content_to_embed, str):
            content_to_embed = str(content_to_embed)

        # DEBUG: Log what we're parsing for the first PE router we see
        if "pe1" in str(text).lower() or "pe2" in str(text).lower() or "pe3" in str(text).lower():
            logger.debug(f"DEBUG config_to_parse (first 500 chars): {content_to_embed[:500]}")
            if data_dict:
                logger.debug(f"DEBUG parsed as dict, keys: {list(data_dict.keys())[:10]}")

        features = self._parse_vyos_commands(content_to_embed)
        
        # Extract RT values for explicit features (last 4 dimensions)
        rt_import_as, rt_import_val = 0.0, 0.0
        rt_export_as, rt_export_val = 0.0, 0.0
        
        for feature in features:
            if feature.startswith("vrf_rt_import:"):
                rt_str = feature.split(":", 1)[1]  # e.g., "65035:1030"
                try:
                    as_num, val_num = rt_str.split(":")
                    rt_import_as = float(as_num) / 65535.0  # Normalize AS number
                    rt_import_val = float(val_num) / 10000.0  # Normalize RT value
                except:
                    pass
            elif feature.startswith("vrf_rt_export:"):
                rt_str = feature.split(":", 1)[1]
                try:
                    as_num, val_num = rt_str.split(":")
                    rt_export_as = float(as_num) / 65535.0
                    rt_export_val = float(val_num) / 10000.0
                except:
                    pass
        
        # Hash embedding (first 128 dimensions)
        embedding = np.zeros(self.text_embed_dim)
        
        import hashlib
        if not features:
            features = content_to_embed.split()
        
        for feature in features:
            # Hash into first 128 buckets
            hash_val = int(hashlib.md5(feature.encode('utf-8')).hexdigest(), 16)
            idx = hash_val % 128  # Only use first 128 dimensions for hashing
            embedding[idx] = 1.0
        
        # Explicit RT features (last 4 dimensions: [128, 129, 130, 131])
        embedding[128] = rt_import_as
        embedding[129] = rt_import_val
        embedding[130] = rt_export_as
        embedding[131] = rt_export_val
            
        return embedding

    def fit_scalers(self, snapshot_objects):
        logger.info(f"Fitting scalers on {len(snapshot_objects)} snapshot objects")
        
        all_metrics = {
            "Interface": {"rx_bytes": [], "tx_bytes": [], "rx_drops": [], "tx_drops": [], "rx_errors": [], "tx_errors": []},
            "Interface_Metrics": {"rx_bytes_velocity": [], "tx_bytes_velocity": [], "rx_drops_velocity": [], "tx_drops_velocity": [], "rx_errors_velocity": [], "tx_errors_velocity": []},
            "Protocol_State": {"ospf_neighbors": [], "bgp_peers": [], "mpls_routes": []}
        }
        
        self.previous_metrics = {}
        
        for data in snapshot_objects:
            # First pass: map nodes for relation building
            node_map = {node["id"]: node for node in data["nodes"]}
            edge_list = data.get("edges", [])
            
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                
                if ntype == "Interface":
                    rx_bytes = np.log1p(float(node.get("rx_bytes") or 0.0))
                    tx_bytes = np.log1p(float(node.get("tx_bytes") or 0.0))
                    rx_drops = np.log1p(float(node.get("rx_drops") or 0.0))
                    tx_drops = np.log1p(float(node.get("tx_drops") or 0.0))
                    rx_errors = np.log1p(float(node.get("rx_errors") or 0.0))
                    tx_errors = np.log1p(float(node.get("tx_errors") or 0.0))
                    
                    all_metrics["Interface"]["rx_bytes"].append(rx_bytes)
                    all_metrics["Interface"]["tx_bytes"].append(tx_bytes)
                    all_metrics["Interface"]["rx_drops"].append(rx_drops)
                    all_metrics["Interface"]["tx_drops"].append(tx_drops)
                    all_metrics["Interface"]["rx_errors"].append(rx_errors)
                    all_metrics["Interface"]["tx_errors"].append(tx_errors)
                    
                    # Calculate derivatives if we have previous state
                    prev = self.previous_metrics.get(nid)
                    if prev:
                        rx_b_v = rx_bytes - prev["rx_bytes"]
                        tx_b_v = tx_bytes - prev["tx_bytes"]
                        rx_d_v = rx_drops - prev["rx_drops"]
                        tx_d_v = tx_drops - prev["tx_drops"]
                        rx_e_v = rx_errors - prev["rx_errors"]
                        tx_e_v = tx_errors - prev["tx_errors"]
                    else:
                        rx_b_v, tx_b_v = 0.0, 0.0
                        rx_d_v, tx_d_v = 0.0, 0.0
                        rx_e_v, tx_e_v = 0.0, 0.0
                        
                    self.previous_metrics[nid] = {
                        "rx_bytes": rx_bytes, "tx_bytes": tx_bytes,
                        "rx_drops": rx_drops, "tx_drops": tx_drops,
                        "rx_errors": rx_errors, "tx_errors": tx_errors
                    }
                    
                    all_metrics["Interface_Metrics"]["rx_bytes_velocity"].append(rx_b_v)
                    all_metrics["Interface_Metrics"]["tx_bytes_velocity"].append(tx_b_v)
                    all_metrics["Interface_Metrics"]["rx_drops_velocity"].append(rx_d_v)
                    all_metrics["Interface_Metrics"]["tx_drops_velocity"].append(tx_d_v)
                    all_metrics["Interface_Metrics"]["rx_errors_velocity"].append(rx_e_v)
                    all_metrics["Interface_Metrics"]["tx_errors_velocity"].append(tx_e_v)
                    
                elif "Router" in ntype and ntype in ["PE Router", "P Router", "CE Router"]:
                    # Extract protocol state features from VyOS config/status
                    config_str = str(node.get("config", ""))
                    ospf_count = config_str.count("protocols ospf")
                    bgp_count = config_str.count("protocols bgp neighbor")
                    mpls_count = config_str.count("protocols mpls")
                    
                    all_metrics["Protocol_State"]["ospf_neighbors"].append(float(ospf_count))
                    all_metrics["Protocol_State"]["bgp_peers"].append(float(bgp_count))
                    all_metrics["Protocol_State"]["mpls_routes"].append(float(mpls_count))

        for map_type, metrics in all_metrics.items():
            if map_type not in self.scalers:
                self.scalers[map_type] = {}
            for metric, values in metrics.items():
                if values:
                    scaler = StandardScaler()
                    scaler.fit(np.array(values).reshape(-1, 1))
                    self.scalers[map_type][metric] = scaler
                    logger.debug(f"Fitted scaler for {map_type}.{metric} with {len(values)} values")
        
        logger.info("Building global ID map including sub-nodes")
        # Reset ID map
        self.global_id_map = {k: {} for k in self.global_id_map.keys()}
        
        for data in snapshot_objects:
            for node in data["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                if ntype in ["PE Router", "P Router", "CE Router", "Interface", "BGP_Session"]:
                    if nid not in self.global_id_map[ntype]:
                        self.global_id_map[ntype][nid] = len(self.global_id_map[ntype])

                    # Also create sub-node IDs for routers (for HetGNN)
                    if "Router" in ntype:
                        conf_id = f"{nid}_config"
                        prot_id = f"{nid}_protocol"
                        if conf_id not in self.global_id_map["Router_Config"]:
                            self.global_id_map["Router_Config"][conf_id] = len(self.global_id_map["Router_Config"])
                        if prot_id not in self.global_id_map["Protocol_State"]:
                            self.global_id_map["Protocol_State"][prot_id] = len(self.global_id_map["Protocol_State"])
                    
                    # Create sub-node IDs for interfaces (for HetGNN)
                    if ntype == "Interface":
                        met_id = f"{nid}_metrics"
                        if met_id not in self.global_id_map["Interface_Metrics"]:
                            self.global_id_map["Interface_Metrics"][met_id] = len(self.global_id_map["Interface_Metrics"])
        
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
        
        # Dimensions:
        # Core: Router(1), Interface(4)
        # Sub-nodes: Router_Config(128), Protocol_State(3), Interface_Metrics(6 - raw+velocity)
        for ntype, id_map in self.global_id_map.items():
            count = len(id_map)
            
            dim = 0
            if ntype in ["PE Router", "P Router", "CE Router"]: dim = 1  # Just state
            elif ntype == "Router_Config": dim = self.text_embed_dim
            elif ntype == "Protocol_State": dim = 3
            elif ntype == "Interface": dim = 7  # Baseline + 6 metrics
            elif ntype == "Interface_Metrics": dim = 12  # 6 metrics + 6 velocities
            elif ntype == "BGP_Session": dim = 1          # Established=1 / Idle=0

            if count > 0:
                features_dict[ntype] = np.zeros((count, dim), dtype=np.float32)
                input_dims[ntype] = dim

        def get_scaled(map_type, metric, val):
            if map_type in self.scalers and metric in self.scalers[map_type]:
                return self.scalers[map_type][metric].transform([[val]])[0][0]
            return val

        # Map to find node info for edges later
        id_to_type = {}
        id_to_metrics = {}

        for node in data["nodes"]:
            ntype = node["type"]
            nid = node["id"]
            id_to_type[nid] = ntype
            
            if ntype not in self.global_id_map or nid not in self.global_id_map[ntype]:
                continue
                
            idx = self.global_id_map[ntype][nid]
            state = node.get("state", 0.0)
            
            if "Router" in ntype:
                # 1. Base Node
                features_dict[ntype][idx] = np.array([state])
                
                # 2. Config Sub-node
                conf_id = f"{nid}_config"
                conf_idx = self.global_id_map["Router_Config"][conf_id]
                config_text = node.get("config", "")
                features_dict["Router_Config"][conf_idx] = self.get_config_embedding(config_text)
                
                # 3. Protocol Sub-node
                prot_id = f"{nid}_protocol"
                prot_idx = self.global_id_map["Protocol_State"][prot_id]
                o_c, b_c, m_c = str(config_text).count("protocols ospf"), str(config_text).count("protocols bgp neighbor"), str(config_text).count("protocols mpls")
                features_dict["Protocol_State"][prot_idx] = np.array([
                    get_scaled("Protocol_State", "ospf_neighbors", float(o_c)),
                    get_scaled("Protocol_State", "bgp_peers", float(b_c)),
                    get_scaled("Protocol_State", "mpls_routes", float(m_c))
                ])
                
            elif ntype == "Interface":
                rx_bytes = np.log1p(float(node.get("rx_bytes") or 0.0))
                tx_bytes = np.log1p(float(node.get("tx_bytes") or 0.0))
                rx_drops = np.log1p(float(node.get("rx_drops") or 0.0))
                tx_drops = np.log1p(float(node.get("tx_drops") or 0.0))
                rx_errors = np.log1p(float(node.get("rx_errors") or 0.0))
                tx_errors = np.log1p(float(node.get("tx_errors") or 0.0))
                
                # Base node
                features_dict[ntype][idx] = np.array([
                    state, 
                    get_scaled("Interface", "rx_bytes", rx_bytes),
                    get_scaled("Interface", "tx_bytes", tx_bytes),
                    get_scaled("Interface", "rx_drops", rx_drops),
                    get_scaled("Interface", "tx_drops", tx_drops),
                    get_scaled("Interface", "rx_errors", rx_errors),
                    get_scaled("Interface", "tx_errors", tx_errors)
                ])
                
                id_to_metrics[nid] = {
                    "rx_bytes": rx_bytes, "tx_bytes": tx_bytes,
                    "rx_drops": rx_drops, "tx_drops": tx_drops,
                    "rx_errors": rx_errors, "tx_errors": tx_errors
                }
                
                # Metrics Sub-node
                met_id = f"{nid}_metrics"
                met_idx = self.global_id_map["Interface_Metrics"][met_id]
                
                prev = self.previous_metrics.get(nid, {
                    "rx_bytes": 0.0, "tx_bytes": 0.0,
                    "rx_drops": 0.0, "tx_drops": 0.0,
                    "rx_errors": 0.0, "tx_errors": 0.0
                })
                
                rx_b_v = rx_bytes - prev["rx_bytes"]
                tx_b_v = tx_bytes - prev["tx_bytes"]
                rx_d_v = rx_drops - prev["rx_drops"]
                tx_d_v = tx_drops - prev["tx_drops"]
                rx_e_v = rx_errors - prev["rx_errors"]
                tx_e_v = tx_errors - prev["tx_errors"]
                
                self.previous_metrics[nid] = {
                    "rx_bytes": rx_bytes, "tx_bytes": tx_bytes,
                    "rx_drops": rx_drops, "tx_drops": tx_drops,
                    "rx_errors": rx_errors, "tx_errors": tx_errors
                } # Update state for next snapshot
                
                features_dict["Interface_Metrics"][met_idx] = np.array([
                    get_scaled("Interface", "rx_bytes", rx_bytes),
                    get_scaled("Interface", "tx_bytes", tx_bytes),
                    get_scaled("Interface", "rx_drops", rx_drops),
                    get_scaled("Interface", "tx_drops", tx_drops),
                    get_scaled("Interface", "rx_errors", rx_errors),
                    get_scaled("Interface", "tx_errors", tx_errors),
                    get_scaled("Interface_Metrics", "rx_bytes_velocity", rx_b_v),
                    get_scaled("Interface_Metrics", "tx_bytes_velocity", tx_b_v),
                    get_scaled("Interface_Metrics", "rx_drops_velocity", rx_d_v),
                    get_scaled("Interface_Metrics", "tx_drops_velocity", tx_d_v),
                    get_scaled("Interface_Metrics", "rx_errors_velocity", rx_e_v),
                    get_scaled("Interface_Metrics", "tx_errors_velocity", tx_e_v)
                ])

            elif ntype == "BGP_Session":
                # Single feature: Established=1.0, Idle/Down=0.0
                # The reconstruction error on this feature is the primary BGP anomaly signal.
                features_dict[ntype][idx] = np.array([state])

        for ntype, feat_array in features_dict.items():
            feat_array = np.nan_to_num(feat_array, nan=0.0)
            hetero_data[ntype].x = torch.from_numpy(feat_array).float()
            
        # Edge Processing
        edge_indices = {}
        edge_attr_dict = {} # For D-GAT asymmetry
        
        # We process the base structural edges, and infer the sub-node edges
        for edge in data["edges"]:
            src, tgt, rel = edge["source"], edge["target"], edge["relation"]
            
            if src not in id_to_type or tgt not in id_to_type: continue
            
            src_type, tgt_type = id_to_type[src], id_to_type[tgt]
            edge_type = (src_type, rel, tgt_type)
            
            if edge_type not in edge_indices:
                edge_indices[edge_type] = [[], []]
                edge_attr_dict[edge_type] = []
                
            src_idx = self.global_id_map[src_type].get(src)
            tgt_idx = self.global_id_map[tgt_type].get(tgt)
            
            if src_idx is not None and tgt_idx is not None:
                edge_indices[edge_type][0].append(src_idx)
                edge_indices[edge_type][1].append(tgt_idx)
                
                # Edge Attributes for D-GAT (Asymmetry)
                if src_type == "Interface" and tgt_type == "Interface":
                    src_m = id_to_metrics.get(src, {"tx_bytes":0,"rx_bytes":0,"tx_drops":0,"rx_drops":0})
                    tgt_m = id_to_metrics.get(tgt, {"tx_bytes":0,"rx_bytes":0,"tx_drops":0,"rx_drops":0})
                    
                    # Traffic asymmetry formula: |tx_rate_A - rx_rate_B| / max(tx_rate_A, rx_rate_B)
                    tx_a = src_m["tx_bytes"]
                    rx_b = tgt_m["rx_bytes"]
                    max_traffic = max(tx_a, rx_b)
                    traffic_asym = abs(tx_a - rx_b) / max_traffic if max_traffic > 0 else 0.0
                    
                    # Drop asymmetry formula: |drops_A->B - drops_B->A| / max(drops_A->B, drops_B->A)
                    # We approximate directed drops as A's tx drops vs B's tx drops
                    # (ideally we'd have explicit directed metrics, but interface tx drop is closest)
                    drops_ab = src_m["tx_drops"]
                    drops_ba = tgt_m["tx_drops"]
                    max_drops = max(drops_ab, drops_ba)
                    drop_asym = abs(drops_ab - drops_ba) / max_drops if max_drops > 0 else 0.0
                    
                    edge_attr_dict[edge_type].append([traffic_asym, drop_asym])
                else:
                    edge_attr_dict[edge_type].append([0.0, 0.0]) # Default weights
                
        # Inject structural edges for the sub-nodes (HetGNN requirement)
        # Router -> Config/Protocol
        for r_type in ["PE Router", "P Router", "CE Router"]:
            conf_rel = (r_type, "Has_Config", "Router_Config")
            prot_rel = (r_type, "Has_Protocol", "Protocol_State")
            edge_indices[conf_rel], edge_indices[prot_rel] = [[], []], [[], []]
            
            for nid, idx in self.global_id_map[r_type].items():
                conf_id, prot_id = f"{nid}_config", f"{nid}_protocol"
                if conf_id in self.global_id_map["Router_Config"]:
                    edge_indices[conf_rel][0].append(idx)
                    edge_indices[conf_rel][1].append(self.global_id_map["Router_Config"][conf_id])
                if prot_id in self.global_id_map["Protocol_State"]:
                    edge_indices[prot_rel][0].append(idx)
                    edge_indices[prot_rel][1].append(self.global_id_map["Protocol_State"][prot_id])
                    
        # Interface -> Metrics
        met_rel = ("Interface", "Has_Metrics", "Interface_Metrics")
        edge_indices[met_rel] = [[], []]
        for nid, idx in self.global_id_map["Interface"].items():
            met_id = f"{nid}_metrics"
            if met_id in self.global_id_map["Interface_Metrics"]:
                edge_indices[met_rel][0].append(idx)
                edge_indices[met_rel][1].append(self.global_id_map["Interface_Metrics"][met_id])
            
        for etype, indices in edge_indices.items():
            if indices[0]:  # Only add if edges exist
                hetero_data[etype].edge_index = torch.tensor(indices, dtype=torch.long)
                if etype in edge_attr_dict and edge_attr_dict[etype]:
                    att = np.nan_to_num(np.array(edge_attr_dict[etype]), nan=0.0)
                    hetero_data[etype].edge_attr = torch.from_numpy(att).float()
            
        return hetero_data, input_dims
