import time
import itertools
import json
import os
import datetime
import threading
from google.api import metric_pb2 as ga_metric
from google.cloud import monitoring_v3
from google.cloud import spanner

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.environ.get("GOOGLE_PROJECT")
INSTANCE_ID = os.environ.get("GOOGLE_SPANNER_INSTANCE")
DATABASE_ID = os.environ.get("GOOGLE_SPANNER_DATABASE")

# Must be greater than the ops agent scrape interval so that we always
# get the same number of data points at each polling interval.
# See ops agent config file for more details:
#   operator/src/vyosvm/playbooks/templates/ops-agent-config.yaml.j2
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 20))

# Spanner write batching
# Each row insert generates mutations for the base table AND every secondary
# index entry.  NetworkMetrics has 9 columns and 4 secondary indexes, giving:
#
#   Base table              9 cols  →  9 mutations
#   NetworkMetricsIdx1      timestamp + id + 7 STORING  →  9 mutations
#   NetworkMetricsByNodeTime      node_name, metric_name, interface, timestamp + id + 3 STORING  →  8 mutations
#   NetworkMetricsByKindNodeTime  kind, node_name, metric_name, timestamp + id + 3 STORING  →  8 mutations
#   NetworkMetricsByKindTime      kind, timestamp + id + 5 STORING  →  8 mutations
#
#   Total: 42 mutations per row  →  hard limit of 80,000 allows at most ~1,904 rows
#
# We default to 1,500 rows (~63,000 mutations) to stay safely under that ceiling.
SPANNER_BATCH_SIZE = int(os.environ.get("SPANNER_BATCH_SIZE", 1500))

# Retention settings
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", 3))
# Metrics clean up frequency
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", 1200))
# Spanner DB connection check interval
DB_CHECK_SECONDS = int(os.environ.get("DB_CHECK_SECONDS", 60))

# ── VyOS router metrics (job=vyos-lab) ────────────────────────────────────────
SELECTED_METRICS = [
  # SYSTEM metrics
  'prometheus.googleapis.com/node_load1/gauge',
  'prometheus.googleapis.com/node_memory_SwapFree_bytes/gauge',
  'prometheus.googleapis.com/node_memory_MemTotal_bytes/gauge',
  'prometheus.googleapis.com/node_memory_MemAvailable_bytes/gauge',
  'prometheus.googleapis.com/node_network_up/gauge',
  'prometheus.googleapis.com/node_network_carrier/gauge',
  'prometheus.googleapis.com/node_network_mtu_bytes/gauge',
  'prometheus.googleapis.com/node_network_carrier_changes_total/counter',
  'prometheus.googleapis.com/node_network_receive_bytes_total/counter',
  'prometheus.googleapis.com/node_network_receive_drop_total/counter',
  'prometheus.googleapis.com/node_network_receive_errs_total/counter',
  'prometheus.googleapis.com/node_network_receive_packets_total/counter',
  'prometheus.googleapis.com/node_network_transmit_bytes_total/counter',
  'prometheus.googleapis.com/node_network_transmit_drop_total/counter',
  'prometheus.googleapis.com/node_network_transmit_errs_total/counter',
  'prometheus.googleapis.com/node_network_transmit_packets_total/counter',
  'prometheus.googleapis.com/node_network_transmit_queue_length/gauge',
  # ROUTING metrics
  'prometheus.googleapis.com/frr_bfd_peer_count/gauge',
  'prometheus.googleapis.com/frr_collector_up/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbor_adjacencies/gauge',
  'prometheus.googleapis.com/frr_ospf_neighbors/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_prefixes_advertised_count_total/gauge',
  'prometheus.googleapis.com/frr_bgp_peer_uptime_seconds/gauge',
  'prometheus.googleapis.com/frr_bgp_update_total/counter',
  'prometheus.googleapis.com/frr_route_total/gauge',
  'prometheus.googleapis.com/frr_route_total_fib/gauge',
  'prometheus.googleapis.com/process_open_fds/gauge',
  'prometheus.googleapis.com/process_network_receive_bytes_total/counter',
  'prometheus.googleapis.com/process_network_transmit_bytes_total/counter',
]

