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

