
import datetime
import json
import logging
import os
from typing import List, Dict, Optional

import google.auth
from google.cloud import spanner

logger = logging.getLogger(__name__)


def _parse_speed_bps(speed_str: str) -> float:
    """Convert a speed string ('1G', '10G', '1000M', '100M') to bits-per-second.

    Falls back to 1 Gbps if the string cannot be parsed.
    """
    if not speed_str:
        return 1e9
    s = speed_str.strip().upper()
    try:
        if s.endswith("GBIT") or s.endswith("GBPS"):
            return float(s[:-4]) * 1e9
        if s.endswith("G"):
            return float(s[:-1]) * 1e9
        if s.endswith("MBIT") or s.endswith("MBPS"):
            return float(s[:-4]) * 1e6
        if s.endswith("M"):
            return float(s[:-1]) * 1e6
        if s.endswith("KBIT") or s.endswith("KBPS"):
            return float(s[:-4]) * 1e3
        if s.endswith("K"):
            return float(s[:-1]) * 1e3
        return float(s)
    except ValueError:
        return 1e9


class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""

    def __init__(
        self,
        instance_id: str,
        database_id: str,
        num_snapshots: int = 50,
        interval_minutes: int = 5,
        project_id: Optional[str] = None,
    ):
        logger.info(
            f"Initializing SpannerDataset: instance_id={instance_id}, "
            f"database_id={database_id}, num_snapshots={num_snapshots}, "
            f"interval_minutes={interval_minutes}, project_id={project_id}"
        )

        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
        if creds_path and os.path.exists(creds_path):
            credentials, detected_project = google.auth.load_credentials_from_file(
                creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            self.client = spanner.Client(
                project=effective_project,
                credentials=credentials,
                disable_builtin_metrics=True,
            )
        else:
            credentials, detected_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            self.client = spanner.Client(
                project=effective_project,
                credentials=credentials,
                disable_builtin_metrics=True,
            )

        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)
        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        logger.info("SpannerDataset initialized successfully")

    def _get_latest_timestamp(self) -> datetime.datetime:
        """Query Spanner for the most recent data timestamp across topology and metrics tables."""
        query = """
            SELECT MAX(ts) FROM (
                SELECT MAX(valid_start_ts) AS ts FROM PhysicalRouter
                UNION ALL
                SELECT MAX(valid_start_ts) AS ts FROM PhysicalInterface
                UNION ALL
                SELECT MAX(timestamp)      AS ts FROM NetworkMetrics
            )
        """
        try:
            with self.database.snapshot() as sn:
                results = sn.execute_sql(query)
                row = results.one_or_none()
                if row and row[0] is not None:
                    ts = row[0]
                    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    return ts
        except Exception as e:
            logger.warning(f"Could not determine latest timestamp, falling back: {e}")
        return datetime.datetime.utcnow()

    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps ending at the latest data timestamp."""
        end_time = self._get_latest_timestamp()
        timestamps = []
        for i in range(self.num_snapshots):
            delta = datetime.timedelta(
                minutes=self.interval_minutes * (self.num_snapshots - 1 - i)
            )
            timestamps.append(end_time - delta)
        logger.info(
            f"Generated {len(timestamps)} timestamps from "
            f"{timestamps[0].isoformat()} to {timestamps[-1].isoformat()}"
        )
        return timestamps

    def fetch_snapshot(self, timestamp: datetime.datetime) -> Dict:
        """Fetches the full heterogeneous network snapshot at ``timestamp``.

        Returns a dict with keys:
            timestamp   — ISO string
            nodes       — list of node dicts (router / interface / bgp_session)
            edges       — list of edge dicts (source, target, relation)

        Node dict keys by type
        ──────────────────────
        router:
            id, type, hostname, role, state, cpu, mem, ospf_num_routes,
            pfx_count_norm, rx_bytes_rate, tx_bytes_rate
        interface:
            id, type, name, device_id, speed_bps, state, rx_drops,
            tx_drops, mtu_norm, rx_errs_rate, rx_bytes_rate, tx_bytes_rate
            (rx_err_gradient, tx_util, rx_util added later by
             compute_temporal_features())
        bgp_session:
            id, type, router_id, vrf_id, peer_ip, bgp_state,
            valid_start_ts, pfx_count_raw
            (prefix_count_delta, session_uptime_norm added later)
        """
        logger.info(f"Fetching snapshot for timestamp: {timestamp.isoformat()}")
        snapshot_data = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}

        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {"ts": timestamp}
        param_types = {"ts": spanner.param_types.TIMESTAMP}

        with self.database.snapshot(multi_use=True) as sn:

            # ── 1. Routers ──────────────────────────────────────────────────
            router_ids: Dict[str, str] = {}  # router_id → hostname
            query_routers = f"""
                SELECT id, name, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            for row in sn.execute_sql(query_routers, params=params, param_types=param_types):
                r_id, r_name, r_role, r_status = row
                state_val = 1.0 if r_status and r_status.lower() == "running" else 0.0
                role_str = (r_role or "").upper()
                router_ids[r_id] = r_name
                snapshot_data["nodes"].append({
                    "id": r_id,
                    "type": "router",
                    "hostname": r_name,
                    "role": role_str,
                    "state": state_val,
                    "cpu": 0.0,
                    "mem": 0.0,
                    "ospf_num_routes": 0.0,
                    "pfx_count_norm": 0.0,
                })
            logger.info(f"Fetched {len(router_ids)} routers")

            # ── 2. Interfaces ────────────────────────────────────────────────
            iface_ids: Dict[str, str] = {}  # interface_id → router_id
            iface_speed: Dict[str, float] = {}  # interface_id → speed_bps
            query_ifaces = f"""
                SELECT id, router_id, name, speed, status
                FROM PhysicalInterface WHERE {valid_filter}
            """
            for row in sn.execute_sql(query_ifaces, params=params, param_types=param_types):
                i_id, i_router_id, i_name, i_speed, i_status = row
                state_val = 1.0 if i_status and i_status.lower() == "up" else 0.0
                speed_bps = _parse_speed_bps(i_speed)
                iface_ids[i_id] = i_router_id
                iface_speed[i_id] = speed_bps
                snapshot_data["nodes"].append({
                    "id": i_id,
                    "type": "interface",
                    "name": i_name,
                    "device_id": i_router_id,
                    "speed_bps": speed_bps,
                    "state": state_val,
                    "rx_drops": 0.0,
                    "tx_drops": 0.0,
                    "mtu_norm": 0.0,
                    "rx_errs_rate": 0.0,
                    "rx_bytes_rate": 0.0,
                    "tx_bytes_rate": 0.0,
                    # Temporal features — filled in by compute_temporal_features()
                    "rx_err_gradient": 0.0,
                    "tx_util": 0.0,
                    "rx_util": 0.0,
                })
            logger.info(f"Fetched {len(iface_ids)} interfaces")

            # ── 3. has_interface edges (router → interface) ──────────────────
            for iface_node in snapshot_data["nodes"]:
                if iface_node["type"] == "interface":
                    snapshot_data["edges"].append({
                        "source": iface_node["device_id"],
                        "target": iface_node["id"],
                        "relation": "has_interface",
                    })

            # ── 4. connected_to edges (interface ↔ interface) ────────────────
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM Interface_Link il1
                JOIN Interface_Link il2 ON il1.link_id = il2.link_id
                WHERE il1.interface_id < il2.interface_id
                  AND il1.valid_start_ts <= @ts
                  AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                  AND il2.valid_start_ts <= @ts
                  AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            for row in sn.execute_sql(query_links, params=params, param_types=param_types):
                a, b = row
                snapshot_data["edges"].append({"source": a, "target": b, "relation": "connected_to"})
                snapshot_data["edges"].append({"source": b, "target": a, "relation": "connected_to"})

            # ── 5. ospf_peer edges (router ↔ router via shared PhysicalLink) ─
            # Two routers sharing a p2p PhysicalLink are OSPF peers.
            query_ospf = f"""
                SELECT DISTINCT r1.id AS router_a, r2.id AS router_b
                FROM PhysicalLink pl
                JOIN Interface_Link il1 ON il1.link_id = pl.id
                JOIN Interface_Link il2 ON il2.link_id = pl.id
                    AND il1.interface_id < il2.interface_id
                JOIN PhysicalInterface pi1 ON pi1.id = il1.interface_id
                    AND pi1.valid_start_ts <= @ts
                    AND (pi1.valid_end_ts > @ts OR pi1.valid_end_ts IS NULL)
                JOIN PhysicalInterface pi2 ON pi2.id = il2.interface_id
                    AND pi2.valid_start_ts <= @ts
                    AND (pi2.valid_end_ts > @ts OR pi2.valid_end_ts IS NULL)
                JOIN PhysicalRouter r1 ON r1.id = pi1.router_id
                    AND r1.valid_start_ts <= @ts
                    AND (r1.valid_end_ts > @ts OR r1.valid_end_ts IS NULL)
                JOIN PhysicalRouter r2 ON r2.id = pi2.router_id
                    AND r2.valid_start_ts <= @ts
                    AND (r2.valid_end_ts > @ts OR r2.valid_end_ts IS NULL)
                WHERE pl.valid_start_ts <= @ts
                  AND (pl.valid_end_ts > @ts OR pl.valid_end_ts IS NULL)
                  AND il1.valid_start_ts <= @ts
                  AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                  AND il2.valid_start_ts <= @ts
                  AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
                  AND r1.id != r2.id
            """
            for row in sn.execute_sql(query_ospf, params=params, param_types=param_types):
                ra, rb = row
                if ra != rb:
                    snapshot_data["edges"].append({"source": ra, "target": rb, "relation": "ospf_peer"})
                    snapshot_data["edges"].append({"source": rb, "target": ra, "relation": "ospf_peer"})

            # ── 6. BGP Sessions ──────────────────────────────────────────────
            bgp_to_router: Dict[str, str] = {}  # bgp_session_id → router_id
            query_bgp = f"""
                SELECT bs.id, bs.vrf_id, bs.peer_ip, bs.status, bs.valid_start_ts,
                       v.router_id
                FROM BGPSession bs
                JOIN VRF v ON v.id = bs.vrf_id
                    AND v.valid_start_ts <= @ts
                    AND (v.valid_end_ts > @ts OR v.valid_end_ts IS NULL)
                WHERE bs.valid_start_ts <= @ts
                  AND (bs.valid_end_ts > @ts OR bs.valid_end_ts IS NULL)
            """
            for row in sn.execute_sql(query_bgp, params=params, param_types=param_types):
                bs_id, vrf_id, peer_ip, bs_status, bs_start, router_id = row
                bgp_state = 1.0 if bs_status and bs_status.lower() == "established" else 0.0
                # Normalise valid_start_ts
                if bs_start and hasattr(bs_start, "tzinfo") and bs_start.tzinfo is not None:
                    bs_start = bs_start.replace(tzinfo=None)
                bgp_to_router[bs_id] = router_id
                snapshot_data["nodes"].append({
                    "id": bs_id,
                    "type": "bgp_session",
                    "router_id": router_id,
                    "vrf_id": vrf_id,
                    "peer_ip": peer_ip or "",
                    "bgp_state": bgp_state,
                    "valid_start_ts": bs_start.isoformat() if bs_start else None,
                    "pfx_count_raw": 0.0,
                    # Temporal features — filled by compute_temporal_features()
                    "prefix_count_delta": 0.0,
                    "session_uptime_norm": 0.0,
                })

                # session_on edge: bgp_session → router
                if router_id:
                    snapshot_data["edges"].append({
                        "source": bs_id,
                        "target": router_id,
                        "relation": "session_on",
                    })
            logger.info(f"Fetched {len(bgp_to_router)} BGP sessions")

            # ── 7. bgp_peer edges (router ↔ router via BGP_Peering) ──────────
            query_bgp_peer = f"""
                SELECT DISTINCT v1.router_id AS router_a, v2.router_id AS router_b
                FROM BGP_Peering bp
                JOIN BGPSession bs1 ON bs1.id = bp.session_id_a
                    AND bs1.valid_start_ts <= @ts
                    AND (bs1.valid_end_ts > @ts OR bs1.valid_end_ts IS NULL)
                JOIN BGPSession bs2 ON bs2.id = bp.session_id_b
                    AND bs2.valid_start_ts <= @ts
                    AND (bs2.valid_end_ts > @ts OR bs2.valid_end_ts IS NULL)
                JOIN VRF v1 ON v1.id = bs1.vrf_id
                    AND v1.valid_start_ts <= @ts
                    AND (v1.valid_end_ts > @ts OR v1.valid_end_ts IS NULL)
                JOIN VRF v2 ON v2.id = bs2.vrf_id
                    AND v2.valid_start_ts <= @ts
                    AND (v2.valid_end_ts > @ts OR v2.valid_end_ts IS NULL)
                WHERE bp.valid_start_ts <= @ts
                  AND (bp.valid_end_ts > @ts OR bp.valid_end_ts IS NULL)
                  AND v1.router_id != v2.router_id
            """
            bgp_peer_pairs = set()
            for row in sn.execute_sql(query_bgp_peer, params=params, param_types=param_types):
                ra, rb = row
                pair = tuple(sorted([ra, rb]))
                if pair not in bgp_peer_pairs:
                    bgp_peer_pairs.add(pair)
                    snapshot_data["edges"].append({"source": ra, "target": rb, "relation": "bgp_peer"})
                    snapshot_data["edges"].append({"source": rb, "target": ra, "relation": "bgp_peer"})

            # ── 8. Prometheus metrics ────────────────────────────────────────
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)

            PROMETHEUS_METRIC_MAP = {
                # interface
                "node_network_receive_drop_total":      ("interface", "rx_drops",      "rate"),
                "node_network_transmit_drop_total":     ("interface", "tx_drops",      "rate"),
                "node_network_mtu_bytes":               ("interface", "mtu_raw",        "gauge"),
                "node_network_receive_errs_total":      ("interface", "rx_errs_rate",   "rate"),
                "node_network_receive_bytes_total":     ("interface", "rx_bytes_rate",  "rate"),
                "node_network_transmit_bytes_total":    ("interface", "tx_bytes_rate",  "rate"),
                "node_network_up":                      ("interface", "net_up",         "gauge"),
                # router
                "node_load1":                           ("router",    "cpu",            "gauge"),
                "node_memory_MemAvailable_bytes":       ("router",    "mem_bytes",      "gauge"),
                "frr_route_total":                      ("router",    "ospf_num_routes","gauge"),
                # router-level sum across all peers
                "frr_bgp_peer_prefixes_advertised_count_total": ("bgp_or_router", "pfx_count", "gauge"),
            }

            query_metrics = """
                SELECT node_name, interface, metric_name, value, labels
                FROM NetworkMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                  AND node_name IS NOT NULL
                  AND metric_name IN UNNEST(@metric_names)
            """
            params_m = {
                "t_start": t_start,
                "t_end": timestamp,
                "metric_names": list(PROMETHEUS_METRIC_MAP.keys()),
            }
            ptypes_m = {
                "t_start": spanner.param_types.TIMESTAMP,
                "t_end": spanner.param_types.TIMESTAMP,
                "metric_names": spanner.param_types.Array(spanner.param_types.STRING),
            }

            # Accumulators: {target_id: {metric_key: [values]}}
            prom_agg: Dict[str, Dict[str, list]] = {}

            for row in sn.execute_sql(query_metrics, params=params_m, param_types=ptypes_m):
                node_name, iface_name, metric_name, value, labels_json = row
                if not node_name or value is None:
                    continue
                entry = PROMETHEUS_METRIC_MAP.get(metric_name)
                if not entry:
                    continue
                scope, field, _ = entry

                labels: Dict = {}
                try:
                    if labels_json:
                        labels = json.loads(labels_json) if isinstance(labels_json, str) else labels_json
                except Exception:
                    pass

                if scope == "interface":
                    if iface_name:
                        target_id = f"{node_name}:interface:{iface_name}"
                        prom_agg.setdefault(target_id, {}).setdefault(field, []).append(float(value))
                elif scope == "router":
                    prom_agg.setdefault(node_name, {}).setdefault(field, []).append(float(value))
                elif scope == "bgp_or_router":
                    # Map to router-level sum AND to per-session if peer label available
                    prom_agg.setdefault(node_name, {}).setdefault("pfx_count_router", []).append(float(value))
                    # Try per-session: labels should have 'peer' key with the peer IP
                    peer_ip = labels.get("peer") or labels.get("neighbor")
                    if peer_ip:
                        session_key = f"bgp:{node_name}:{peer_ip}"
                        prom_agg.setdefault(session_key, {}).setdefault("pfx_count_session", []).append(float(value))

            # Average all accumulated samples
            avg_metrics: Dict[str, Dict[str, float]] = {
                tid: {k: sum(vs) / len(vs) for k, vs in mdict.items() if vs}
                for tid, mdict in prom_agg.items()
            }

            # Build lookup: interface_name_on_router → interface node
            # id format for interfaces: "{router_id}:interface:{iface_name}"
            # Prometheus uses node_name (hostname) and interface name
            # Build hostname → router_id map
            hostname_to_router_id: Dict[str, str] = {
                n["hostname"]: n["id"] for n in snapshot_data["nodes"] if n["type"] == "router"
            }

            # Apply metrics to nodes
            for node in snapshot_data["nodes"]:
                ntype = node["type"]

                if ntype == "interface":
                    iid = node["id"]
                    # Try key as "{hostname}:interface:{iface_name}"
                    host = hostname_to_router_id.get(iface_ids.get(iid, ""), "")
                    iface_key = f"{host}:interface:{node['name']}" if host else iid

                    m = avg_metrics.get(iface_key, {})
                    node["rx_drops"] = m.get("rx_drops", 0.0)
                    node["tx_drops"] = m.get("tx_drops", 0.0)
                    mtu_raw = m.get("mtu_raw", 0.0)
                    node["mtu_norm"] = mtu_raw / 9000.0 if mtu_raw > 0 else 0.0
                    node["rx_errs_rate"] = m.get("rx_errs_rate", 0.0)
                    node["rx_bytes_rate"] = m.get("rx_bytes_rate", 0.0)
                    node["tx_bytes_rate"] = m.get("tx_bytes_rate", 0.0)

                    # node_network_up overrides the CRD-derived state if present
                    if "net_up" in m:
                        node["state"] = float(m["net_up"] > 0)

                    # Compute utilisation (bits per second / speed)
                    spd = node.get("speed_bps", 1e9)
                    if spd > 0:
                        node["tx_util"] = min(node["tx_bytes_rate"] * 8 / spd, 1.0)
                        node["rx_util"] = min(node["rx_bytes_rate"] * 8 / spd, 1.0)

                elif ntype == "router":
                    hostname = node.get("hostname", "")
                    m = avg_metrics.get(hostname, {})
                    node["cpu"] = m.get("cpu", 0.0)
                    mem_bytes = m.get("mem_bytes", 0.0)
                    node["mem"] = min(mem_bytes / (4 * 1024 * 1024 * 1024), 1.0)
                    node["ospf_num_routes"] = m.get("ospf_num_routes", 0.0)
                    # Sum per-peer pfx_count_router values already summed via pfx_count_router
                    node["pfx_count_norm"] = m.get("pfx_count_router", 0.0)

                elif ntype == "bgp_session":
                    peer_ip = node.get("peer_ip", "")
                    # Find hostname for this session's router
                    router_id = node.get("router_id", "")
                    # Reverse lookup: router_id → hostname
                    hostname = ""
                    for n in snapshot_data["nodes"]:
                        if n["type"] == "router" and n["id"] == router_id:
                            hostname = n.get("hostname", "")
                            break
                    session_key = f"bgp:{hostname}:{peer_ip}" if hostname and peer_ip else ""
                    m_sess = avg_metrics.get(session_key, {})
                    node["pfx_count_raw"] = m_sess.get("pfx_count_session", 0.0)

        logger.info(
            f"Snapshot {timestamp.isoformat()}: "
            f"{len(snapshot_data['nodes'])} nodes, {len(snapshot_data['edges'])} edges"
        )
        return snapshot_data

    @staticmethod
    def compute_temporal_features(
        snapshots: List[Dict],
        interval_seconds: float = 300.0,
    ) -> List[Dict]:
        """Compute gradient / delta features across consecutive snapshots in-place.

        Modifies each snapshot dict to populate:
          - interface nodes: rx_err_gradient  (rate of change of rx error rate)
          - bgp_session nodes: prefix_count_delta (change in advertised prefixes)
          - bgp_session nodes: session_uptime_norm (age in days, capped at 1.0)

        The first snapshot in the list receives 0.0 for all gradient features.

        Args:
            snapshots:        Ordered list of snapshot dicts (oldest first).
            interval_seconds: Seconds between consecutive snapshots (default 300 = 5 min).

        Returns:
            The same list (mutated in-place) for convenience.
        """
        if not snapshots:
            return snapshots

        # Build lookup: snapshot_index → {node_id: node_dict}
        def _index(snap):
            return {n["id"]: n for n in snap["nodes"]}

        prev_idx = _index(snapshots[0])

        for snap_i, snap in enumerate(snapshots):
            snap_ts = datetime.datetime.fromisoformat(snap["timestamp"])
            curr_idx = _index(snap)

            for node in snap["nodes"]:
                ntype = node["type"]
                nid = node["id"]
                prev = prev_idx.get(nid)

                if ntype == "interface":
                    if prev is not None and snap_i > 0:
                        delta_err = node.get("rx_errs_rate", 0.0) - prev.get("rx_errs_rate", 0.0)
                        node["rx_err_gradient"] = delta_err / interval_seconds if interval_seconds > 0 else 0.0
                    else:
                        node["rx_err_gradient"] = 0.0

                elif ntype == "bgp_session":
                    # prefix_count_delta
                    if prev is not None and snap_i > 0:
                        node["prefix_count_delta"] = (
                            node.get("pfx_count_raw", 0.0) - prev.get("pfx_count_raw", 0.0)
                        )
                    else:
                        node["prefix_count_delta"] = 0.0

                    # session_uptime_norm — age in days, capped at 1.0
                    vs_str = node.get("valid_start_ts")
                    if vs_str:
                        try:
                            vs = datetime.datetime.fromisoformat(vs_str)
                            age_days = (snap_ts - vs).total_seconds() / 86400.0
                            node["session_uptime_norm"] = min(max(age_days, 0.0), 1.0)
                        except Exception:
                            node["session_uptime_norm"] = 0.0
                    else:
                        node["session_uptime_norm"] = 0.0

            prev_idx = curr_idx

        return snapshots


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
    DATABASE_ID = os.getenv("SPANNER_DATABASE", "networktopology-db")
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", None)
    NUM_SNAPSHOTS = 5
    INTERVAL_MINUTES = 5

    dataset = SpannerDataset(
        instance_id=INSTANCE_ID,
        database_id=DATABASE_ID,
        num_snapshots=NUM_SNAPSHOTS,
        interval_minutes=INTERVAL_MINUTES,
        project_id=PROJECT_ID,
    )

    timestamps = dataset._get_timestamps()
    snapshots = []
    for i, ts in enumerate(timestamps):
        try:
            snap = dataset.fetch_snapshot(ts)
            snapshots.append(snap)
            node_types: Dict[str, int] = {}
            for n in snap["nodes"]:
                node_types[n["type"]] = node_types.get(n["type"], 0) + 1
            edge_types: Dict[str, int] = {}
            for e in snap["edges"]:
                edge_types[e["relation"]] = edge_types.get(e["relation"], 0) + 1
            print(f"[{i+1:02d}] {ts.isoformat()}")
            print(f"     Nodes: " + "  ".join(f"{k}={v}" for k, v in sorted(node_types.items())))
            print(f"     Edges: " + "  ".join(f"{k}={v}" for k, v in sorted(edge_types.items())))
        except Exception as exc:
            print(f"[{i+1:02d}] ERROR: {exc}")

    SpannerDataset.compute_temporal_features(snapshots, interval_seconds=INTERVAL_MINUTES * 60)
    print(f"\nFetched {len(snapshots)}/{NUM_SNAPSHOTS} snapshots with temporal features applied.")