# ── Traffic-agent device metrics (job=traffic-agents) ─────────────────────────
# Scraped from :9091/metrics on each device container.
# Labels per series: flow_id, role, protocol
# Stored in NetworkMetrics with kind="TRAFFIC", interface=flow_id.
#
# Note: bytes_sent_total and bytes_received_total are separate counters — there
# is no combined traffic_agent_bytes_total metric.
TRAFFIC_AGENT_METRICS = [
  'prometheus.googleapis.com/traffic_agent_throughput_bps/gauge',
  'prometheus.googleapis.com/traffic_agent_bytes_sent_total/counter',
  'prometheus.googleapis.com/traffic_agent_bytes_received_total/counter',
  'prometheus.googleapis.com/traffic_agent_latency_ms/gauge',
  'prometheus.googleapis.com/traffic_agent_jitter_ms/gauge',
  'prometheus.googleapis.com/traffic_agent_packet_loss_pct/gauge',
  'prometheus.googleapis.com/traffic_agent_active_sessions/gauge',
  'prometheus.googleapis.com/traffic_agent_flow_running/gauge',
]


# ── Metric descriptor cache ────────────────────────────────────────────────────
# Cloud Monitoring metric descriptors are static (they only change when a new
# metric is registered). Fetching them on every 20-second poll cycle wastes
# 24+ API calls per cycle. We cache them here and refresh hourly.
#
# _descriptor_cache maps metric_type (str) → descriptor object.
# _descriptor_cache_lock guards the dict for thread-safe refresh.
# _descriptor_cache_updated_at is a monotonic timestamp (time.monotonic()).

DESCRIPTOR_CACHE_TTL_SECONDS = int(os.environ.get("DESCRIPTOR_CACHE_TTL", 3600))

_descriptor_cache: dict = {}
_descriptor_cache_updated_at: float = 0.0
# RLock (reentrant) is required because _get_descriptor() acquires this lock
# and then calls _refresh_descriptor_cache() which also acquires it.  A plain
# threading.Lock() would deadlock when the TTL fires after 1 hour.
_descriptor_cache_lock = threading.RLock()


def _refresh_descriptor_cache(client) -> None:
    """Fetch all metric descriptors for known metric types and populate the cache.

    Called once at startup and whenever the cache TTL has expired.  Any metric
    that cannot be fetched is silently omitted (it will be skipped at poll time).
    """
    all_metrics = SELECTED_METRICS + TRAFFIC_AGENT_METRICS
    new_cache: dict = {}
    logger.info(
        "Refreshing metric descriptor cache for %d metric types …", len(all_metrics)
    )
    for metric_type in all_metrics:
        try:
            descriptor = client.get_metric_descriptor(
                name=f"projects/{PROJECT_ID}/metricDescriptors/{metric_type}"
            )
            new_cache[metric_type] = descriptor
        except Exception as e:
            logger.warning("Could not get descriptor for %s: %s", metric_type, e)

    with _descriptor_cache_lock:
        _descriptor_cache.clear()
        _descriptor_cache.update(new_cache)
        global _descriptor_cache_updated_at
        _descriptor_cache_updated_at = time.monotonic()

    logger.info(
        "Descriptor cache refreshed: %d/%d metrics cached.",
        len(new_cache), len(all_metrics),
    )


