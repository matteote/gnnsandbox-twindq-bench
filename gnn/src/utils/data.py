"""Spanner-backed dataset loader for the four-layer HetGNN RCA pipeline.

SpannerDataset queries Google Cloud Spanner for point-in-time snapshots of the
telco-lab L3VPN network topology and emits self-describing Python dicts that are
consumed downstream by gnn_utils.GraphBuilder and persisted as GCS pickle files
by the Vertex AI ingest KFP component.

Node types returned by fetch_snapshot()
────────────────────────────────────────
router:
    id, type, hostname, role, state, cpu, mem, ospf_num_routes (log1p),
    pfx_count_norm (log1p), bgp_update_rate (log1p), vrf_count (log1p),
    fib_size_norm (log1p), role_P, role_PE, role_RR, role_CE
interface:
    id, type, name, device_id, speed_bps, state, rx_drops, tx_drops,
    mtu_norm, rx_errs_rate, rx_bytes_rate, tx_bytes_rate, tx_queue_len_norm,
    rx_err_gradient, tx_util, rx_util  ← added by compute_temporal_features()
bgp_session:
    id, type, router_id, vrf_id, peer_ip,
    bgp_state        ← 1.0 if frr_bgp_peer_uptime_seconds > 0 else 0.0
    pfx_count_raw, pfx_count_norm (log1p),
    prefix_count_delta, session_uptime_norm  ← both set from frr_bgp_peer_uptime_seconds
    Note: bgp_state and session_uptime_norm are derived from the
    frr_bgp_peer_uptime_seconds metric (20 s cadence) rather than the
    SCD-written BGPSession.status / valid_start_ts (60 s update cycle).
vrf:
    id, type, router_id, vpn_id, vrf_name, status,
    vrf_route_count (log1p), rt_import_hash, rt_export_hash,
    vrf_active_sessions (log1p),
    vrf_route_count_delta  ← added by compute_temporal_features()
    Note: vpn_blue/vpn_red/is_hub one-hots removed — RT hashes capture
    VPN policy identity without per-VPN string enumeration.
flow:
    id, type, flow_name, src_device_id, dst_device_id,
    throughput_bps (log1p), latency_ms_norm, jitter_norm,
    packet_loss_pct, active_sessions (log1p),
    throughput_delta  ← added by compute_temporal_features()
    Config-dependent features removed (throughput_norm, expected_rate_deviation,
    active_sessions_norm, protocol_tcp, is_constant).

Edge relation types
────────────────────
Existing:
    (router,      has_interface,       interface)
    (interface,   connected_to,        interface)
    (router,      ospf_peer,           router)
    (router,      bgp_peer,            router)
    (bgp_session, session_on,          router)
New VRF edges:
    (router,      has_vrf,             vrf)          — bidirectional
    (vrf,         contains_session,    bgp_session)  — bidirectional
    (vrf,         same_vpn_as,         vrf)          — bidirectional
New flow edges:
    (flow,        ingresses_at,        interface)
    (flow,        source_pe,           router)
    (flow,        dest_pe,             router)
    (flow,        belongs_to_vrf,      vrf)

"""

import datetime
import hashlib
import json
import logging
import math
import os
from typing import List, Dict, Optional, Tuple

import google.auth
from google.cloud import spanner

logger = logging.getLogger(__name__)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _render_spanner_query(query: str, params: dict) -> str:
    """Substitute @param placeholders with Spanner-literal values for copy-paste into Spanner Studio."""
    rendered = query
    for key in sorted(params.keys(), key=len, reverse=True):
        value = params[key]
        if isinstance(value, datetime.datetime):
            literal = f"TIMESTAMP '{value.isoformat()}'"
        elif isinstance(value, list):
            parts = []
            for item in value:
                parts.append(f"'{item}'" if isinstance(item, str) else str(item))
            literal = f"[{', '.join(parts)}]"
        elif isinstance(value, str):
            escaped = value.replace("'", "\\'")
            literal = f"'{escaped}'"
        elif value is None:
            literal = "NULL"
        else:
            literal = str(value)
        rendered = rendered.replace(f"@{key}", literal)
    return rendered


def _parse_speed_bps(speed_str: str) -> float:
    """Convert a speed string ('1G', '10G', '1000M', '100M', '8Mbps') to bits-per-second.

    Falls back to 1 Gbps if the string cannot be parsed.
    """
    if not speed_str:
        return 1e9
    s = str(speed_str).strip().upper()
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
        if s.endswith("BPS"):
            return float(s[:-3])
        return float(s)
    except ValueError:
        return 1e9


def _rt_hash(rt_list: list) -> float:
    """Compute a stable normalised hash of a set of Route Target strings.

    Returns a float in [0, 1].  The hash is MD5-based and deterministic —
    the same set of RTs always yields the same value, regardless of list order.
    Deviations across snapshots signal RT policy changes (Fault 4 / Fault 11).
    Returns 0.0 for empty or null input.
    """
    if not rt_list:
        return 0.0
    rt_str = ",".join(sorted(str(r) for r in rt_list))
    h = int(hashlib.md5(rt_str.encode()).hexdigest(), 16)
    return (h % 1_000_003) / 1_000_003.0


