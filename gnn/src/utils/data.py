
import datetime
import logging
import os
from typing import List, Dict, Optional

import google.auth
from google.cloud import spanner

logger = logging.getLogger(__name__)

class SpannerDataset:
    """Loads snapshots from Google Spanner using SCD Type 2 query logic."""
    
    def __init__(self, instance_id: str, database_id: str, num_snapshots: int = 50, interval_minutes: int = 5, project_id: Optional[str] = None):
        logger.info(f"Initializing SpannerDataset: instance_id={instance_id}, database_id={database_id}, "
                   f"num_snapshots={num_snapshots}, interval_minutes={interval_minutes}, project_id={project_id}")
        
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/agent/networkagent.json")
        if creds_path and os.path.exists(creds_path):
            logger.debug(f"Loading credentials from file: {creds_path}")
            credentials, detected_project = google.auth.load_credentials_from_file(
                creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            self.client = spanner.Client(project=effective_project, credentials=credentials)
        else:
            # Running on Vertex AI / Cloud Run with ADC (no key file mounted).
            # Explicitly resolve credentials + project via google.auth.default() so we
            # can override the project with the caller-supplied project_id.  Without
            # this override the Spanner client can pick up the Vertex AI *tenant*
            # project (e.g. g83b4821cc8e8a159-tp) instead of the user's project,
            # causing spanner.sessions.create 403 errors.
            logger.debug("GOOGLE_APPLICATION_CREDENTIALS not set or file absent — using ADC")
            credentials, detected_project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            effective_project = project_id or detected_project
            logger.debug(f"ADC resolved project: {detected_project!r}, effective project: {effective_project!r}")
            self.client = spanner.Client(project=effective_project, credentials=credentials)
        self.instance = self.client.instance(instance_id)
        self.database = self.instance.database(database_id)
        self.num_snapshots = num_snapshots
        self.interval_minutes = interval_minutes
        
        logger.info("SpannerDataset initialized successfully")
        
    def _get_latest_timestamp(self) -> datetime.datetime:
        """Query Spanner for the most recent data timestamp across topology and metrics tables.

        Returns the latest `valid_start_ts` from PhysicalRouter / PhysicalInterface and
        the latest `timestamp` from NetworkMetrics, taking the overall maximum.  Falls
        back to `datetime.utcnow()` if the tables are empty or the query fails.
        """
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
                    # Spanner returns timezone-aware datetimes; strip tzinfo for
                    # consistency with the rest of the codebase (naive UTC).
                    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)
                    logger.debug(f"Latest data timestamp from Spanner: {ts.isoformat()}")
                    return ts
        except Exception as e:
            logger.warning(f"Could not determine latest data timestamp, falling back to utcnow(): {e}")
        fallback = datetime.datetime.utcnow()
        logger.debug(f"Using fallback timestamp: {fallback.isoformat()}")
        return fallback

    def _get_timestamps(self) -> List[datetime.datetime]:
        """Generates a list of timestamps ending at the latest data timestamp in Spanner,
        spaced by interval_minutes.  Anchoring to the last known data point (rather than
        utcnow()) ensures the most-recent snapshot always contains data and avoids
        trailing empty windows caused by collection lag."""
        end_time = self._get_latest_timestamp()
        logger.debug(f"Generating {self.num_snapshots} timestamps ending at {end_time.isoformat()}")
        
        timestamps = []
        for i in range(self.num_snapshots):
            delta = datetime.timedelta(minutes=self.interval_minutes * (self.num_snapshots - 1 - i))
            timestamps.append(end_time - delta)
        
        logger.info(f"Generated {len(timestamps)} timestamps from {timestamps[0].isoformat()} to {timestamps[-1].isoformat()}")
        return timestamps

    def fetch_snapshot(self, timestamp: datetime.datetime) -> Dict:
        """
        Fetches the network state at `timestamp` and returns a JSON-compatible dict 
        matching the format expected by GraphBuilder.
        """
        logger.info(f"Fetching snapshot for timestamp: {timestamp.isoformat()}")
        snapshot_data = {"timestamp": timestamp.isoformat(), "nodes": [], "edges": []}
        
        # Active filter for SCD Type 2
        valid_filter = "valid_start_ts <= @ts AND (valid_end_ts > @ts OR valid_end_ts IS NULL)"
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        params = {'ts': timestamp}
        param_types = {'ts': spanner.param_types.TIMESTAMP}
        
        with self.database.snapshot(multi_use=True) as sn:
            # 1. Fetch Routers with ROLE
            # Map role to Node Type: PE Router, P Router, CE Router
            logger.debug("Querying PhysicalRouter table")
            query_routers = f"""
                SELECT id, name, config, role, status
                FROM PhysicalRouter WHERE {valid_filter}
            """
            results = sn.execute_sql(query_routers, params=params, param_types=param_types)
            router_count = 0
            router_types = {"PE Router": 0, "P Router": 0, "CE Router": 0}
            
            for row in results:
                role = row[3]
                
                # Default to P Router
                node_type = "P Router"
                
                # Normalize and map
                if role:
                    role_upper = role.upper()
                    if role_upper == "PE":
                         node_type = "PE Router"
                    elif role_upper == "CE":
                         node_type = "CE Router"
                    elif role_upper == "P":
                         node_type = "P Router"
                    # Legacy fallback
                    elif "PE" in role_upper:
                         node_type = "PE Router"
                    elif "CE" in role_upper:
                         node_type = "CE Router"
                
                router_types[node_type] += 1
                
                # Encode state/status — Spanner stores "Running", "Failed", "Pending"
                state_val = 1.0 if row[4] and row[4].lower() == "running" else 0.0
                
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": node_type,
                    "hostname": row[1],
                    "config": row[2] if row[2] else "",
                    "state": state_val
                })
                router_count += 1
            
            logger.info(f"Fetched {router_count} routers - PE: {router_types['PE Router']}, "
                       f"P: {router_types['P Router']}, CE: {router_types['CE Router']}")
                
            # 2. Fetch Interfaces
            logger.debug("Querying PhysicalInterface table")
            query_interfaces = f"""
                SELECT id, router_id, name, speed, status
                FROM PhysicalInterface WHERE {valid_filter}
            """
            results = sn.execute_sql(query_interfaces, params=params, param_types=param_types)
            interface_count = 0
            interface_up_count = 0
            
            for row in results:
                state_val = 1.0 if row[4] and row[4].lower() == "up" else 0.0
                if state_val == 1.0:
                    interface_up_count += 1
                    
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": "Interface",
                    "name": row[2],
                    "device_id": row[1],
                    "state": state_val,
                    "rx_bytes": 0.0,   # populated below from NetworkMetrics
                    "tx_bytes": 0.0,
                    "rx_errors": 0.0,
                    "tx_errors": 0.0,
                    "rx_drops": 0.0,   # not available in current DB schema
                    "tx_drops": 0.0,
                })
                interface_count += 1
            
            logger.info(f"Fetched {interface_count} interfaces ({interface_up_count} up, {interface_count - interface_up_count} down)")

            # 3. Fetch BGP Sessions
            logger.debug("Querying BGPSession table")
            query_bgp = f"""
                SELECT id, vrf_id, local_as, remote_as, peer_ip, status
                FROM BGPSession WHERE {valid_filter}
            """
            results = sn.execute_sql(query_bgp, params=params, param_types=param_types)
            bgp_count = 0
            bgp_established_count = 0

            for row in results:
                status = row[5]
                state_val = 1.0 if status and status.lower() == "established" else 0.0
                if state_val == 1.0:
                    bgp_established_count += 1

                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": "BGP_Session",
                    "vrf_id": row[1],
                    "local_as": int(row[2]) if row[2] else 0,
                    "remote_as": int(row[3]) if row[3] else 0,
                    "peer_ip": row[4] or "",
                    "state": state_val,
                })
                bgp_count += 1

            logger.info(f"Fetched {bgp_count} BGP sessions ({bgp_established_count} established, {bgp_count - bgp_established_count} idle/down)")

            # 4. Router -> Interface Edges (Owns)
            logger.debug("Creating Router -> Interface 'Owns' edges")
            owns_edge_count = 0
            for node in snapshot_data["nodes"]:
                if node["type"] == "Interface":
                    snapshot_data["edges"].append({
                        "source": node["device_id"],
                        "target": node["id"],
                        "relation": "Owns"
                    })
                    owns_edge_count += 1

            logger.debug(f"Created {owns_edge_count} 'Owns' edges")

            # 5. Interface <-> Interface Edges (Connected)
            # Find interfaces sharing a link
            logger.debug("Querying Interface_Link table for 'Connected' edges")
            query_links = f"""
                SELECT il1.interface_id, il2.interface_id
                FROM Interface_Link il1
                JOIN Interface_Link il2 ON il1.link_id = il2.link_id
                WHERE il1.interface_id < il2.interface_id
                AND il1.valid_start_ts <= @ts AND (il1.valid_end_ts > @ts OR il1.valid_end_ts IS NULL)
                AND il2.valid_start_ts <= @ts AND (il2.valid_end_ts > @ts OR il2.valid_end_ts IS NULL)
            """
            results = sn.execute_sql(query_links, params=params, param_types=param_types)
            connected_edge_count = 0
            
            for row in results:
                # Add bidirectional 'Connected' edge
                snapshot_data["edges"].append({
                    "source": row[0],
                    "target": row[1],
                    "relation": "Connected"
                })
                snapshot_data["edges"].append({
                    "source": row[1],
                    "target": row[0],
                    "relation": "Connected"
                })
                connected_edge_count += 2
            
            logger.debug(f"Created {connected_edge_count} 'Connected' edges")

            # 6. BGP_Session PeersWith Edges (from BGP_Peering table)
            logger.debug("Querying BGP_Peering table for 'PeersWith' edges")
            query_peering = f"""
                SELECT session_id_a, session_id_b
                FROM BGP_Peering
                WHERE {valid_filter}
            """
            results = sn.execute_sql(query_peering, params=params, param_types=param_types)
            peering_edge_count = 0

            # Build a set of valid BGP session IDs for fast lookup
            bgp_session_ids = {n["id"] for n in snapshot_data["nodes"] if n["type"] == "BGP_Session"}

            for row in results:
                src, dst = row[0], row[1]
                # Only add if both sessions exist in the current snapshot
                if src in bgp_session_ids and dst in bgp_session_ids:
                    snapshot_data["edges"].append({"source": src, "target": dst, "relation": "PeersWith"})
                    snapshot_data["edges"].append({"source": dst, "target": src, "relation": "PeersWith"})
                    peering_edge_count += 2

            logger.info(f"Created {peering_edge_count} 'PeersWith' edges from {peering_edge_count // 2} BGP peering pairs")

            # 7. Apply Prometheus metrics (metricscollector)
            # Rows written by the metricscollector have node_name (router hostname),
            # interface (interface name), metric_name, and value columns.
            # PhysicalInterface ID = "router:{node_name}:interface:{interface}".
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            logger.debug(f"Querying Prometheus NetworkMetrics for time range: {t_start.isoformat()} to {timestamp.isoformat()}")

            interface_nodes = [n for n in snapshot_data["nodes"] if n["type"] == "Interface"]

            PROMETHEUS_METRIC_MAP = {
                "node_network_receive_bytes_total":  "rx_bytes",
                "node_network_transmit_bytes_total": "tx_bytes",
                "node_network_receive_errs_total":   "rx_errors",
                "node_network_transmit_errs_total":  "tx_errors",
                "node_network_receive_drop_total":   "rx_drops",
                "node_network_transmit_drop_total":  "tx_drops",
            }

            query_prom_metrics = """
                SELECT node_name, interface, metric_name, value
                FROM NetworkMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                AND node_name IS NOT NULL
                AND interface IS NOT NULL
                AND metric_name IN UNNEST(@metric_names)
            """
            params_prom = {
                "t_start": t_start,
                "t_end": timestamp,
                "metric_names": list(PROMETHEUS_METRIC_MAP.keys()),
            }
            param_types_prom = {
                "t_start": spanner.param_types.TIMESTAMP,
                "t_end": spanner.param_types.TIMESTAMP,
                "metric_names": spanner.param_types.Array(spanner.param_types.STRING),
            }

            prom_results = sn.execute_sql(query_prom_metrics, params=params_prom, param_types=param_types_prom)

            # Aggregate multiple samples in the window by averaging per interface/metric.
            prom_agg: Dict[str, Dict[str, list]] = {}
            for row in prom_results:
                node_name, iface_name, metric_name, value = row
                if not node_name or not iface_name or value is None:
                    continue
                iface_id = f"router:{node_name}:interface:{iface_name}"
                metric_key = PROMETHEUS_METRIC_MAP.get(metric_name)
                if metric_key:
                    prom_agg.setdefault(iface_id, {}).setdefault(metric_key, []).append(float(value))

            avg_metrics: Dict[str, Dict[str, float]] = {
                iface_id: {k: sum(vs) / len(vs) for k, vs in metrics_by_key.items() if vs}
                for iface_id, metrics_by_key in prom_agg.items()
            }
            logger.debug(f"Prometheus metrics resolved for {len(avg_metrics)} interface IDs")

            # Apply averaged metrics to interface nodes.
            metrics_applied = 0
            for node in interface_nodes:
                node_id = node.get("id", "")
                m = avg_metrics.get(node_id, {})
                node["rx_bytes"]  = m.get("rx_bytes",  0.0)
                node["tx_bytes"]  = m.get("tx_bytes",  0.0)
                node["rx_errors"] = m.get("rx_errors", 0.0)
                node["tx_errors"] = m.get("tx_errors", 0.0)
                node["rx_drops"]  = m.get("rx_drops",  0.0)
                node["tx_drops"]  = m.get("tx_drops",  0.0)
                if m:
                    metrics_applied += 1

            logger.info(f"Applied metrics to {metrics_applied}/{interface_count} interfaces")

        total_edges = len(snapshot_data["edges"])
        total_nodes = len(snapshot_data["nodes"])
        logger.info(f"Snapshot complete: {total_nodes} nodes, {total_edges} edges at {timestamp.isoformat()}")
        
        return snapshot_data


