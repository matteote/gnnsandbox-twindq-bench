"""gnn_utils.py — HetGNN graph builder and scaler utilities.

Four-layer node schema (matches rca.md feature model)
──────────────────────────────────────────────────────────────────
router       12 features:  state, cpu, mem, ospf_num_routes, pfx_count_norm,
                           bgp_update_rate, vrf_count, fib_size_norm,
                           role_P, role_PE, role_RR, role_CE
             Scaler applied to first 8 (continuous); role one-hots left unchanged.

interface    11 features:  state, rx_drops, tx_drops, mtu_norm, rx_errs_rate,
                           rx_bytes_rate, tx_bytes_rate, tx_queue_len_norm,
                           rx_err_gradient, tx_util, rx_util
             Scaler applied to all 11.

bgp_session   4 features:  bgp_state, pfx_count_norm, prefix_count_delta,
                           session_uptime_norm
             bgp_state and session_uptime_norm are derived from
             frr_bgp_peer_uptime_seconds (20 s cadence) rather than the
             SCD-written BGPSession.status / valid_start_ts (60 s cadence).
             Scaler applied to all 4.

vrf           5 features:  vrf_route_count, vrf_route_count_delta,
                           rt_import_hash, rt_export_hash,
                           vrf_active_sessions
             Scaler applied to all 5 (all continuous).
             Note: lab-specific vpn_blue/vpn_red/is_hub one-hots were removed.
             RT policy identity is captured by rt_import_hash/rt_export_hash,
             which generalise to any number of VPNs without per-VPN enumeration.

flow          6 features:  throughput_bps, throughput_delta,
                           latency_ms_norm, jitter_norm,
                           packet_loss_pct, active_sessions
             All 6 are continuous — scaler applied to all.
             Config-dependent features removed (throughput_norm, expected_rate_deviation,
             active_sessions_norm, protocol_tcp, is_constant) so the model learns
             "normal" purely from observed traffic, not from configured expectations.

Edge types (14)
───────────────
  Existing:
    (router,      has_interface,    interface)
    (interface,   connected_to,     interface)
    (router,      ospf_peer,        router)
    (router,      bgp_peer,         router)
    (bgp_session, session_on,       router)
  New VRF edges:
    (router,      has_vrf,          vrf)
    (vrf,         has_vrf,          router)           ← reverse direction
    (vrf,         contains_session, bgp_session)
    (bgp_session, contains_session, vrf)              ← reverse direction
    (vrf,         same_vpn_as,      vrf)
  New flow edges:
    (flow,        ingresses_at,     interface)
    (flow,        source_pe,        router)
    (flow,        dest_pe,          router)
    (flow,        belongs_to_vrf,   vrf)

Anomaly MSE thresholds
──────────────────────
  router:      0.15
  interface:   0.20
  bgp_session: 0.10
  vrf:         0.10
  flow:        0.15
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler
from torch_geometric.data import HeteroData

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

NODE_TYPES = ["router", "interface", "bgp_session", "vrf", "flow"]

EDGE_TYPES: List[Tuple[str, str, str]] = [
    # ── existing ──────────────────────────────────────────────────────────────
    ("router",      "has_interface",    "interface"),
    ("interface",   "connected_to",     "interface"),
    ("router",      "ospf_peer",        "router"),
    ("router",      "bgp_peer",         "router"),
    ("bgp_session", "session_on",       "router"),
    # ── VRF edges (bidirectional pairs registered as separate typed edges) ────
    ("router",      "has_vrf",          "vrf"),
    ("vrf",         "has_vrf",          "router"),
    ("vrf",         "contains_session", "bgp_session"),
    ("bgp_session", "contains_session", "vrf"),
    ("vrf",         "same_vpn_as",      "vrf"),
    # ── flow edges ────────────────────────────────────────────────────────────
    ("flow",        "ingresses_at",     "interface"),
    ("flow",        "source_pe",        "router"),
    ("flow",        "dest_pe",          "router"),
    ("flow",        "belongs_to_vrf",   "vrf"),
]

# Feature keys per node type (order defines column indices in the feature tensor)
ROUTER_FEATURES = [
    "state", "cpu", "mem", "ospf_num_routes", "pfx_count_norm",
    "bgp_update_rate", "vrf_count", "fib_size_norm",   # ← new in four-layer model
    "role_P", "role_PE", "role_RR", "role_CE",          # one-hots — not scaled
]

INTERFACE_FEATURES = [
    "state", "rx_drops", "tx_drops", "mtu_norm", "rx_errs_rate",
    "rx_bytes_rate", "tx_bytes_rate",
    "tx_queue_len_norm",      # ← new: txqueuelen / 1000 (Fault 8 signal)
    "rx_err_gradient", "tx_util", "rx_util",
]

BGP_SESSION_FEATURES = [
    "bgp_state",              # 1.0 if frr_bgp_peer_uptime_seconds > 0, else 0.0
    "pfx_count_norm",         # log1p(pfx_count_raw) — replaces raw value in model
    "prefix_count_delta",
    "session_uptime_norm",    # frr_bgp_peer_uptime_seconds / 86400, capped at 1.0
]

VRF_FEATURES = [              # ⭐ NEW node type — all continuous, all scaled
    "vrf_route_count",        # log1p  — IPv4 routes in this VRF's FIB
    "vrf_route_count_delta",  # delta  — rate of route count change
    "rt_import_hash",         # [0,1]  — MD5 fingerprint of import RT set
    "rt_export_hash",         # [0,1]  — MD5 fingerprint of export RT set
    "vrf_active_sessions",    # log1p  — active BGP sessions in this VRF
]

FLOW_FEATURES = [             # all continuous — scaler applied to full array
    "throughput_bps",           # log1p(bps) — observed throughput
    "throughput_delta",         # delta      — change in log1p(bps) between snapshots
    "latency_ms_norm",          # ratio      — latency_ms / 100.0 (fixed reference)
    "jitter_norm",              # ratio      — jitter_ms / 10.0 (fixed reference)
    "packet_loss_pct",          # [0,1]      — raw packet loss fraction
    "active_sessions",          # log1p      — observed concurrent sessions
]

FEATURE_DIMS = {
    "router":      len(ROUTER_FEATURES),       # 12
    "interface":   len(INTERFACE_FEATURES),    # 11
    "bgp_session": len(BGP_SESSION_FEATURES),  #  4
    "vrf":         len(VRF_FEATURES),          #  5
    "flow":        len(FLOW_FEATURES),         #  6
}

# Per-type reconstruction MSE thresholds for anomaly detection
ANOMALY_THRESHOLDS = {
    "router":      0.15,
    "interface":   0.20,
    "bgp_session": 0.10,
    "vrf":         0.10,   # RT-hash deviations are discrete jumps; keep tight
    "flow":        0.15,
}

# Continuous column slices — used when applying/fitting scalers.
# One-hot columns are excluded so they remain in {0, 1}.
_ROUTER_CONT_COLS   = slice(0, 8)   # state … fib_size_norm  (4 role one-hots follow)
# interface, bgp_session, vrf, flow: all features continuous — scaler on full array

# Role one-hot order (router only)
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
            types produce empty index tensors.
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
                        float(n.get("bgp_update_rate", 0.0)),
                        float(n.get("vrf_count",       0.0)),
                        float(n.get("fib_size_norm",   0.0)),
                    ] + role_oh
                    rows.append(row)

            elif ntype == "interface":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("state",             0.0)),
                        float(n.get("rx_drops",          0.0)),
                        float(n.get("tx_drops",          0.0)),
                        float(n.get("mtu_norm",          0.0)),
                        float(n.get("rx_errs_rate",      0.0)),
                        float(n.get("rx_bytes_rate",     0.0)),
                        float(n.get("tx_bytes_rate",     0.0)),
                        float(n.get("tx_queue_len_norm", 0.0)),
                        float(n.get("rx_err_gradient",   0.0)),
                        float(n.get("tx_util",           0.0)),
                        float(n.get("rx_util",           0.0)),
                    ]
                    rows.append(row)

            elif ntype == "bgp_session":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("bgp_state",           0.0)),
                        float(n.get("pfx_count_norm",       0.0)),
                        float(n.get("prefix_count_delta",   0.0)),
                        float(n.get("session_uptime_norm",  0.0)),
                    ]
                    rows.append(row)

            elif ntype == "vrf":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("vrf_route_count",       0.0)),
                        float(n.get("vrf_route_count_delta",  0.0)),
                        float(n.get("rt_import_hash",         0.0)),
                        float(n.get("rt_export_hash",         0.0)),
                        float(n.get("vrf_active_sessions",    0.0)),
                    ]
                    rows.append(row)

            elif ntype == "flow":
                rows = []
                for n in node_list:
                    row = [
                        float(n.get("throughput_bps",    0.0)),
                        float(n.get("throughput_delta",   0.0)),
                        float(n.get("latency_ms_norm",    0.0)),
                        float(n.get("jitter_norm",        0.0)),
                        float(n.get("packet_loss_pct",    0.0)),
                        float(n.get("active_sessions",    0.0)),
                    ]
                    rows.append(row)

            else:
                rows = [[0.0] * FEATURE_DIMS[ntype]] * len(node_list)

            feat_arr = np.array(rows, dtype=np.float32)

            # Apply scaler — one-hot columns are never scaled
            scaler = self.scalers.get(ntype)
            if scaler is not None:
                if ntype == "router":
                    # Continuous: state…fib_size_norm (cols 0–7); role one-hots (cols 8–11) unchanged
                    feat_arr[:, _ROUTER_CONT_COLS] = scaler.transform(
                        feat_arr[:, _ROUTER_CONT_COLS]
                    ).astype(np.float32)
                else:
                    # interface, bgp_session, vrf, flow — all columns are continuous
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
        data.node_id_map = {
            ntype: [n["id"] for n in nodes_by_type[ntype]]
            for ntype in NODE_TYPES
        }

        return data


def fit_scalers(snapshots: List[Dict]) -> Dict[str, StandardScaler]:
    """Fit one StandardScaler per node type over all snapshots.

    One-hot columns are excluded from fitting so they stay in {0, 1}:
      - router:      only continuous cols 0–7  (8 features; excludes 4 role one-hots)
      - interface:   all 11 features (all continuous)
      - bgp_session: all 4 features (all continuous)
      - vrf:         all 5 features (all continuous)
      - flow:        all 6 features (all continuous — config one-hots removed)

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
                # Continuous features only (excludes role one-hots)
                row = [
                    float(node.get("state",           0.0)),
                    float(node.get("cpu",             0.0)),
                    float(node.get("mem",             0.0)),
                    float(node.get("ospf_num_routes", 0.0)),
                    float(node.get("pfx_count_norm",  0.0)),
                    float(node.get("bgp_update_rate", 0.0)),
                    float(node.get("vrf_count",       0.0)),
                    float(node.get("fib_size_norm",   0.0)),
                ]

            elif ntype == "interface":
                row = [
                    float(node.get("state",             0.0)),
                    float(node.get("rx_drops",          0.0)),
                    float(node.get("tx_drops",          0.0)),
                    float(node.get("mtu_norm",          0.0)),
                    float(node.get("rx_errs_rate",      0.0)),
                    float(node.get("rx_bytes_rate",     0.0)),
                    float(node.get("tx_bytes_rate",     0.0)),
                    float(node.get("tx_queue_len_norm", 0.0)),
                    float(node.get("rx_err_gradient",   0.0)),
                    float(node.get("tx_util",           0.0)),
                    float(node.get("rx_util",           0.0)),
                ]

            elif ntype == "bgp_session":
                # All 4 features are continuous
                row = [
                    float(node.get("bgp_state",           0.0)),
                    float(node.get("pfx_count_norm",       0.0)),
                    float(node.get("prefix_count_delta",   0.0)),
                    float(node.get("session_uptime_norm",  0.0)),
                ]

            elif ntype == "vrf":
                # All 5 VRF features are continuous — scaler applied to full array
                row = [
                    float(node.get("vrf_route_count",       0.0)),
                    float(node.get("vrf_route_count_delta",  0.0)),
                    float(node.get("rt_import_hash",         0.0)),
                    float(node.get("rt_export_hash",         0.0)),
                    float(node.get("vrf_active_sessions",    0.0)),
                ]

            elif ntype == "flow":
                # All 6 flow features are continuous
                row = [
                    float(node.get("throughput_bps",   0.0)),
                    float(node.get("throughput_delta",  0.0)),
                    float(node.get("latency_ms_norm",   0.0)),
                    float(node.get("jitter_norm",       0.0)),
                    float(node.get("packet_loss_pct",   0.0)),
                    float(node.get("active_sessions",   0.0)),
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
        logger.info(
            f"Fitted scaler for '{ntype}' on {len(rows)} node observations "
            f"({arr.shape[1]} continuous features)"
        )

    return scalers
