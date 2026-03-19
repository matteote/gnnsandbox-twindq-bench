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
from agent_library import get_credentials
import json

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

def fetch_all_open_incidents():
    """
    Fetch all open incidents from the Spanner database.
    
    Returns:
        list: List of incident dictionaries with proper field mapping for dashboard
    """
    logger.info("getting all open incidents")
    database = spanner_connect()
    incidents = []
    
    with database.snapshot() as snapshot:
        try:
            # Query for all incidents that are not resolved
            sql = """
                SELECT 
                    id,
                    recordedTimestamp,
                    agentTaskId,
                    issue,
                    strategy,
                    root_cause,
                    resolution,
                    resolvedTimestamp
                FROM Incident 
                WHERE resolvedTimestamp IS NULL
                ORDER BY recordedTimestamp DESC
            """
            results = snapshot.execute_sql(sql)
            
            for row in results:
                # Handle JSON fields that might already be parsed objects or strings
                def parse_json_field(field_value):
                    if field_value is None:
                        return None
                    if isinstance(field_value, str):
                        try:
                            return json.loads(field_value)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse JSON field: {field_value}")
                            return None
                    # If it's already a dict/object, return as-is
                    return field_value
                
                # Convert timestamps to milliseconds for Dart compatibility
                def timestamp_to_millis(timestamp):
                    if timestamp is None:
                        return None
                    if hasattr(timestamp, 'timestamp'):
                        return int(timestamp.timestamp() * 1000)
                    return timestamp
                
                incident = {
                    'id': row[0],
                    'recordedTimestamp': timestamp_to_millis(row[1]),
                    'agentTaskId': row[2],
                    'issue': parse_json_field(row[3]) or {},
                    'strategy': parse_json_field(row[4]),
                    'rootCause': row[5],  # Keep as String to match Spanner String(MAX) type
                    'resolution': row[6],  # Keep as String to match Spanner String(MAX) type
                    'resolvedTimestamp': timestamp_to_millis(row[7]),
                    # Add lastProgressUpdate field based on available data
                    'lastProgressUpdate': timestamp_to_millis(row[1])  # Use recordedTimestamp as fallback
                }
                
                # Log the incident data for debugging
                logger.info(f"Fetched incident {incident['id']}:")
                logger.info(f"  - Has strategy: {incident['strategy'] is not None}")
                logger.info(f"  - Has rootCause: {incident['rootCause'] is not None}")
                logger.info(f"  - Has resolution: {incident['resolution'] is not None}")
                
                incidents.append(incident)
                
        except Exception as e:
            logger.error(f"Error fetching incidents: {e}")
    
    logger.info(f"Successfully fetched {len(incidents)} open incidents with complete data")
    return incidents

def fetch_incident_by_id(incident_id):
    """
    Fetch a specific incident by ID from the Spanner database.
    
    Args:
        incident_id (str): The ID of the incident to fetch
        
    Returns:
        dict: Incident dictionary or None if not found
    """
    database = spanner_connect()
    
    with database.snapshot() as snapshot:
        try:
            sql = """
                SELECT 
                    id,
                    recordedTimestamp,
                    agentTaskId,
                    issue,
                    strategy,
                    root_cause,
                    resolution,
                    resolvedTimestamp
                FROM Incident 
                WHERE id = @incident_id
            """
            results = snapshot.execute_sql(sql, params={'incident_id': incident_id})
            row = results.one_or_none()
            
            if row:
                # Handle JSON fields that might already be parsed objects or strings
                def parse_json_field(field_value):
                    if field_value is None:
                        return None
                    if isinstance(field_value, str):
                        return json.loads(field_value)
                    # If it's already a dict/object, return as-is
                    return field_value
                
                return {
                    'id': row[0],
                    'recordedTimestamp': row[1],
                    'agentTaskId': row[2],
                    'issue': parse_json_field(row[3]) or {},
                    'strategy': parse_json_field(row[4]),
                    'root_cause': parse_json_field(row[5]),
                    'resolution': parse_json_field(row[6]),
                    'resolvedTimestamp': row[7]
                }
                
        except Exception as e:
            logger.error(f"Error fetching incident {incident_id}: {e}")
            
    return None

def update_incident_resolution(incident_id, resolution_data, assigned_agent=None):
    """
    Update the resolution of an incident in the Spanner database.
    
    Args:
        incident_id (str): The ID of the incident to update
        resolution_data (dict): The resolution data
        assigned_agent (str, optional): The agent assigned to the incident
        
    Returns:
        bool: True if successful, False otherwise
    """
    database = spanner_connect()
    
    try:
        with database.batch() as batch:
            # Update the resolution and set resolved timestamp
            update_data = {
                'resolution': json.dumps(resolution_data),
                'resolvedTimestamp': spanner.COMMIT_TIMESTAMP
            }
            
            # If assigned_agent is provided, also update the issue JSON to include it
            if assigned_agent:
                # First fetch the current issue data
                current_incident = fetch_incident_by_id(incident_id)
                if current_incident:
                    issue_data = current_incident['issue']
                    issue_data['assigned_agent'] = assigned_agent
                    update_data['issue'] = json.dumps(issue_data)
            
            batch.update(
                table='Incident',
                columns=['id'] + list(update_data.keys()),
                values=[(incident_id,) + tuple(update_data.values())]
            )
        return True
        
    except Exception as e:
        logger.error(f"Error updating incident {incident_id}: {e}")
        return False

def create_incident(title, description, severity, affected_node=None, assigned_agent=None, agent_task_id=None):
    """
    Create a new incident in the Spanner database.
    
    Args:
        title (str): The title of the incident
        description (str): The description of the incident
        severity (str): The severity level of the incident
        affected_node (str, optional): The affected node
        assigned_agent (str, optional): The agent assigned to the incident
        agent_task_id (str, optional): The agent task ID
        
    Returns:
        str: The ID of the created incident, or None if failed
    """
    database = spanner_connect()
    
    try:
        import uuid
        incident_id = str(uuid.uuid4())
        
        # Create the issue JSON structure
        issue_data = {
            'title': title,
            'description': description,
            'severity': severity
        }
        
        if affected_node:
            issue_data['affected_node'] = affected_node
        if assigned_agent:
            issue_data['assigned_agent'] = assigned_agent
        
        with database.batch() as batch:
            batch.insert(
                table='Incident',
                columns=['id', 'recordedTimestamp', 'agentTaskId', 'issue'],
                values=[(
                    incident_id,
                    spanner.COMMIT_TIMESTAMP,
                    agent_task_id or incident_id,
                    json.dumps(issue_data)
                )]
            )
        return incident_id
        
    except Exception as e:
        logger.error(f"Error creating incident: {e}")
        return None


def clear_incidents():
  """
  Clears all records from the Incident table.
  
  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  database = spanner_connect()
  try:
    def delete_all(transaction):
      row_count = transaction.execute_update(
        "DELETE FROM Incident WHERE 1=1"
      )
      logger.info(f"Deleted {row_count} records from Incident table")
      return row_count
      
    row_count = database.run_in_transaction(delete_all)
    logger.info(f"Successfully cleared {row_count} records from Incident table")
    return True
  except Exception as e:
    logger.error(f"Failed to clear Incident table: {e}")
    return False