def _get_descriptor(client, metric_type: str):
    """Return the cached descriptor for *metric_type*, refreshing if stale."""
    global _descriptor_cache_updated_at

    age = time.monotonic() - _descriptor_cache_updated_at
    if age >= DESCRIPTOR_CACHE_TTL_SECONDS or metric_type not in _descriptor_cache:
        # Only one thread should refresh; others fall through to whatever is
        # already in the cache so they are not blocked.
        if _descriptor_cache_lock.acquire(blocking=False):
            try:
                # Re-check inside the lock (another thread may have refreshed).
                age = time.monotonic() - _descriptor_cache_updated_at
                if age >= DESCRIPTOR_CACHE_TTL_SECONDS or metric_type not in _descriptor_cache:
                    _refresh_descriptor_cache(client)
            finally:
                _descriptor_cache_lock.release()

    return _descriptor_cache.get(metric_type)


def get_metric_aggregation(descriptor, window_seconds=60):
    """
    Determines the correct aligner based on the metric's Kind and Value Type.
    """
    kind = descriptor.metric_kind  # GAUGE, CUMULATIVE, or DELTA

    if kind == descriptor.MetricKind.CUMULATIVE:
        # Counters need a rate (delta / time interval)
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_RATE
    elif kind == descriptor.MetricKind.GAUGE:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_MEAN
    else:
        aligner = monitoring_v3.Aggregation.Aligner.ALIGN_NEXT_OLDER

    return monitoring_v3.Aggregation({
        "alignment_period": {"seconds": window_seconds},
        "per_series_aligner": aligner,
        "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_NONE,
    })


def _fetch_metrics(client, project_name, metric_list, label_gate_key=None):
    """
    Generic helper: fetch a list of Cloud Monitoring metric types and return a
    chained iterator of time-series results.

    Descriptors are resolved from the module-level cache (_descriptor_cache)
    instead of calling get_metric_descriptor() on every poll cycle.

    Args:
        metric_list: list of fully-qualified metric type strings.
        label_gate_key: if set, skip descriptors that lack this label key.
    """
    offset = 2  # safety offset so data has arrived before we query
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - offset
    start_ts = now_ts - POLL_INTERVAL

    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": now_ts},
        "start_time": {"seconds": start_ts},
    })

    generators = []

    for metric_type in metric_list:
        descriptor = _get_descriptor(client, metric_type)
        if descriptor is None:
            logger.warning("No cached descriptor for %s — skipping", metric_type)
            continue

        # Optional: skip descriptors that don't carry the expected label
        if label_gate_key:
            has_label = any(lbl.key == label_gate_key for lbl in descriptor.labels)
            if not has_label:
                logger.debug(f"Skipping {metric_type} — missing label '{label_gate_key}'")
                continue

        aggregation = get_metric_aggregation(descriptor, POLL_INTERVAL)
        metric_filter = f'metric.type = "{descriptor.type}"'

        try:
            results = client.list_time_series(request={
                "name": project_name,
                "filter": metric_filter,
                "interval": interval,
                "aggregation": aggregation,
            })
            generators.append(results)
        except Exception as e:
            logger.error(f"Error fetching {descriptor.type}: {e}")

    return itertools.chain.from_iterable(generators)


def fetch_all_vyos_metrics(client, project_name, _start_time):
    """Fetches every metric belonging to the VyOS job (router_name label required)."""
    return _fetch_metrics(client, project_name, SELECTED_METRICS, label_gate_key="router_name")


def fetch_traffic_agent_metrics(client, project_name):
    """Fetches traffic-agent metrics (flow_id label required)."""
    return _fetch_metrics(client, project_name, TRAFFIC_AGENT_METRICS, label_gate_key="flow_id")


def check_spanner_connection(db_container, lock):
    """Checks if the Spanner database exists and reconnects if necessary."""
    try:
        if not db_container['db'].exists():
            with lock:
                if not db_container['db'].exists():
                    logger.warning("Spanner database not found. Attempting to reconnect...")
                    db_container['db'] = spanner_connect()
                    logger.warning("Reconnected to Spanner.")
        else:
            logger.info("Spanner DB connection alive!")
    except Exception as e:
        with lock:
            logger.error(f"Error checking Spanner connection: {e}. Attempting to reconnect...")
            try:
                db_container['db'] = spanner_connect()
                logger.warning("Reconnected to Spanner after error.")
            except Exception as re:
                logger.error(f"Failed to reconnect to Spanner: {re}")


