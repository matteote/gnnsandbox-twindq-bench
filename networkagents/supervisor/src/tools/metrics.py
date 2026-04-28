# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from agent_library import get_credentials
import json
from google.cloud import spanner

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
def spanner_connect():
  credentials, _ = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

def _format_metrics_results(results):
    nodes_data = {}
    for row in results:
        node_name, interface, metric_name, value, timestamp = row
        if not node_name:
            continue
            
        if node_name not in nodes_data:
            nodes_data[node_name] = {
                'timestamp': int(timestamp.timestamp() * 1000) if timestamp else 0,
                'metrics': {
                    'interfaces': {},
                    'cpu': {}
                }
            }
            
        if interface:
            if interface not in nodes_data[node_name]['metrics']['interfaces']:
                nodes_data[node_name]['metrics']['interfaces'][interface] = {}
            
            short_metric_name = metric_name.replace('node_network_', '')
            nodes_data[node_name]['metrics']['interfaces'][interface][short_metric_name] = value
        else:
            nodes_data[node_name]['metrics']['cpu'][metric_name] = value

    last_metrics = {}
    for node_name, data in nodes_data.items():
        last_metrics[node_name] = [{
            'timestamp': data['timestamp'],
            'metrics': data['metrics']
        }]
    return last_metrics

def fetch_last_metrics_for_id(node_id):
  with database.snapshot() as snapshot:
    try:
      sql = f"""SELECT t1.node_name, t1.interface, t1.metric_name, t1.value, t1.timestamp 
      FROM NetworkMetrics t1 
      JOIN (
        SELECT node_name, interface, metric_name, MAX(timestamp) as max_ts 
        FROM NetworkMetrics 
        WHERE node_name = @node_id
        GROUP BY node_name, interface, metric_name
      ) t2 
      ON t1.node_name = t2.node_name AND t1.interface = t2.interface AND t1.metric_name = t2.metric_name AND t1.timestamp = t2.max_ts"""
      
      results = snapshot.execute_sql(
          sql, 
          params={"node_id": node_id}, 
          param_types={"node_id": spanner.param_types.STRING}
      )
      return {"node_metrics": _format_metrics_results(results)}
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return {}

def fetch_all_metrics_for_id(node_id):
  # Legacy support fallback mapping to last metrics to prevent query explosion
  return fetch_last_metrics_for_id(node_id)


def fetch_all_last_metrics():
  with database.snapshot() as snapshot:
    try:
      sql = """SELECT t1.node_name, t1.interface, t1.metric_name, t1.value, t1.timestamp 
      FROM NetworkMetrics t1 
      JOIN (
        SELECT node_name, interface, metric_name, MAX(timestamp) as max_ts 
        FROM NetworkMetrics 
        GROUP BY node_name, interface, metric_name
      ) t2 
      ON t1.node_name = t2.node_name AND t1.interface = t2.interface AND t1.metric_name = t2.metric_name AND t1.timestamp = t2.max_ts"""
      
      results = snapshot.execute_sql(sql)
      return {"node_metrics": _format_metrics_results(results)}
    except Exception as e:
      logger.error("Metrics SQL error: {}".format(e))
      return {}
    
def fetch_all_metrics():
  # Legacy support fallback mapping to last metrics to prevent query explosion
  return fetch_all_last_metrics()