def _compute_expected_rate(
    pattern_type: str,
    pattern_config: dict,
    bandwidth_bps: float,
    ts: datetime.datetime,
) -> float:
    """Compute the expected traffic rate in bps at *ts* given the configured pattern.

    Supports the three pattern types used in the telco-lab:
      - ``constant``   — always returns *bandwidth_bps*
      - ``schedule``   — piecewise linear, daily repeat, keyed on UTC time-of-day
      - ``multi_sine`` — superposition of sinusoids anchored to wall-clock UTC

    Falls back to *bandwidth_bps* for unknown pattern types.
    """
    pattern_type = (pattern_type or "constant").lower().strip()

    if pattern_type == "constant":
        return float(bandwidth_bps)

    if pattern_type == "schedule":
        waypoints = pattern_config.get("waypoints", [])
        if not waypoints:
            return float(bandwidth_bps)

        def _secs(t_str: str) -> int:
            h, m = t_str.split(":")
            return int(h) * 3600 + int(m) * 60

        pts = sorted(
            (_secs(w["time"]), _parse_speed_bps(str(w["rate"])))
            for w in waypoints
            if "time" in w and "rate" in w
        )
        if not pts:
            return float(bandwidth_bps)

        secs_now = ts.hour * 3600 + ts.minute * 60 + ts.second
        if secs_now <= pts[0][0]:
            return float(pts[0][1])
        if secs_now >= pts[-1][0]:
            return float(pts[-1][1])
        for i in range(len(pts) - 1):
            t0, r0 = pts[i]
            t1, r1 = pts[i + 1]
            if t0 <= secs_now <= t1:
                frac = (secs_now - t0) / (t1 - t0) if t1 > t0 else 0.0
                return float(r0 + frac * (r1 - r0))
        return float(bandwidth_bps)

    if pattern_type == "multi_sine":
        base_rate = _parse_speed_bps(str(pattern_config.get("base_rate", bandwidth_bps)))
        components = pattern_config.get("components", [])
        # Seconds since Unix epoch in UTC (ts is naive UTC)
        t = (ts - datetime.datetime(1970, 1, 1)).total_seconds()
        rate = base_rate
        for comp in components:
            try:
                period = float(comp.get("period", 86400))
                amplitude = _parse_speed_bps(str(comp.get("amplitude", 0)))
                phase_offset = float(comp.get("phase_offset", 0))
                if period > 0:
                    rate += amplitude * math.sin(2 * math.pi * (t + phase_offset) / period)
            except (TypeError, ValueError):
                pass
        min_rate = _parse_speed_bps(str(pattern_config.get("min_rate", 0)))
        max_rate_cfg = _parse_speed_bps(str(pattern_config.get("max_rate", bandwidth_bps * 2 or 1e9)))
        return float(max(min_rate, min(max_rate_cfg, rate)))

    return float(bandwidth_bps)


# ── SpannerDataset ────────────────────────────────────────────────────────────

