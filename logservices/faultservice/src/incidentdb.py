
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

from google.cloud import spanner
import logging 
import google.auth
import os
import time
import json
from datetime import datetime

logger = logging.getLogger(__name__)

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

# ------------------------------------------
# Build a serialized JSON representation of the 
# body that fit into a INSERT/UPDATE SQL statement
#
# **WARNING** Please think twice before making modifications
# here as it took me a lot of trial and errors to come up
# with this solution
# ------------------------------------------
def body_sql_json_dump(string_dump):
  # Double escape the \" sequences created by the santitize call so as to build
  # a syntactically correct SQL INSERT statement for Spanner to execute.
  # Also escape single quotes as single quotes are used to enclose the
  # JSON string in the SQL statement.
  return string_dump.replace('\\n','\\\\n').replace('\\"', '\\\\"').replace("'", "\\'")

class IncidentDB:
    _instance = None

    @classmethod
    async def get_instance(cls):
        if IncidentDB._instance is None:
            IncidentDB._instance = cls()
            await IncidentDB._instance.spanner_connect()
        return IncidentDB._instance

    def __init__(self):
        """
        Initialize the Incident DB client.
        """
        self.database = None

    def get_credentials(self):
        try:
            credentials, _ = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/app/networkagent.json"))
            logger.info("Successfully loaded default credentials.")
            return credentials
        except Exception as e:
            logger.error(f"Error loading default credentials: {e}")
            return None

    async def spanner_connect(self):
        logger.info("Spanner connect")
        if self.database is None:
            credentials = self.get_credentials()
            spanner_client = spanner.Client(credentials=credentials)
            instance = spanner_client.instance(SPANNER_INSTANCE)
            self.database = instance.database(SPANNER_DATABASE)

    async def check_for_open_incident(self, incident_id):
        logger.info(f"Check for open incident {incident_id}")
        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                "SELECT id FROM Incident WHERE id = @incident_id AND resolvedTimestamp IS NULL",
                params={"incident_id": incident_id},
                param_types={"incident_id": spanner.param_types.STRING}
            )
            return len(list(results)) > 0

    async def get_incident(self, incident_id):
        """Gets an incident from the database."""
        logger.info(f"get incident {incident_id}")
        try:
            with self.database.snapshot() as snapshot:
                results = snapshot.execute_sql(
                    "SELECT * FROM Incident WHERE id = @incident_id",
                    params={"incident_id": incident_id},
                    param_types={"incident_id": spanner.param_types.STRING}
                )
                for row in results:
                    return row
        except Exception as e:
            logger.error(f"Error getting incident: {e}")
            return None

    async def create_incident(self, incident_data, task_id):
        """Creates an incident in the database."""
        logger.info(f"creating incident {incident_data} {task_id} ")

        # create spanner compatible json
        incident_json=json.dumps(incident_data, ensure_ascii = True)
        incident_json_spanner=body_sql_json_dump(incident_json)
        logger.info(incident_json_spanner)

        upsert_template="INSERT OR UPDATE Incident (id, recordedTimestamp, agentTaskId, issue) VALUES ('{id}', {timestamp}, '{task_id}', JSON '{issue}')"
        # Use UTC timestamp in milliseconds to match dashboard expectations
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        upsert = upsert_template.format(id=task_id, timestamp=timestamp_ms, task_id=task_id, issue=incident_json_spanner)
        logger.info(upsert)

        try:
            def insert_incident(transaction):
                transaction.execute_update(upsert)

            self.database.run_in_transaction(insert_incident)

            logger.info(f"Created incident: {task_id    }")
            return task_id

        except Exception as e:
            logger.error(f"Error creating incident: {e}")
            return None

    async def get_incident_by_issue(self, key, value, key2, value2):
        """Checks if an open incident exists in the database based on two keys in the issue JSON."""
        logger.info(f"get incident by issue {key} {value} {key2} {value2} ")

        sql_template="SELECT * FROM Incident WHERE JSON_VALUE(issue, '$.{key}') = '{value}' AND JSON_VALUE(issue, '$.{key2}') = '{value2}' AND resolvedTimestamp IS NULL"
        sql_query = sql_template.format(key=key, value=value, key2=key2, value2=value2)
        logger.info(sql_query)

        try:
            with self.database.snapshot() as snapshot:
                results = snapshot.execute_sql(sql_query)
                # Convert to list to avoid iterator consumption issues
                result_list = list(results)
                logger.info(f"Found {len(result_list)} results")
                return len(result_list) > 0
        except Exception as e:
            logger.error(f"Error getting incident by issue: {e}")
            return False