if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # ── Configuration ──────────────────────────────────────────────────────────
    INSTANCE_ID      = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
    DATABASE_ID      = os.getenv("SPANNER_DATABASE", "networktopology-db")
    PROJECT_ID       = os.getenv("GOOGLE_CLOUD_PROJECT", None)
    NUM_SNAPSHOTS    = 20
    INTERVAL_MINUTES = 1
    # ───────────────────────────────────────────────────────────────────────────

    print("=" * 70)
    print(f"SpannerDataset smoke-test")
    print(f"  Instance : {INSTANCE_ID}")
    print(f"  Database : {DATABASE_ID}")
    print(f"  Project  : {PROJECT_ID or '(from credentials)'}")
    print(f"  Snapshots: {NUM_SNAPSHOTS}  x  every {INTERVAL_MINUTES} min")
    print("=" * 70)

    dataset = SpannerDataset(
        instance_id=INSTANCE_ID,
        database_id=DATABASE_ID,
        num_snapshots=NUM_SNAPSHOTS,
        interval_minutes=INTERVAL_MINUTES,
        project_id=PROJECT_ID,
    )

    timestamps = dataset._get_timestamps()
    print(f"\nTimestamp window: {timestamps[0].isoformat()}  →  {timestamps[-1].isoformat()}\n")

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

            print(f"[{i+1:02d}/{NUM_SNAPSHOTS}]  {ts.isoformat()}")
            print("         Full Snapshot Model:")
            print(json.dumps(snap, indent=2))
            print(f"         Nodes  ({sum(node_types.values())}): " +
                  "  ".join(f"{k}={v}" for k, v in sorted(node_types.items())))
            print(f"         Edges  ({sum(edge_types.values())}): " +
                  "  ".join(f"{k}={v}" for k, v in sorted(edge_types.items())))

            # Print BGP session status
            bgp_nodes = [n for n in snap["nodes"] if n["type"] == "BGP_Session"]
            if bgp_nodes:
                established = sum(1 for n in bgp_nodes if n.get("state") == 1.0)
                idle = len(bgp_nodes) - established
                print(f"         BGP sessions ({len(bgp_nodes)}): {established} established, {idle} idle/down")
                for s in bgp_nodes:
                    state_str = "ESTABLISHED" if s.get("state") == 1.0 else "IDLE/DOWN   "
                    print(f"           {state_str}  {s['id']:<60}  peer={s.get('peer_ip','?')}")
            else:
                print("         No BGP session nodes found in this snapshot.")

            # Print metrics for every interface node
            iface_nodes = [n for n in snap["nodes"] if n["type"] == "Interface"]
            if iface_nodes:
                print(f"         Interface metrics ({len(iface_nodes)} interfaces):")
                print(f"           {'Name':<30} {'State':<6} {'rx_bytes':>12} {'tx_bytes':>12} "
                      f"{'rx_errors':>10} {'tx_errors':>10} {'rx_drops':>10} {'tx_drops':>10}")
                print(f"           {'-'*30} {'-'*6} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
                for iface in iface_nodes:
                    name  = iface.get("name", iface["id"])[:30]
                    state = "up" if iface.get("state") == 1.0 else "down"
                    print(
                        f"           {name:<30} {state:<6} "
                        f"{iface.get('rx_bytes', 0):>12.0f} {iface.get('tx_bytes', 0):>12.0f} "
                        f"{iface.get('rx_errors', 0):>10.0f} {iface.get('tx_errors', 0):>10.0f} "
                        f"{iface.get('rx_drops', 0):>10.0f} {iface.get('tx_drops', 0):>10.0f}"
                    )
            else:
                print("         No interface nodes found in this snapshot.")
        except Exception as exc:
            print(f"[{i+1:02d}/{NUM_SNAPSHOTS}]  {ts.isoformat()}  ERROR: {exc}")

    print("\n" + "=" * 70)
    print(f"Fetched {len(snapshots)}/{NUM_SNAPSHOTS} snapshots successfully.")
    empty = NUM_SNAPSHOTS - len(snapshots)
    if empty:
        print(f"WARNING: {empty} snapshot(s) were empty or failed.")
    print("=" * 70)



