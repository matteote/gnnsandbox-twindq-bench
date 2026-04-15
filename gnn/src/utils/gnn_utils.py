"""gnn_utils.py — HetGNN graph builder and scaler utilities.

Node types
──────────
  router      9 features:  state, cpu, mem, ospf_num_routes, pfx_count_norm,
                            role_P, role_PE, role_RR, role_CE
  interface  10 features:  state, rx_drops, tx_drops, mtu_norm, rx_errs_rate,
                            rx_bytes_rate, tx_bytes_rate, rx_err_gradient,
                            tx_util, rx_util
  bgp_session 4 features:  bgp_state, pfx_count_raw, prefix_count_delta,
                            session_uptime_norm

Edge types (5)
──────────────
  (router,      has_interface, interface)
  (interface,   interface_of,  router)
  (interface,   connected_to,  interface)
  (router,      ospf_peer,     router)
  (router,      bgp_peer,      router)
  (bgp_session, session_on,    router)
  (router,      hosts_session, bgp_session)

Anomaly MSE thresholds
──────────────────────
  router:      0.15
  interface:   0.20
  bgp_session: 0.10
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler
from torch_geometric.data import HeteroData

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

NODE_TYPES = ["router", "interface", "bgp_session"]

EDGE_TYPES: List[Tuple[str, str, str]] = [
    ("router",      "has_interface", "interface"),
    ("interface",   "interface_of",  "router"),
    ("interface",   "connected_to",  "interface"),
    ("router",      "ospf_peer",     "router"),
    ("router",      "bgp_peer",      "router"),
    ("bgp_session", "session_on",    "router"),
    ("router",      "hosts_session", "bgp_session"),
]

# Feature keys per node type (order matters — defines column indices)
ROUTER_FEATURES    = ["state", "cpu", "mem", "ospf_num_routes", "pfx_count_norm",
                      "role_P", "role_PE", "role_RR", "role_CE"]
INTERFACE_FEATURES = ["state", "rx_drops", "tx_drops", "mtu_norm", "rx_errs_rate",
                      "rx_bytes_rate", "tx_bytes_rate", 
                      "rx_err_gradient",
                      "tx_util", "rx_util"]
BGP_SESSION_FEATURES = ["bgp_state", "pfx_count_raw", 
                        "prefix_count_delta",
                        "session_uptime_norm"
                        ]

FEATURE_DIMS = {
    "router":      len(ROUTER_FEATURES),
    "interface":   len(INTERFACE_FEATURES),
    "bgp_session": len(BGP_SESSION_FEATURES),
}

# Per-type reconstruction MSE thresholds for anomaly detection
ANOMALY_THRESHOLDS = {
    "router":      0.15,
    "interface":   0.20,
    "bgp_session": 0.10,
}

# Role one-hot order
ROLE_ORDER = ["P", "PE", "RR", "CE"]


def _role_onehot(role: str) -> List[float]:
    """Return a 4-element one-hot vector for a router role string."""
    role_u = (role or "").upper().strip()
    return [1.0 if role_u == r else 0.0 for r in ROLE_ORDER]


class GraphBuilder:
    """Converts a snapshot dict into a PyG HeteroData object.

    Usage
    ─────
    builder = GraphBuilder(scalers=scalers)
    hetero_data = builder.process_snapshot(snapshot_dict)
    """

    def __init__(self, scalers: Optional[Dict[str, StandardScaler]] = None):
        """
        Args:
            scalers: dict mapping node type → fitted StandardScaler.
                     If None, raw (unscaled) features are returned.
        """
        self.scalers = scalers or {}

    def process_snapshot(self, snapshot: Dict) -> HeteroData:
        """Convert a snapshot dict into a PyG HeteroData object.

        Args:
            snapshot: dict returned by SpannerDataset.fetch_snapshot() (after
                      compute_temporal_features() has been called).

        Returns:
            A HeteroData object with nodes and edges for all defined types.
            Nodes with no edges for a given relation still appear; missing edge
            types simply produce empty index tensors.
        """
        data = HeteroData()

        nodes_by_type: Dict[str, List[Dict]] = {t: [] for t in NODE_TYPES}
        global_id_map: Dict[str, Tuple[str, int]] = {}  # node_id → (type, local_idx)

        # ── 1. Assign nodes to types ─────────────────────────────────────────
        for node in snapshot.get("nodes", []):
            ntype = node.get("type")
            if ntype not in nodes_by_type:
                logger.debug(f"Skipping unknown node type: {ntype} (id={node.get('id')})")
                continue
            local_idx = len(nodes_by_type[ntype])
            nodes_by_type[ntype].append(node)
            global_id_map[node["id"]] = (ntype, local_idx)

        # ── 2. Build feature tensors ─────────────────────────────────────────
        for ntype in NODE_TYPES:
            node_list = nodes_by_type[ntype]
            if not node_list:
                data[ntype].x = torch.zeros((0, FEATURE_DIMS[ntype]), dtype=torch.float)
                continue

            if ntype == "router":
                rows = []
                for n in node_list:
                    role_oh = _role_onehot(n.get("role", ""))
                    row = [
                        float(n.get("state",           0.0)),
                        float(n.get("cpu",             0.0)),
                        float(n.get("mem",             0.0)),
                        float(n.get("ospf_num_routes", 0.0)),
                        float(n.get("pfx_count_norm",  0.0)),
                    ] + role_oh
                    rows.append(row)

            elif ntype == "interface":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("state",           0.0)),
                        float(n.get("rx_drops",        0.0)),
                        float(n.get("tx_drops",        0.0)),
                        float(n.get("mtu_norm",        0.0)),
                        float(n.get("rx_errs_rate",    0.0)),
                        float(n.get("rx_bytes_rate",   0.0)),
                        float(n.get("tx_bytes_rate",   0.0)),
                        float(n.get("rx_err_gradient", 0.0)),
                        float(n.get("tx_util",         0.0)),
                        float(n.get("rx_util",         0.0)),
                    ]
                    rows.append(row)

            elif ntype == "bgp_session":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("bgp_state",           0.0)),
                        float(n.get("pfx_count_raw",       0.0)),
                        float(n.get("prefix_count_delta",  0.0)),
                        float(n.get("session_uptime_norm", 0.0)),
                    ]
                    rows.append(row)
            else:
                rows = [[0.0] * FEATURE_DIMS[ntype]] * len(node_list)

            feat_arr = np.array(rows, dtype=np.float32)

            # Apply scaler if available
            scaler = self.scalers.get(ntype)
            if scaler is not None:
                # Only scale continuous columns; skip one-hot role columns for routers
                if ntype == "router":
                    cont_cols = slice(0, 5)          # state..pfx_count_norm
                    feat_arr[:, cont_cols] = scaler.transform(feat_arr[:, cont_cols]).astype(np.float32)
                else:
                    feat_arr = scaler.transform(feat_arr).astype(np.float32)

            data[ntype].x = torch.tensor(feat_arr, dtype=torch.float)

        # ── 3. Build edge index tensors ──────────────────────────────────────
        edge_buckets: Dict[Tuple[str, str, str], Tuple[List[int], List[int]]] = {
            et: ([], []) for et in EDGE_TYPES
        }

        for edge in snapshot.get("edges", []):
            src_id = edge.get("source")
            dst_id = edge.get("target")
            rel    = edge.get("relation")
            if src_id not in global_id_map or dst_id not in global_id_map:
                continue
            src_type, src_idx = global_id_map[src_id]
            dst_type, dst_idx = global_id_map[dst_id]

            # Find matching edge type
            key = (src_type, rel, dst_type)
            if key not in edge_buckets:
                logger.debug(f"Skipping unrecognised edge relation: {key}")
                continue
            edge_buckets[key][0].append(src_idx)
            edge_buckets[key][1].append(dst_idx)

        for (src_type, rel, dst_type), (srcs, dsts) in edge_buckets.items():
            if srcs:
                data[src_type, rel, dst_type].edge_index = torch.tensor(
                    [srcs, dsts], dtype=torch.long
                )
            else:
                data[src_type, rel, dst_type].edge_index = torch.zeros(
                    (2, 0), dtype=torch.long
                )

        # ── 4. Store metadata for downstream use ─────────────────────────────
        data.snapshot_timestamp = snapshot.get("timestamp", "")
        data.node_id_map = {ntype: [n["id"] for n in nodes_by_type[ntype]]
                            for ntype in NODE_TYPES}

        return data


def fit_scalers(snapshots: List[Dict]) -> Dict[str, StandardScaler]:
    """Fit one StandardScaler per node type over all snapshots.

    For routers, only the continuous features (indices 0–4) are scaled;
    the one-hot role columns are left unchanged.

    Args:
        snapshots: list of snapshot dicts (temporal features already computed).

    Returns:
        dict mapping node type → fitted StandardScaler.
    """
    accumulators: Dict[str, List[List[float]]] = {t: [] for t in NODE_TYPES}

    for snap in snapshots:
        for node in snap.get("nodes", []):
            ntype = node.get("type")
            if ntype not in accumulators:
                continue

            if ntype == "router":
                row = [
                    float(node.get("state",           0.0)),
                    float(node.get("cpu",             0.0)),
                    float(node.get("mem",             0.0)),
                    float(node.get("ospf_num_routes", 0.0)),
                    float(node.get("pfx_count_norm",  0.0)),
                ]
            elif ntype == "interface":
                row = [
                    float(node.get("state",           0.0)),
                    float(node.get("rx_drops",        0.0)),
                    float(node.get("tx_drops",        0.0)),
                    float(node.get("mtu_norm",        0.0)),
                    float(node.get("rx_errs_rate",    0.0)),
                    float(node.get("rx_bytes_rate",   0.0)),
                    float(node.get("tx_bytes_rate",   0.0)),
                    float(node.get("rx_err_gradient", 0.0)),
                    float(node.get("tx_util",         0.0)),
                    float(node.get("rx_util",         0.0)),
                ]
            elif ntype == "bgp_session":
                row = [
                    float(node.get("bgp_state",           0.0)),
                    float(node.get("pfx_count_raw",       0.0)),
                    float(node.get("prefix_count_delta",  0.0)),
                    float(node.get("session_uptime_norm", 0.0)),
                ]
            else:
                continue

            accumulators[ntype].append(row)

    scalers: Dict[str, StandardScaler] = {}
    for ntype, rows in accumulators.items():
        if not rows:
            logger.warning(f"No data to fit scaler for node type '{ntype}'")
            scalers[ntype] = StandardScaler()
            continue
        arr = np.array(rows, dtype=np.float32)
        sc = StandardScaler()
        sc.fit(arr)
        scalers[ntype] = sc
        logger.info(f"Fitted scaler for '{ntype}' on {len(rows)} node observations")

    return scalers