def fetch_traffic_test_metrics(test_name: str) -> list:
  """
  Fetch the latest traffic-agent metrics for all flows belonging to a TrafficTest.

  Flow IDs follow the naming convention  {test_name}_{source_device}  and
  {test_name}_{source_device}_rev  (bidirectional reverse).  We match all of
  them by looking for flow_id values that start with  "{test_name}_".

  Returns a list of dicts — one per (flow_id, role, protocol) group — each
  containing the latest value for every traffic-agent metric scraped into
  NetworkMetrics (kind='TRAFFIC').
  """
  prefix = f"{test_name}_"
  with database.snapshot() as snapshot:
    try:
      # NOTE: labels is a Spanner JSON type.  Spanner JSON does NOT support
      # GROUP BY or equality (=) operators.  We use TO_JSON_STRING() to convert
      # to STRING first, which allows both GROUP BY and equality comparisons.
      # JSON_VALUE() returns STRING so STARTS_WITH is safe.
      sql = """
        SELECT t1.node_name, t1.metric_name, t1.value, t1.labels, t1.timestamp
        FROM NetworkMetrics t1
        JOIN (
          SELECT
            node_name,
            metric_name,
            TO_JSON_STRING(labels) AS labels_str,
            MAX(timestamp)         AS max_ts
          FROM NetworkMetrics
          WHERE kind = 'TRAFFIC'
            AND STARTS_WITH(JSON_VALUE(labels, '$.flow_id'), @prefix)
          GROUP BY node_name, metric_name, TO_JSON_STRING(labels)
        ) t2
        ON  t1.node_name              = t2.node_name
        AND t1.metric_name            = t2.metric_name
        AND TO_JSON_STRING(t1.labels) = t2.labels_str
        AND t1.timestamp              = t2.max_ts
        WHERE t1.kind = 'TRAFFIC'
      """
      results = snapshot.execute_sql(
          sql,
          params={"prefix": prefix},
          param_types={"prefix": spanner.param_types.STRING},
      )

      # Group by (flow_id, role) — both the source device and destination
      # device emit metrics under the SAME flow_id but with different role
      # labels.  Using flow_id alone would cause one to overwrite the other,
      # making the role field non-deterministic (dependent on SQL row order).
      # A key of (flow_id, role) keeps them as separate flow entries so the
      # dashboard can correctly filter source-role flows for throughput.
      _METRIC_MAP = {
        "traffic_agent_throughput_bps":       "throughput_bps",
        "traffic_agent_latency_ms":           "latency_ms",
        "traffic_agent_jitter_ms":            "jitter_ms",
        "traffic_agent_packet_loss_pct":      "packet_loss_pct",
        "traffic_agent_active_sessions":      "active_sessions",
        "traffic_agent_bytes_sent_total":     "bytes_sent_total",
        "traffic_agent_bytes_received_total": "bytes_received_total",
        "traffic_agent_flow_running":         "flow_running",
      }

      flows: dict = {}
      for node_name, metric_name, value, labels_json, timestamp in results:
        try:
          labels = json.loads(labels_json) if isinstance(labels_json, str) else (labels_json or {})
        except Exception:
          labels = {}

        flow_id  = labels.get("flow_id", "")
        role     = labels.get("role", "")
        protocol = labels.get("protocol", "")

        if not flow_id:
          continue

        key = (flow_id, role)
        if key not in flows:
          flows[key] = {
            "flow_id":  flow_id,
            "device":   node_name or "",
            "role":     role,
            "protocol": protocol,
            "timestamp": timestamp.isoformat() if timestamp else None,
            # metric fields initialised to None
            "throughput_bps":       None,
            "latency_ms":           None,
            "jitter_ms":            None,
            "packet_loss_pct":      None,
            "active_sessions":      None,
            "bytes_sent_total":     None,
            "bytes_received_total": None,
            "flow_running":         None,
          }

        # Keep the most recent timestamp for the group.
        if timestamp and flows[key]["timestamp"]:
          if timestamp.isoformat() > flows[key]["timestamp"]:
            flows[key]["timestamp"] = timestamp.isoformat()

        field = _METRIC_MAP.get(metric_name)
        if field:
          flows[key][field] = value

      return list(flows.values())

    except Exception as e:
      logger.error("fetch_traffic_test_metrics SQL error: {}".format(e))
      return []