class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""

    def __init__(
        self,
        instance_id: str,
        database_id: str,
        num_snapshots: int = 576,
        interval_minutes: float = 0.5,
        project_id: Optional[str] = None,
        from_time: Optional[datetime.datetime] = None,
        to_time: Optional[datetime.datetime] = None,
    ):
        logger.info(
            f"Initializing SpannerDataset: instance_id={instance_id}, "
            f"database_id={database_id}, num_snapshots={num_snapshots}, "
            f"interval_minutes={interval_minutes}, project_id={project_id}, "
            f"from_time={from_time}, to_time={to_time}"
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
        self.from_time = from_time
        self.to_time = to_time
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
                    logger.debug(ts)
                    return ts
        except Exception as e:
            logger.warning(f"Exception: Could not determine latest timestamp, falling back: {e}")
        logger.debug("Could not determine latest timestamp (no data rows), falling back to utcnow()")
        return datetime.datetime.utcnow()

    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps stepping backwards from an end time.

        End time selection:
            - ``to_time`` provided → use it directly.
            - ``to_time`` not provided → query Spanner for the latest data timestamp.

        Number of snapshots:
            - ``from_time`` provided → step back at ``interval_minutes`` until the
              next step would go before ``from_time`` (``num_snapshots`` is ignored).
            - ``from_time`` not provided → generate exactly ``num_snapshots`` timestamps.
        """
        end_time = self.to_time if self.to_time is not None else self._get_latest_timestamp()

        step = datetime.timedelta(minutes=self.interval_minutes)

        if self.from_time is not None:
            timestamps = []
            ts = end_time
            while ts >= self.from_time:
                timestamps.append(ts)
                ts = ts - step
            timestamps.reverse()
        else:
            timestamps = []
            for i in range(self.num_snapshots):
                delta = step * (self.num_snapshots - 1 - i)
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
            nodes       — list of node dicts (router / interface / bgp_session / vrf / flow)
            edges       — list of edge dicts (source, target, relation)

        See module docstring for per-node-type field reference.
        Temporal gradient features are added later by compute_temporal_features().
        """
        logger.info(f"Fetching snapshot for timestamp: {timestamp.isoformat()}")
        snapshot_data: Dict = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}

        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {"ts": timestamp}
        param_types = {"ts": spanner.param_types.TIMESTAMP}

        logger.debug(f"[snapshot] Opening Spanner snapshot for ts={timestamp.isoformat()}")
        with self.database.snapshot(multi_use=True) as sn:

            # ── 1. Routers ──────────────────────────────────────────────────
            router_ids: Dict[str, str] = {}  # router_id → hostname
            query_routers = f"""
                SELECT id, name, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] PhysicalRouter:\n%s", _render_spanner_query(query_routers, params))
            for row in sn.execute_sql(query_routers, params=params, param_types=param_types):
                r_id, r_name, r_role, r_status = row
                state_val = 1.0 if r_status and r_status.lower() == "running" else 0.0
                role_str = (r_role or "").upper()
                router_ids[r_id] = r_name
                logger.debug(f"[snapshot]   router: id={r_id} name={r_name} role={role_str}")
                snapshot_data["nodes"].append({
                    "id": r_id,
                    "type": "router",
                    "hostname": r_name,
                    "role": role_str,
                    "state": state_val,
                    "cpu": 0.0,
                    "mem": 0.0,
                    "ospf_num_routes": 0.0,    # log1p applied after metrics fetch
                    "pfx_count_norm": 0.0,     # log1p applied after metrics fetch
                    "bgp_update_rate": 0.0,    # log1p; needs frr_bgp_update_total in metricscollector
                    "vrf_count": 0.0,          # log1p; computed after VRF nodes are built
                    "fib_size_norm": 0.0,      # log1p; needs frr_route_total_fib in metricscollector
                })
            logger.info(f"Fetched {len(router_ids)} routers")

            # ── 2. Interfaces ────────────────────────────────────────────────
            iface_ids: Dict[str, str] = {}    # interface_id → router_id
            iface_speed: Dict[str, float] = {}  # interface_id → speed_bps
            iface_ips: Dict[str, str] = {}    # interface_id → ip_address (for CE→PE resolution)

            query_ifaces = f"""
                SELECT id, router_id, name, speed, status, ip_address
                FROM PhysicalInterface WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] PhysicalInterface:\n%s", _render_spanner_query(query_ifaces, params))
            for row in sn.execute_sql(query_ifaces, params=params, param_types=param_types):
                i_id, i_router_id, i_name, i_speed, i_status, i_ip = row
                state_val = 1.0 if i_status and i_status.lower() == "up" else 0.0
                speed_bps = _parse_speed_bps(i_speed)
                iface_ids[i_id] = i_router_id
                iface_speed[i_id] = speed_bps
                iface_ips[i_id] = i_ip or ""
                logger.debug(
                    f"[snapshot]   interface: id={i_id} name={i_name} router={i_router_id} "
                    f"speed={speed_bps:.0f}bps status={i_status} state={state_val}"
                )
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
                    "tx_queue_len_norm": 0.0,  # needs node_network_transmit_queue_length in metricscollector
                    # Temporal features — filled in by compute_temporal_features()
                    "rx_err_gradient": 0.0,
                    "tx_util": 0.0,
                    "rx_util": 0.0,
                })
            logger.info(f"Fetched {len(iface_ids)} interfaces")

            # Build IP → router_id lookup for CE→PE resolution (section 12)
            ip_to_router: Dict[str, str] = {}  # ip_address → router_id
            for iface_id, r_id in iface_ids.items():
                ip = iface_ips.get(iface_id, "")
                if ip:
                    ip_to_router[ip] = r_id

            # ── 3. has_interface edges (router → interface) ──────────────────
            logger.debug("[snapshot] Building has_interface edges")
            for iface_node in snapshot_data["nodes"]:
                if iface_node["type"] == "interface":
                    snapshot_data["edges"].append({
                        "source": iface_node["device_id"],
                        "target": iface_node["id"],
                        "relation": "has_interface",
                    })
            logger.debug(
                f"[snapshot] has_interface edges: "
                f"{sum(1 for e in snapshot_data['edges'] if e['relation'] == 'has_interface')}"
            )

            # ── 4. connected_to edges (interface ↔ interface) ────────────────
            connected_to_count = 0
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
            logger.debug("[snapshot:sql] Interface_Link (connected_to):\n%s", _render_spanner_query(query_links, params))
            for row in sn.execute_sql(query_links, params=params, param_types=param_types):
                a, b = row
                snapshot_data["edges"].append({"source": a, "target": b, "relation": "connected_to"})
                snapshot_data["edges"].append({"source": b, "target": a, "relation": "connected_to"})
                connected_to_count += 1
            logger.debug(f"[snapshot] connected_to pairs: {connected_to_count}")

            # ── 5. ospf_peer edges (router ↔ router via shared PhysicalLink) ─
            ospf_peer_count = 0
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
            logger.debug("[snapshot:sql] ospf_peer:\n%s", _render_spanner_query(query_ospf, params))
            for row in sn.execute_sql(query_ospf, params=params, param_types=param_types):
                ra, rb = row
                if ra != rb:
                    snapshot_data["edges"].append({"source": ra, "target": rb, "relation": "ospf_peer"})
                    snapshot_data["edges"].append({"source": rb, "target": ra, "relation": "ospf_peer"})
                    ospf_peer_count += 1
            logger.debug(f"[snapshot] ospf_peer pairs: {ospf_peer_count}")

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
            logger.debug("[snapshot:sql] BGPSession:\n%s", _render_spanner_query(query_bgp, params))
            for row in sn.execute_sql(query_bgp, params=params, param_types=param_types):
                bs_id, vrf_id, peer_ip, bs_status, bs_start, router_id = row
                bgp_to_router[bs_id] = router_id
                logger.debug(
                    f"[snapshot]   bgp_session: id={bs_id} peer_ip={peer_ip} "
                    f"vrf={vrf_id} router={router_id}"
                )
                snapshot_data["nodes"].append({
                    "id": bs_id,
                    "type": "bgp_session",
                    "router_id": router_id,
                    "vrf_id": vrf_id,
                    "peer_ip": peer_ip or "",
                    # bgp_state and session_uptime_norm are set in the metrics
                    # application block (section 8) from frr_bgp_peer_uptime_seconds.
                    "bgp_state": 0.0,
                    "pfx_count_raw": 0.0,
                    "pfx_count_norm": 0.0,    # log1p(pfx_count_raw) — computed after metrics
                    # Temporal feature — filled by compute_temporal_features()
                    "prefix_count_delta": 0.0,
                    "session_uptime_norm": 0.0,  # set from frr_bgp_peer_uptime_seconds in section 8
                })

                # session_on edge: bgp_session → router
                if router_id:
                    snapshot_data["edges"].append({
                        "source": bs_id,
                        "target": router_id,
                        "relation": "session_on",
                    })
            logger.info(f"Fetched {len(bgp_to_router)} BGP sessions")

            # Build CE router set and CE → PE mapping for flow edge resolution
            ce_router_ids: set = {
                n["id"]
                for n in snapshot_data["nodes"]
                if n["type"] == "router" and n.get("role", "") == "CE"
            }
            ce_to_pe: Dict[str, str] = {}  # ce_router_id → pe_router_id
            for node in snapshot_data["nodes"]:
                if node["type"] == "bgp_session":
                    peer_ip = node.get("peer_ip", "")
                    pe_router_id = node.get("router_id", "")
                    peer_router_id = ip_to_router.get(peer_ip, "")
                    if peer_router_id in ce_router_ids and pe_router_id:
                        ce_to_pe[peer_router_id] = pe_router_id

            # ── 7. bgp_peer edges (router ↔ router via BGPSession.peer_ip) ───
            query_bgp_peer = f"""
                SELECT DISTINCT v.router_id AS router_a, pi_peer.router_id AS router_b
                FROM BGPSession bs
                JOIN VRF v ON v.id = bs.vrf_id
                    AND v.valid_start_ts <= @ts
                    AND (v.valid_end_ts > @ts OR v.valid_end_ts IS NULL)
                JOIN PhysicalInterface pi_peer ON pi_peer.ip_address = bs.peer_ip
                    AND pi_peer.valid_start_ts <= @ts
                    AND (pi_peer.valid_end_ts > @ts OR pi_peer.valid_end_ts IS NULL)
                WHERE bs.valid_start_ts <= @ts
                  AND (bs.valid_end_ts > @ts OR bs.valid_end_ts IS NULL)
                  AND v.router_id != pi_peer.router_id
            """
            bgp_peer_pairs: set = set()
            logger.debug("[snapshot:sql] bgp_peer:\n%s", _render_spanner_query(query_bgp_peer, params))
            for row in sn.execute_sql(query_bgp_peer, params=params, param_types=param_types):
                ra, rb = row
                pair = tuple(sorted([ra, rb]))
                if pair not in bgp_peer_pairs:
                    bgp_peer_pairs.add(pair)
                    snapshot_data["edges"].append({"source": ra, "target": rb, "relation": "bgp_peer"})
                    snapshot_data["edges"].append({"source": rb, "target": ra, "relation": "bgp_peer"})
            logger.debug(f"[snapshot] bgp_peer pairs: {len(bgp_peer_pairs)}")

            # ── 8. Prometheus metrics ────────────────────────────────────────
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            logger.debug(f"[snapshot] Metrics window: {t_start.isoformat()} -> {timestamp.isoformat()}")

            # scope values:
            #   "interface"       — keyed by "{hostname}:interface:{iface_name}"
            #   "router"          — keyed by "{hostname}"
            #   "router_and_vrf"  — router-level + per-VRF accumulation (frr_route_total)
            #   "bgp_or_router"   — per-session by peer label + router-level sum
            #   "flow"            — keyed by "flow:{flow_id}" (interface col = flow_id)
            PROMETHEUS_METRIC_MAP: Dict[str, Tuple[str, str, str]] = {
                # ── interface ────────────────────────────────────────────────
                "node_network_receive_drop_total":       ("interface", "rx_drops",          "rate"),
                "node_network_transmit_drop_total":      ("interface", "tx_drops",          "rate"),
                "node_network_mtu_bytes":                ("interface", "mtu_raw",           "gauge"),
                "node_network_receive_errs_total":       ("interface", "rx_errs_rate",      "rate"),
                "node_network_receive_bytes_total":      ("interface", "rx_bytes_rate",     "rate"),
                "node_network_transmit_bytes_total":     ("interface", "tx_bytes_rate",     "rate"),
                "node_network_up":                       ("interface", "net_up",            "gauge"),
                # tx queue length: txqueuelen / 1000 (Fault 8 signal)
                # Requires node_network_transmit_queue_length in metricscollector
                "node_network_transmit_queue_length":    ("interface", "tx_queue_len_raw",  "gauge"),
                # ── router ──────────────────────────────────────────────────
                "node_load1":                            ("router",    "cpu",               "gauge"),
                "node_memory_MemAvailable_bytes":        ("router",    "mem_bytes",         "gauge"),
                # FIB size: needs frr_route_total_fib in metricscollector
                "frr_route_total_fib":                   ("router",    "fib_entries",       "gauge"),
                # BGP UPDATE rate: needs frr_bgp_update_total in metricscollector (Fault 10 signal)
                "frr_bgp_update_total":                  ("router",    "bgp_update_rate",   "rate"),
                # ── router + VRF ─────────────────────────────────────────────
                # frr_route_total has labels {afi, vrf} — used for both router-level
                # ospf_num_routes (sum) and per-VRF vrf_route_count
                "frr_route_total":                       ("router_and_vrf", "route_count",  "gauge"),
                # ── bgp_session + router ────────────────────────────────────
                # Per-session prefix count AND router-level prefix sum
                "frr_bgp_peer_prefixes_advertised_count_total": ("bgp_or_router", "pfx_count",     "gauge"),
                # BGP session uptime — used to derive bgp_state and session_uptime_norm.
                # More timely than the SCD-written BGPSession.status (20 s vs. 60 s).
                # Per-session only (no router-level sum needed).
                "frr_bgp_peer_uptime_seconds":                  ("bgp_or_router", "peer_uptime",   "gauge"),
                # ── flow (traffic-agent Prometheus metrics) ──────────────────
                # interface column = flow_id, node_name = source device name
                "traffic_agent_throughput_bps":          ("flow",      "throughput_bps",    "gauge"),
                "traffic_agent_latency_ms":              ("flow",      "latency_ms",        "gauge"),
                "traffic_agent_jitter_ms":               ("flow",      "jitter_ms",         "gauge"),
                "traffic_agent_packet_loss_pct":         ("flow",      "packet_loss_pct",   "gauge"),
                "traffic_agent_active_sessions":         ("flow",      "active_sessions",   "gauge"),
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

            # Accumulators: {target_key: {field_name: [values]}}
            prom_agg: Dict[str, Dict[str, list]] = {}
            metrics_row_count = 0

            logger.debug("[snapshot:sql] NetworkMetrics:\n%s", _render_spanner_query(query_metrics, params_m))
            for row in sn.execute_sql(query_metrics, params=params_m, param_types=ptypes_m):
                node_name, iface_name, metric_name, value, labels_json = row
                if not node_name or value is None:
                    continue
                entry = PROMETHEUS_METRIC_MAP.get(metric_name)
                if not entry:
                    continue
                scope, field, _ = entry
                metrics_row_count += 1

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

                elif scope == "router_and_vrf":
                    # Router-level: accumulate all afi values for ospf_num_routes
                    prom_agg.setdefault(node_name, {}).setdefault("ospf_num_routes", []).append(float(value))
                    # VRF-level: capture per-VRF IPv4 route counts
                    afi = labels.get("afi", "")
                    vrf_label = labels.get("vrf", "")
                    if afi == "ipv4" and vrf_label and vrf_label.lower() not in ("default", ""):
                        vrf_route_key = f"vrf_route:{node_name}:{vrf_label}"
                        prom_agg.setdefault(vrf_route_key, {}).setdefault("vrf_route_count", []).append(float(value))
                        logger.debug(f"[snapshot]   vrf_route: key={vrf_route_key} value={value}")

                elif scope == "bgp_or_router":
                    # Per-session accumulation — keyed by router hostname + peer IP
                    peer_ip = labels.get("peer") or labels.get("neighbor")
                    if peer_ip:
                        session_key = f"bgp:{node_name}:{peer_ip}"
                        prom_agg.setdefault(session_key, {}).setdefault(field, []).append(float(value))
                    # Router-level prefix sum — only for pfx_count, not peer_uptime
                    if field == "pfx_count":
                        prom_agg.setdefault(node_name, {}).setdefault("pfx_count_router", []).append(float(value))

                elif scope == "flow":
                    # iface_name column = flow_id for TRAFFIC metrics
                    if iface_name:
                        flow_key = f"flow:{iface_name}"
                        prom_agg.setdefault(flow_key, {}).setdefault(field, []).append(float(value))
                        logger.debug(f"[snapshot]   flow_metric: flow={iface_name} field={field} value={value}")

            logger.debug(f"[snapshot] Metrics rows processed: {metrics_row_count}; unique keys: {len(prom_agg)}")

            # Average all accumulated samples
            avg_metrics: Dict[str, Dict[str, float]] = {
                tid: {k: sum(vs) / len(vs) for k, vs in mdict.items() if vs}
                for tid, mdict in prom_agg.items()
            }

            # Build router_id → hostname lookup for interface key resolution
            router_id_to_hostname: Dict[str, str] = {
                n["id"]: n["hostname"] for n in snapshot_data["nodes"] if n["type"] == "router"
            }

            # Apply metrics to existing node types
            for node in snapshot_data["nodes"]:
                ntype = node["type"]

                if ntype == "interface":
                    iid = node["id"]
                    hostname = router_id_to_hostname.get(iface_ids.get(iid, ""), "")
                    iface_key = f"{hostname}:interface:{node['name']}" if hostname else iid
                    m = avg_metrics.get(iface_key, {})
                    if not m:
                        logger.debug(f"[snapshot]   interface {node['name']} (key={iface_key}): no metrics")
                    else:
                        logger.debug(f"[snapshot]   interface {node['name']} (key={iface_key}): {m}")

                    node["rx_drops"] = m.get("rx_drops", 0.0)
                    node["tx_drops"] = m.get("tx_drops", 0.0)
                    mtu_raw = m.get("mtu_raw", 0.0)
                    node["mtu_norm"] = mtu_raw / 9000.0 if mtu_raw > 0 else 0.0
                    node["rx_errs_rate"] = m.get("rx_errs_rate", 0.0)
                    node["rx_bytes_rate"] = m.get("rx_bytes_rate", 0.0)
                    node["tx_bytes_rate"] = m.get("tx_bytes_rate", 0.0)
                    # tx_queue_len_norm: txqueuelen / 1000  (healthy default = 1000 → 1.0)
                    tx_q_raw = m.get("tx_queue_len_raw", 0.0)
                    node["tx_queue_len_norm"] = tx_q_raw / 1000.0 if tx_q_raw > 0 else 0.0

                    # node_network_up overrides CRD-derived state if present
                    if "net_up" in m:
                        node["state"] = float(m["net_up"] > 0)

                    # Utilisation (bits per second / link speed)
                    spd = node.get("speed_bps", 1e9)
                    if spd > 0:
                        node["tx_util"] = min(node["tx_bytes_rate"] * 8 / spd, 1.0)
                        node["rx_util"] = min(node["rx_bytes_rate"] * 8 / spd, 1.0)

                elif ntype == "router":
                    hostname = node.get("hostname", "")
                    m = avg_metrics.get(hostname, {})
                    if not m:
                        logger.debug(f"[snapshot]   router {hostname}: no metrics")
                    else:
                        logger.debug(f"[snapshot]   router {hostname}: {m}")

                    node["cpu"] = m.get("cpu", 0.0)
                    mem_bytes = m.get("mem_bytes", 0.0)
                    node["mem"] = min(mem_bytes / (4 * 1024 * 1024 * 1024), 1.0)
                    # log1p transforms as specified in the four-layer feature model
                    node["ospf_num_routes"] = math.log1p(m.get("ospf_num_routes", 0.0))
                    node["pfx_count_norm"] = math.log1p(m.get("pfx_count_router", 0.0))
                    node["bgp_update_rate"] = math.log1p(m.get("bgp_update_rate", 0.0))
                    # fib_size_norm: log1p(fib_entries / vrf_count) — vrf_count set in section 10
                    node["_fib_entries_raw"] = m.get("fib_entries", 0.0)  # temp; cleaned up in section 10

                elif ntype == "bgp_session":
                    peer_ip = node.get("peer_ip", "")
                    router_id = node.get("router_id", "")
                    hostname = router_id_to_hostname.get(router_id, "")
                    session_key = f"bgp:{hostname}:{peer_ip}" if hostname and peer_ip else ""
                    m_sess = avg_metrics.get(session_key, {})
                    # Prefix count (field="pfx_count" from PROMETHEUS_METRIC_MAP)
                    raw = m_sess.get("pfx_count", 0.0)
                    node["pfx_count_raw"] = raw
                    node["pfx_count_norm"] = math.log1p(raw)
                    # bgp_state and session_uptime_norm from frr_bgp_peer_uptime_seconds.
                    # 1.0 = session established (uptime > 0); 0.0 = session down.
                    # Resets to 0 the moment FRR drops the session — 3× faster than SCD.
                    uptime_seconds = m_sess.get("peer_uptime", 0.0)
                    node["bgp_state"] = 1.0 if uptime_seconds > 0 else 0.0
                    node["session_uptime_norm"] = min(uptime_seconds / 86400.0, 1.0)

            logger.info(
                f"Snapshot metrics applied — "
                f"{sum(1 for n in snapshot_data['nodes'] if n['type'] == 'router')} routers, "
                f"{sum(1 for n in snapshot_data['nodes'] if n['type'] == 'interface')} interfaces, "
                f"{sum(1 for n in snapshot_data['nodes'] if n['type'] == 'bgp_session')} bgp_sessions"
            )

            # ── 9. VRF nodes ─────────────────────────────────────────────────
            # One node per VRF per PE router.  Features are derived from:
            #   - VRF.config JSON  (rt_import, rt_export)
            #   - Prometheus frr_route_total with VRF label (vrf_route_count)
            #   - BGP session count per VRF (vrf_active_sessions)
            vrf_ids: Dict[str, Dict] = {}     # vrf_id → vrf_node dict (for edge building)
            vrf_by_router: Dict[str, list] = {}  # router_id → [vrf_id] (for vrf_count + fib_size_norm)
            # Count BGP sessions per VRF for vrf_active_sessions
            bgp_sessions_per_vrf: Dict[str, int] = {}
            for node in snapshot_data["nodes"]:
                if node["type"] == "bgp_session":
                    vrf_id = node.get("vrf_id", "")
                    if vrf_id:
                        bgp_sessions_per_vrf[vrf_id] = bgp_sessions_per_vrf.get(vrf_id, 0) + 1

            query_vrf = f"""
                SELECT id, router_id, vpn_id, name, rd, status, config
                FROM VRF WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] VRF:\n%s", _render_spanner_query(query_vrf, params))
            for row in sn.execute_sql(query_vrf, params=params, param_types=param_types):
                v_id, v_router_id, v_vpn_id, v_name, v_rd, v_status, v_config_raw = row

                # Parse VRF config JSON for RT policy
                v_config: Dict = {}
                try:
                    if v_config_raw:
                        v_config = (
                            json.loads(v_config_raw)
                            if isinstance(v_config_raw, str)
                            else v_config_raw
                        )
                except Exception:
                    pass
                # RT import/export — try common key variants from VyOS config JSON
                rt_import = (
                    v_config.get("rt_import")
                    or v_config.get("rt-import")
                    or v_config.get("import", {}).get("vpn", [])
                    or []
                )
                rt_export = (
                    v_config.get("rt_export")
                    or v_config.get("rt-export")
                    or v_config.get("export", {}).get("vpn", [])
                    or []
                )
                if isinstance(rt_import, str):
                    rt_import = [rt_import]
                if isinstance(rt_export, str):
                    rt_export = [rt_export]

                # VRF route count from Prometheus (frr_route_total with VRF label)
                # Key built in section 8: "vrf_route:{hostname}:{vrf_label}"
                hostname = router_id_to_hostname.get(v_router_id, "")
                # Try the VRF name as-is, and also without "VRF-" prefix (FRR may use either)
                vrf_label_candidates = [v_name, v_name.replace("VRF-", "")]
                raw_route_count = 0.0
                for candidate in vrf_label_candidates:
                    vrf_route_key = f"vrf_route:{hostname}:{candidate}"
                    if vrf_route_key in avg_metrics:
                        raw_route_count = avg_metrics[vrf_route_key].get("vrf_route_count", 0.0)
                        break

                # Active BGP sessions in this VRF
                vrf_active_sess = bgp_sessions_per_vrf.get(v_id, 0)

                vrf_node = {
                    "id": v_id,
                    "type": "vrf",
                    "router_id": v_router_id,
                    "vpn_id": v_vpn_id or "",
                    "vrf_name": v_name or "",
                    "status": v_status or "",
                    "vrf_route_count": math.log1p(raw_route_count),
                    "rt_import_hash": _rt_hash(rt_import),
                    "rt_export_hash": _rt_hash(rt_export),
                    "vrf_active_sessions": math.log1p(vrf_active_sess),
                    # Temporal feature — filled by compute_temporal_features()
                    "vrf_route_count_delta": 0.0,
                    # Internal: raw route count preserved for temporal delta
                    "_vrf_route_count_raw": raw_route_count,
                }
                snapshot_data["nodes"].append(vrf_node)
                vrf_ids[v_id] = vrf_node
                vrf_by_router.setdefault(v_router_id, []).append(v_id)
                logger.debug(
                    f"[snapshot]   vrf: id={v_id} router={v_router_id} vpn={v_vpn_id} "
                    f"vrf_route_count={raw_route_count:.1f} rt_import={rt_import} rt_export={rt_export}"
                )
            logger.info(f"Fetched {len(vrf_ids)} VRF nodes")

            # ── 10. VRF edges + post-VRF router derivations ──────────────────

            # has_vrf: router ↔ vrf (bidirectional)
            for v_id, vrf_node in vrf_ids.items():
                r_id = vrf_node["router_id"]
                if r_id:
                    snapshot_data["edges"].append({"source": r_id, "target": v_id, "relation": "has_vrf"})
                    snapshot_data["edges"].append({"source": v_id, "target": r_id, "relation": "has_vrf"})

            # contains_session: vrf ↔ bgp_session (bidirectional)
            for node in snapshot_data["nodes"]:
                if node["type"] == "bgp_session":
                    v_id = node.get("vrf_id", "")
                    bs_id = node["id"]
                    if v_id and v_id in vrf_ids:
                        snapshot_data["edges"].append({
                            "source": v_id, "target": bs_id, "relation": "contains_session"
                        })
                        snapshot_data["edges"].append({
                            "source": bs_id, "target": v_id, "relation": "contains_session"
                        })

            # same_vpn_as: vrf ↔ vrf (bidirectional) — group by vpn_id
            vpn_to_vrfs: Dict[str, list] = {}
            for v_id, vrf_node in vrf_ids.items():
                vpn = vrf_node.get("vpn_id", "")
                if vpn:
                    vpn_to_vrfs.setdefault(vpn, []).append(v_id)
            same_vpn_pairs: set = set()
            for vpn, vrf_list in vpn_to_vrfs.items():
                for i in range(len(vrf_list)):
                    for j in range(i + 1, len(vrf_list)):
                        a, b = vrf_list[i], vrf_list[j]
                        pair = (min(a, b), max(a, b))
                        if pair not in same_vpn_pairs:
                            same_vpn_pairs.add(pair)
                            snapshot_data["edges"].append({
                                "source": a, "target": b, "relation": "same_vpn_as"
                            })
                            snapshot_data["edges"].append({
                                "source": b, "target": a, "relation": "same_vpn_as"
                            })
            logger.debug(f"[snapshot] same_vpn_as pairs: {len(same_vpn_pairs)}")

            # Post-VRF derivations on router nodes
            #   vrf_count: log1p(number of VRFs on this router)
            #   fib_size_norm: log1p(fib_entries / vrf_count)
            for node in snapshot_data["nodes"]:
                if node["type"] == "router":
                    r_id = node["id"]
                    vrf_count = len(vrf_by_router.get(r_id, []))
                    node["vrf_count"] = math.log1p(vrf_count)
                    fib_raw = node.pop("_fib_entries_raw", 0.0)
                    if vrf_count > 0 and fib_raw > 0:
                        node["fib_size_norm"] = math.log1p(fib_raw / vrf_count)
                    else:
                        node["fib_size_norm"] = 0.0

            # ── 11. Device lookup (for flow edge resolution) ──────────────────
            # device_id → {interface_id, ip_address}
            device_info: Dict[str, Dict] = {}
            query_devices = f"""
                SELECT id, interface_id, ip_address
                FROM Device WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] Device:\n%s", _render_spanner_query(query_devices, params))
            for row in sn.execute_sql(query_devices, params=params, param_types=param_types):
                d_id, d_iface_id, d_ip = row
                device_info[d_id] = {
                    "interface_id": d_iface_id or "",
                    "ip_address": d_ip or "",
                }
            logger.debug(f"[snapshot] Fetched {len(device_info)} device records")

            # Helper: device_id → PE router_id via Device → CE interface → CE router → PE
            def _resolve_pe(device_id: str) -> str:
                """Return the PE router_id for a given device_id, or '' if unresolvable."""
                dinfo = device_info.get(device_id, {})
                iface_id = dinfo.get("interface_id", "")
                if not iface_id:
                    return ""
                ce_router_id = iface_ids.get(iface_id, "")
                if not ce_router_id:
                    return ""
                return ce_to_pe.get(ce_router_id, ce_router_id)

            # ── 12. TrafficFlow / flow nodes ─────────────────────────────────
            # One node per active flow direction.  Features combine the static
            # TrafficTest config (protocol, pattern) with live traffic-agent metrics.
            query_flows = f"""
                SELECT id, name, src_device_id, dst_device_id, phase, config
                FROM TrafficFlow WHERE {valid_filter}
            """
            logger.debug("[snapshot:sql] TrafficFlow:\n%s", _render_spanner_query(query_flows, params))
            flow_count = 0
            for row in sn.execute_sql(query_flows, params=params, param_types=param_types):
                f_id, f_name, src_dev_id, dst_dev_id, f_phase, f_config_raw = row

                # Parse config JSON for pattern/bandwidth info
                f_config: Dict = {}
                try:
                    if f_config_raw:
                        f_config = (
                            json.loads(f_config_raw)
                            if isinstance(f_config_raw, str)
                            else f_config_raw
                        )
                except Exception:
                    pass

                is_reverse = (f_id or "").endswith("_rev")

                # Fetch live traffic-agent metrics for this flow.
                # All flow features are purely observed — no TrafficFlow.config
                # normalisation (bandwidth, expected rate, protocol, pattern) is used
                # so the model learns "normal" from observed traffic alone.
                flow_key = f"flow:{f_id}"
                fm = avg_metrics.get(flow_key, {})
                throughput_bps = fm.get("throughput_bps", 0.0)

                # Latency / jitter: fixed reference normalisers (not from config)
                latency_ms_norm = fm.get("latency_ms", 0.0) / 100.0   # 100 ms reference
                jitter_norm     = fm.get("jitter_ms",  0.0) / 10.0    # 10 ms reference

                flow_node = {
                    "id": f_id,
                    "type": "flow",
                    "flow_name": f_name or "",
                    "src_device_id": src_dev_id or "",
                    "dst_device_id": dst_dev_id or "",
                    # Purely observed features — no TrafficFlow.config dependency
                    "throughput_bps": math.log1p(throughput_bps),
                    "latency_ms_norm": latency_ms_norm,
                    "jitter_norm": jitter_norm,
                    "packet_loss_pct": fm.get("packet_loss_pct", 0.0),
                    "active_sessions": math.log1p(fm.get("active_sessions", 0.0)),
                    # Temporal feature — filled by compute_temporal_features()
                    "throughput_delta": 0.0,
                }
                snapshot_data["nodes"].append(flow_node)
                flow_count += 1

                # ── Flow edges ──────────────────────────────────────────────

                # ingresses_at: flow → CE interface of source device
                src_iface_id = device_info.get(src_dev_id or "", {}).get("interface_id", "")
                if src_iface_id and src_iface_id in iface_ids:
                    snapshot_data["edges"].append({
                        "source": f_id, "target": src_iface_id, "relation": "ingresses_at"
                    })

                # source_pe: flow → PE router of source device
                src_pe_id = _resolve_pe(src_dev_id or "")
                if src_pe_id and src_pe_id in router_ids:
                    snapshot_data["edges"].append({
                        "source": f_id, "target": src_pe_id, "relation": "source_pe"
                    })

                # dest_pe: flow → PE router of destination device
                dst_pe_id = _resolve_pe(dst_dev_id or "")
                if dst_pe_id and dst_pe_id in router_ids:
                    snapshot_data["edges"].append({
                        "source": f_id, "target": dst_pe_id, "relation": "dest_pe"
                    })

                # belongs_to_vrf: flow → VRF(s) on the source PE matching this VPN
                # Match by extracting the VPN key from the config vpnRef or flow name
                vpn_ref = (f_config.get("vpnRef", "") or f_name or "").lower()
                if src_pe_id:
                    for v_id in vrf_by_router.get(src_pe_id, []):
                        vrf_node = vrf_ids.get(v_id, {})
                        vpn_id_str = (vrf_node.get("vpn_id", "") or "").lower()
                        # Match "blue" keyword from vpnRef against VPN id
                        if (
                            ("blue" in vpn_ref and "blue" in vpn_id_str)
                            or ("red" in vpn_ref and "red" in vpn_id_str)
                        ):
                            snapshot_data["edges"].append({
                                "source": f_id, "target": v_id, "relation": "belongs_to_vrf"
                            })

                logger.debug(
                    f"[snapshot]   flow: id={f_id} is_reverse={is_reverse} "
                    f"throughput_bps={throughput_bps:.0f} bps  "
                    f"src_pe={src_pe_id} dst_pe={dst_pe_id}"
                )

            logger.info(f"Fetched {flow_count} flow nodes")

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
          - interface nodes:    rx_err_gradient  (rate of change of rx error rate)
          - bgp_session nodes:  prefix_count_delta (change in advertised prefixes)
          - vrf nodes:          vrf_route_count_delta (change in IPv4 route count)
          - flow nodes:         throughput_delta (change in normalised throughput)

        Note: bgp_state and session_uptime_norm are set directly from
        frr_bgp_peer_uptime_seconds in fetch_snapshot() (section 8) — they do
        not need to be computed here.

        The first snapshot in the list receives 0.0 for all gradient/delta features.

        Args:
            snapshots:        Ordered list of snapshot dicts (oldest first).
            interval_seconds: Seconds between consecutive snapshots (default 300 = 5 min).

        Returns:
            The same list (mutated in-place) for convenience.
        """
        if not snapshots:
            return snapshots

        def _index(snap: Dict) -> Dict[str, Dict]:
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
                    # prefix_count_delta (uses raw count for meaningful delta)
                    if prev is not None and snap_i > 0:
                        node["prefix_count_delta"] = (
                            node.get("pfx_count_raw", 0.0) - prev.get("pfx_count_raw", 0.0)
                        )
                    else:
                        node["prefix_count_delta"] = 0.0
                    # bgp_state and session_uptime_norm are already set from
                    # frr_bgp_peer_uptime_seconds in fetch_snapshot() — no SCD derivation needed.

                elif ntype == "vrf":
                    # vrf_route_count_delta (uses raw count for meaningful delta)
                    if prev is not None and snap_i > 0:
                        node["vrf_route_count_delta"] = (
                            node.get("_vrf_route_count_raw", 0.0)
                            - prev.get("_vrf_route_count_raw", 0.0)
                        )
                    else:
                        node["vrf_route_count_delta"] = 0.0

                elif ntype == "flow":
                    # throughput_delta: change in log1p(bps) between consecutive snapshots
                    if prev is not None and snap_i > 0:
                        node["throughput_delta"] = (
                            node.get("throughput_bps", 0.0) - prev.get("throughput_bps", 0.0)
                        )
                    else:
                        node["throughput_delta"] = 0.0

            prev_idx = curr_idx

        return snapshots


if __name__ == "__main__":
    import sys
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    _local_creds = str((Path(__file__).resolve().parent.parent / "networkagent.json").resolve())
    logger.info(_local_creds)
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", _local_creds)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    print(f"Using service-account credentials: {creds_path}")

    INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
    DATABASE_ID = os.getenv("SPANNER_DATABASE", "networktopology-db")
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agents-1234")
    NUM_SNAPSHOTS = 1
    INTERVAL_MINUTES = 5

    FROM_TIME = datetime.datetime(2026, 4, 14, 18, 0, 0)
    TO_TIME = datetime.datetime(2026, 4, 14, 21, 0, 0)

    dataset = SpannerDataset(
        instance_id=INSTANCE_ID,
        database_id=DATABASE_ID,
        num_snapshots=NUM_SNAPSHOTS,
        interval_minutes=INTERVAL_MINUTES,
        project_id=PROJECT_ID,
        # from_time=FROM_TIME,
        # to_time=TO_TIME,
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