def spanner_connection_worker(db_container, lock):
    """Background worker that regularly checks the Spanner connection."""
    logger.info(f"Spanner connection worker started. Checking every {DB_CHECK_SECONDS} seconds.")
    while True:
        check_spanner_connection(db_container, lock)
        time.sleep(DB_CHECK_SECONDS)


def retention_worker(db_container, lock):
    """Background worker that removes old metrics from Spanner."""
    logger.info(
        f"Retention thread started. Retention period: {RETENTION_HOURS} hours. "
        f"Cleanup interval: {CLEANUP_INTERVAL_SECONDS}s"
    )
    while True:
        try:
            time.sleep(CLEANUP_INTERVAL_SECONDS)
            cutoff_time = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(hours=RETENTION_HOURS)
            )
            logger.debug(f"Retention thread: Cleanup started. Removing data older than {cutoff_time}")
            dml = "DELETE FROM NetworkMetrics WHERE timestamp < @cutoff_time"
            with lock:
                logger.debug("Retention thread: Acquired lock")
                row_count = db_container['db'].execute_partitioned_dml(
                    dml,
                    params={'cutoff_time': cutoff_time},
                    param_types={'cutoff_time': spanner.param_types.TIMESTAMP}
                )
                logger.info(f"Retention thread: Deleted {row_count} old metric rows.")
                logger.debug("Retention thread: Released lock")
        except Exception as e:
            logger.error(f"Error in retention worker: {e}")


def spanner_connect():
    span_client = spanner.Client(project=PROJECT_ID, disable_builtin_metrics=True)
    db = span_client.instance(INSTANCE_ID).database(DATABASE_ID)
    return db


