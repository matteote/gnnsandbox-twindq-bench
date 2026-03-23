
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
            self.client = spanner.Client(
                project=effective_project,
                credentials=credentials,
                disable_builtin_metrics=True,
            )
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
                # Always map to 'router'
                node_type = "router"
                
                router_types[node_type] = router_types.get(node_type, 0) + 1
                
                # Encode state/status — Spanner stores "Running", "Failed", "Pending"
                state_val = 1.0 if row[4] and row[4].lower() == "running" else 0.0
                
                snapshot_data["nodes"].append({
                    "id": row[0],
                    "type": node_type,
                    "hostname": row[1],
                    "config": row[2] if row[2] else "",
                    "state": state_val,
                    "cpu": 0.0,
                    "mem": 0.0,
                    "ospf_num_routes": 0.0,
                    "pfx_count_norm": 0.0,
                })
                router_count += 1
            
            logger.info(f"Fetched {router_count} routers")
                
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
                    "type": "interface",
                    "name": row[2],
                    "device_id": row[1],
                    "state": state_val,
                    "rx_drops": 0.0,
                    "tx_drops": 0.0,
                    "mtu_norm": 0.0
                })
                interface_count += 1
            
            logger.info(f"Fetched {interface_count} interfaces ({interface_up_count} up, {interface_count - interface_up_count} down)")

            # 3. Router -> Interface Edges (Owns)
            logger.debug("Creating Router -> Interface 'Owns' edges")
            owns_edge_count = 0
            for node in snapshot_data["nodes"]:
                if node["type"] == "interface":
                    snapshot_data["edges"].append({
                        "source": node["device_id"],
                        "target": node["id"],
                        "relation": "Owns"
                    })
                    owns_edge_count += 1

            logger.debug(f"Created {owns_edge_count} 'Owns' edges")

            # 4. Interface <-> Interface Edges (Connected)
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

            # 5. Apply Prometheus metrics (metricscollector)
            # Rows written by the metricscollector have node_name (router hostname),
            # interface (interface name), metric_name, and value columns.
            # PhysicalInterface ID = "router:{node_name}:interface:{interface}".
            t_start = timestamp - datetime.timedelta(minutes=self.interval_minutes)
            logger.debug(f"Querying Prometheus NetworkMetrics for time range: {t_start.isoformat()} to {timestamp.isoformat()}")

            PROMETHEUS_METRIC_MAP = {
                # interface
                "node_network_receive_drop_total":   "rx_drops",
                "node_network_transmit_drop_total":  "tx_drops",
                "node_network_mtu_bytes":            "mtu_norm",
                # router
                "node_load1":                        "cpu",
                "node_memory_MemAvailable_bytes":    "mem",
                "frr_route_total":                   "ospf_num_routes",
                # router — BGP prefix count summed across all peers on the router
                "frr_bgp_peer_prefixes_advertised_count_total":    "pfx_count_norm",
            }

            query_prom_metrics = """
                SELECT node_name, interface, metric_name, value, labels
                FROM NetworkMetrics
                WHERE timestamp > @t_start AND timestamp <= @t_end
                AND node_name IS NOT NULL
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

            # Aggregate multiple samples in the window by averaging.
            prom_agg: Dict[str, Dict[str, list]] = {}
            for row in prom_results:
                node_name, iface_name, metric_name, value, labels_json = row
                if not node_name or value is None:
                    continue
                
                metric_key = PROMETHEUS_METRIC_MAP.get(metric_name)
                if not metric_key:
                    continue
                    
                # Parse labels if needed
                labels = {}
                try:
                    if labels_json:
                        labels = json.loads(labels_json)
                except: pass
                
                if metric_key in ["rx_drops", "tx_drops", "mtu_norm"]:
                    if iface_name:
                        target_id = f"router:{node_name}:interface:{iface_name}"
                        prom_agg.setdefault(target_id, {}).setdefault(metric_key, []).append(float(value))
                elif metric_key in ["cpu", "mem", "ospf_num_routes", "pfx_count_norm"]:
                    # pfx_count_norm is emitted once per BGP peer; accumulate all peers'
                    # values under the router hostname and sum them in avg_metrics below.
                    target_id = node_name
                    prom_agg.setdefault(target_id, {}).setdefault(metric_key, []).append(float(value))

            avg_metrics: Dict[str, Dict[str, float]] = {
                target_id: {k: sum(vs) / len(vs) for k, vs in metrics_by_key.items() if vs}
                for target_id, metrics_by_key in prom_agg.items()
            }
            logger.debug(f"Prometheus metrics resolved for {len(avg_metrics)} IDs")

            # Apply averaged metrics to nodes.
            metrics_applied = 0
            for node in snapshot_data["nodes"]:
                node_type = node.get("type", "")
                
                if node_type == "interface":
                    node_id = node.get("id", "")
                    m = avg_metrics.get(node_id, {})
                    node["rx_drops"]  = m.get("rx_drops",  0.0)
                    node["tx_drops"]  = m.get("tx_drops",  0.0)
                    mtu = m.get("mtu_norm", 0.0)
                    node["mtu_norm"] = mtu / 9000.0 if mtu > 0 else 0.0
                    if m: metrics_applied += 1
                
                elif node_type == "router":
                    hostname = node.get("hostname", "")
                    m = avg_metrics.get(hostname, {})
                    node["cpu"] = m.get("cpu", 0.0)
                    # Normalize mem (simple division mapping to 0-1)
                    mem = m.get("mem", 0.0)
                    node["mem"] = min(mem / (4 * 1024 * 1024 * 1024), 1.0)  # Assume 4 GiB base
                    node["ospf_num_routes"] = m.get("ospf_num_routes", 0.0)
                    # pfx_count_norm: sum of per-peer prefix counts collected in the
                    # window, averaged over scrape intervals (StandardScaler normalises
                    # the absolute scale at training time).
                    node["pfx_count_norm"] = m.get("pfx_count_norm", 0.0)
                    if m: metrics_applied += 1

            logger.info(f"Applied metrics to nodes: {metrics_applied}")

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
            # print(json.dumps(snap, indent=2))
            print(f"         Nodes  ({sum(node_types.values())}): " +
                  "  ".join(f"{k}={v}" for k, v in sorted(node_types.items())))
            print(f"         Edges  ({sum(edge_types.values())}): " +
                  "  ".join(f"{k}={v}" for k, v in sorted(edge_types.items())))

            # Print router metrics
            router_nodes = [n for n in snap["nodes"] if n["type"] == "router"]
            if router_nodes:
                print(f"         Router metrics ({len(router_nodes)} routers):")
                print(f"           {'Hostname':<20} {'State':<6} {'cpu':>8} {'mem':>8} {'routes':>8} {'pfx_count_norm':>14}")
                print(f"           {'-'*20} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*14}")
                for r in router_nodes:
                    state = "up" if r.get("state") == 1.0 else "down"
                    print(
                        f"           {r.get('hostname', r['id'])[:20]:<20} {state:<6} "
                        f"{r.get('cpu', 0):>8.3f} {r.get('mem', 0):>8.3f} "
                        f"{r.get('ospf_num_routes', 0):>8.0f} {r.get('pfx_count_norm', 0):>14.1f}"
                    )

            # Print metrics for every interface node
            iface_nodes = [n for n in snap["nodes"] if n["type"] == "interface"]
            if iface_nodes:
                print(f"         Interface metrics ({len(iface_nodes)} interfaces):")
                print(f"           {'Name':<30} {'State':<6} {'rx_drops':>10} {'tx_drops':>10} {'mtu_norm':>10}")
                print(f"           {'-'*30} {'-'*6} {'-'*10} {'-'*10} {'-'*10}")
                for iface in iface_nodes:
                    name  = iface.get("name", iface["id"])[:30]
                    state = "up" if iface.get("state") == 1.0 else "down"
                    print(
                        f"           {name:<30} {state:<6} "
                        f"{iface.get('rx_drops', 0):>10.0f} {iface.get('tx_drops', 0):>10.0f} "
                        f"{iface.get('mtu_norm', 0):>10.2f}"
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