def fetch_routing_metrics(node_id: str) -> dict:
    """
    Fetch the latest underlay (Layer 2) routing-protocol metrics for a router.

    Queries NetworkMetrics with kind='ROUTING' for the given node, using the
    full (metric_name, labels) key so that per-interface OSPF entries, per-peer
    BGP entries, etc. are preserved and aggregated correctly.

    Returns a structured dict with OSPF, BGP peer, routing-table, collector,
    and BFD sections ready to be JSON-serialised and sent to the dashboard.
    """
    with database.snapshot() as snapshot:
        try:
            # NOTE: labels is a Spanner JSON type.  Use TO_JSON_STRING() for
            # GROUP BY and equality comparisons (JSON doesn't support either).
            sql = """
                SELECT t1.metric_name, t1.value, t1.labels, t1.timestamp
                FROM NetworkMetrics t1
                JOIN (
                    SELECT
                        metric_name,
                        TO_JSON_STRING(labels) AS labels_str,
                        MAX(timestamp)         AS max_ts
                    FROM NetworkMetrics
                    WHERE node_name = @node_id
                      AND kind = 'ROUTING'
                    GROUP BY metric_name, TO_JSON_STRING(labels)
                ) t2
                ON  t1.metric_name            = t2.metric_name
                AND TO_JSON_STRING(t1.labels) = t2.labels_str
                AND t1.timestamp              = t2.max_ts
                WHERE t1.node_name = @node_id
                  AND t1.kind = 'ROUTING'
            """
            results = snapshot.execute_sql(
                sql,
                params={"node_id": node_id},
                param_types={"node_id": spanner.param_types.STRING},
            )

            ospf_neighbors   = 0
            ospf_adjacencies = 0
            bgp_peers   = {}   # key = (neighbor, afi, vrf)
            routes      = {}   # key = (afi, vrf)
            collectors  = {}   # key = collector name string
            bfd_peers   = None
            latest_ts   = None

            for metric_name, value, labels_json, timestamp in results:
                try:
                    labels = json.loads(labels_json) if isinstance(labels_json, str) else (labels_json or {})
                except Exception:
                    labels = {}

                if latest_ts is None or (timestamp and timestamp > latest_ts):
                    latest_ts = timestamp

                if metric_name == 'frr_ospf_neighbors':
                    ospf_neighbors += int(value or 0)

                elif metric_name == 'frr_ospf_neighbor_adjacencies':
                    ospf_adjacencies += int(value or 0)

                elif metric_name == 'frr_bgp_peer_uptime_seconds':
                    neighbor = labels.get('neighbor', '')
                    afi      = labels.get('afi', '')
                    vrf      = labels.get('vrf', 'default')
                    key      = (neighbor, afi, vrf)
                    bgp_peers[key] = {
                        'neighbor':        neighbor,
                        'afi':             afi,
                        'vrf':             vrf,
                        'uptime_seconds':  float(value) if value is not None else None,
                    }

                elif metric_name == 'frr_route_total':
                    afi = labels.get('afi', '')
                    vrf = labels.get('vrf', 'default')
                    key = (afi, vrf)
                    if key not in routes:
                        routes[key] = {'afi': afi, 'vrf': vrf, 'total': None, 'fib': None}
                    routes[key]['total'] = int(value) if value is not None else None

                elif metric_name == 'frr_route_total_fib':
                    afi = labels.get('afi', '')
                    vrf = labels.get('vrf', 'default')
                    key = (afi, vrf)
                    if key not in routes:
                        routes[key] = {'afi': afi, 'vrf': vrf, 'total': None, 'fib': None}
                    routes[key]['fib'] = int(value) if value is not None else None

                elif metric_name == 'frr_collector_up':
                    collector = labels.get('collector', 'unknown')
                    collectors[collector] = int(value) if value is not None else 0

                elif metric_name == 'frr_bfd_peer_count':
                    bfd_peers = int(value) if value is not None else None

            return {
                'node_id':    node_id,
                'ospf':       {'neighbors': ospf_neighbors, 'adjacencies': ospf_adjacencies},
                'bgp_peers':  list(bgp_peers.values()),
                'routes':     list(routes.values()),
                'collectors': collectors,
                'bfd_peers':  bfd_peers,
                'timestamp':  latest_ts.isoformat() if latest_ts else None,
            }

        except Exception as e:
            logger.error(f"fetch_routing_metrics SQL error: {e}")
            return {'node_id': node_id, 'error': str(e)}


def clear_network_metrics():
  """
  Clears all records from the NetworkMetrics table using Partitioned DML,
  which bypasses the 20,000 mutation-per-transaction limit and is safe for
  tables with large numbers of rows.
  
  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  try:
    row_count = database.execute_partitioned_dml(
      "DELETE FROM NetworkMetrics WHERE TRUE"
    )
    logger.info(f"Successfully cleared ~{row_count} records from NetworkMetrics table")
    return True
  except Exception as e:
    logger.error(f"Failed to clear NetworkMetrics table: {e}")
    return False