def run_worker():
    mon_client = monitoring_v3.MetricServiceClient()
    db = spanner_connect()
    db_container = {'db': db}
    project_name = f"projects/{PROJECT_ID}"

    lock = threading.Lock()

    # Warm the descriptor cache before starting the poll loop so the very first
    # iteration does not incur individual get_metric_descriptor() calls.
    _refresh_descriptor_cache(mon_client)

    # Start retention thread
    retention_thread = threading.Thread(
        target=retention_worker,
        args=(db_container, lock),
        daemon=True
    )
    retention_thread.start()

    # Start Spanner connection worker thread
    connection_thread = threading.Thread(
        target=spanner_connection_worker,
        args=(db_container, lock),
        daemon=True
    )
    connection_thread.start()

    logger.info(f"Worker Pool started. Polling all metrics every {POLL_INTERVAL}s...")
    last_poll_time = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=POLL_INTERVAL)
    )

    while True:
        current_poll_time = datetime.datetime.now(datetime.timezone.utc)
        data_to_insert = []

        try:
            # Chain VyOS and traffic-agent series into one stream
            series_data = itertools.chain(
                fetch_all_vyos_metrics(mon_client, project_name, last_poll_time),
                fetch_traffic_agent_metrics(mon_client, project_name),
            )

            for series in series_data:
                job_name = series.resource.labels.get("job", "")

                # ── VyOS router metrics ────────────────────────────────────
                if job_name == "vyos-lab":
                    router_name = series.metric.labels.get("router_name")
                    if not router_name:
                        continue

                    logger.debug(f"Processing VyOS series: {series.metric.type}")

                    parts = series.metric.type.split('/')
                    raw_metric_name = parts[-2]
                    prom_type = parts[-1]
                    category = "SYSTEM" if raw_metric_name.startswith("node_") else "ROUTING"
                    iface = series.metric.labels.get("device")

                    for point in series.points:
                        # After ALIGN_MEAN (gauge) or ALIGN_RATE (counter), Cloud
                        # Monitoring always returns a DOUBLE value regardless of the
                        # original metric's value type.  Using double_value directly
                        # avoids the proto-plus incompatibility with WhichOneof and
                        # correctly stores genuine zero readings (0.0 ≠ "no value").
                        val = float(point.value.double_value)
                        data_to_insert.append((
                            point.interval.end_time,
                            router_name,
                            raw_metric_name,
                            prom_type,
                            category,
                            val,
                            json.dumps(dict(series.metric.labels)),
                            iface,
                        ))

                # ── Traffic-agent device metrics ───────────────────────────
                elif job_name == "traffic-agents":
                    flow_id  = series.metric.labels.get("flow_id")
                    role     = series.metric.labels.get("role")
                    protocol = series.metric.labels.get("protocol")

                    if not flow_id:
                        continue

                    # flow_id convention: "{traffictest_name}_{source_device}"
                    # e.g. "dev1-to-hub-tcp_dev1"      → node_name = "dev1"
                    #      "dev1-hub-tcp-bidir_dev1_rev" → node_name = "dev1"
                    # Strip the "_rev" suffix (bidirectional reverse flows) before
                    # splitting, so we always extract the device name correctly.
                    clean_flow_id = flow_id[:-4] if flow_id.endswith("_rev") else flow_id
                    node_name = clean_flow_id.rsplit("_", 1)[-1]

                    logger.debug(
                        f"Processing traffic-agent series: {series.metric.type} "
                        f"flow={flow_id} role={role} protocol={protocol}"
                    )

                    parts = series.metric.type.split('/')
                    raw_metric_name = parts[-2]
                    prom_type = parts[-1]

                    for point in series.points:
                        val = float(point.value.double_value)
                        data_to_insert.append((
                            point.interval.end_time,
                            node_name,          # e.g. "dev1"
                            raw_metric_name,    # e.g. "traffic_agent_throughput_bps"
                            prom_type,          # "gauge" or "counter"
                            "TRAFFIC",          # kind discriminant
                            val,
                            json.dumps({
                                "flow_id":  flow_id,
                                "role":     role,
                                "protocol": protocol,
                            }),
                            None,               # interface column — intentionally blank for TRAFFIC
                        ))

                else:
                    logger.debug(f"Skipping series from unknown job '{job_name}'")

        except Exception as e:
            logger.error(f"Error during API poll: {e}")

        # Batch write to Spanner
        # Split into chunks to stay under Spanner's 80,000-mutation limit.
        # Each row costs 42 mutations (9 base + 4 indexes); safe ceiling is
        # ~1,904 rows. SPANNER_BATCH_SIZE defaults to 1,500.
        if data_to_insert:
            total = len(data_to_insert)
            chunks = [
                data_to_insert[i:i + SPANNER_BATCH_SIZE]
                for i in range(0, total, SPANNER_BATCH_SIZE)
            ]
            logger.info(
                f"Inserting {total} metric points in {len(chunks)} Spanner "
                f"batch(es) of up to {SPANNER_BATCH_SIZE} rows at {current_poll_time}"
            )
            for chunk_idx, chunk in enumerate(chunks):
                try:
                    with lock:
                        logger.debug(
                            f"Main thread: writing chunk {chunk_idx + 1}/{len(chunks)} "
                            f"({len(chunk)} rows)"
                        )
                        with db_container['db'].batch() as batch:
                            batch.insert(
                                table="NetworkMetrics",
                                columns=(
                                    "timestamp", "node_name", "metric_name",
                                    "metric_type", "kind", "value", "labels", "interface"
                                ),
                                values=chunk,
                            )
                except Exception as e:
                    logger.error(
                        f"Error writing to Spanner (chunk {chunk_idx + 1}/{len(chunks)}): {e}"
                    )

        last_poll_time = current_poll_time
        logger.debug(f"Sleeping for {POLL_INTERVAL} seconds")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_worker()
