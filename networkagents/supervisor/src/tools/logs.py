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
import json
from agent_library import get_credentials
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

def fetch_log_entries()->str:
  """
  Fetch recent logs related to network and connectivity services

  Returns:
    A list of JSON objects representing each log entry, example log is below:
    {
      "timestamp": "",    # timestamp
      "severity": "",     # severity level
      "source":  "",      # source of the log
      "message": "",      # log message
      "source":  "",      # source of the log
      "details": ""       # any further details
    }
  """
  with database.snapshot() as snapshot:
    try:
      sql = "SELECT timestamp, severity, source, message, content FROM KgLogEntryNode ORDER BY timestamp DESC LIMIT 50"
      results = snapshot.execute_sql(sql)
      
      # Convert to a list of dictionaries that match the LogEntry model in the dashboard app
      log_entries = []
      for row in results:
        timestamp, severity, source, message, content = row
        # full_log_entry = json.loads(content)
        # try:
        #   source = full_log_entry['resource']['labels']['container_name']
        # except Exception as e:
        #   source = '-'
        if not source: source = ''
        log_entries.append({
          'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
          'severity': severity,
          'source': source,
          'message': message,
          'details': {}  # Empty details
        })
      
      return log_entries
    except Exception as e:
      logger.error("Log Entries SQL error: {}".format(e))
      return []  # Return empty list on error

def delete_logs():
  """
  Deletes all records from the KgLogEntryNode table using Partitioned DML,
  which bypasses the 20,000 mutation-per-transaction limit and is safe for
  tables with large numbers of rows.

  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  try:
    row_count = database.execute_partitioned_dml(
      "DELETE FROM KgLogEntryNode WHERE TRUE"
    )
    logger.info(f"Successfully deleted ~{row_count} log entries from KgLogEntryNode")
    return True
  except Exception as e:
    logger.error("Spanner Delete error: {}".format(e))
    return False
