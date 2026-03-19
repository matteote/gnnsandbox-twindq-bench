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

from typing import Annotated, List, Dict
import logging
import datetime
import json
from utils.k8s import get_credentials
from google.cloud import spanner
import utils.globals as globals
from mcp.types import ToolAnnotations
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'

logger = logging.getLogger(__name__)

# Connect to Spanner database
def spanner_connect():
  credentials = get_credentials()
  logger.debug(credentials)
  spanner_client = spanner.Client(credentials=credentials)
  instance = spanner_client.instance(SPANNER_INSTANCE)
  database = instance.database(SPANNER_DATABASE)
  return database

database = spanner_connect()

# Parameters for vertex AI embedding
TASK_TYPE = "QUESTION_ANSWERING"
EMBEDDING_MODEL_NAME="text-embedding-005"
EMBEDDING_MODEL = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)

def get_embedding(text, task_type, model):
  """Given a piece of text return the embedding (Array of Float64)"""
  try:
    text_embedding_input = TextEmbeddingInput(task_type=task_type, text=text)
    embeddings = model.get_embeddings([text_embedding_input])
    return embeddings[0].values
  except Exception as e:
    logger.error(f"Embedding error: {e}")
    return []

@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def query_log_entries(
    request: Annotated[str, "natural language log query request"],
    severity_levels: Annotated[str, "one or more severity levels separated by a comma or a space to filter the logs. None if unspecified"],
    start_time: Annotated[str, "A start time string with the format YYYY-MM-DD HH:MM:SS that is suitable to create a python datetime object. None if unspecified"],
    end_time: Annotated[str, "An end time string with the format YYYY-MM-DD HH:MM:SS that is suitable to create a python datetime object. None if unspecified"]
  ) -> str:
  """
  Fetch recent logs related to network and connectivity services. The query will semantically search all logs in a given time period, the logs include 
  automation and software output running on a ComputeInstance. 
  
  Returns:
    A list of JSON objects representing each log entry, example log is below:
    {
      "timestamp": "",    # timestamp
      "severity": "",     # severity level
      "message": "",      # log message
      "source":  "",      # source of the log
      "details": ""       # any further details
    }
  """

  logger.info(f"query_log_entries called with args: {request}, {severity_levels}, {start_time}, {end_time} ")
  format_str = "%Y-%m-%d %H:%M:%S"

  start_time_obj = datetime.datetime.strptime(start_time, format_str) if start_time else None
  end_time_obj = datetime.datetime.strptime(end_time, format_str) if end_time else None


  with database.snapshot() as snapshot:
    try:
      # 1. Get embedding for the request
      request_embedding = get_embedding(request, TASK_TYPE, EMBEDDING_MODEL)
      if not request_embedding:
        logger.error("Could not generate embedding for the request.")
        return []

      # 2. Build the SQL query dynamically
      sql_parts = ["SELECT timestamp, severity, message, content, COSINE_DISTANCE(embedding, @request_embedding) as distance FROM KgLogEntryNode"]
      where_clauses = []
      params = {"request_embedding": request_embedding}
      param_types = {"request_embedding": spanner.param_types.Array(spanner.param_types.FLOAT64)}

      # 3. Handle severity levels
      if severity_levels:
        # The prompt says comma or space separated. Let's handle both.
        levels = [level.strip().upper() for level in severity_levels.replace(',', ' ').split() if level.strip()]
        if levels:
          where_clauses.append("severity IN UNNEST(@severities)")
          params["severities"] = levels
          param_types["severities"] = spanner.param_types.Array(spanner.param_types.STRING)

      # 4. Handle time window
      if start_time_obj:
        where_clauses.append("timestamp >= @start_time")
        params["start_time"] = start_time_obj
        param_types["start_time"] = spanner.param_types.TIMESTAMP

      if end_time_obj:
        where_clauses.append("timestamp <= @end_time")
        params["end_time"] = end_time_obj
        param_types["end_time"] = spanner.param_types.TIMESTAMP

      if where_clauses:
        sql_parts.append("WHERE " + " AND ".join(where_clauses))
      
      # 5. Add ordering and limit
      sql_parts.append("ORDER BY timestamp DESC LIMIT 30")
      
      sql = " ".join(sql_parts)
      logger.info(f"Executing log query: {sql} with params: {params.keys()}")

      results = snapshot.execute_sql(sql, params=params, param_types=param_types)
      
      # Convert to a list of dictionaries that match the LogEntry model in the dashboard app
      log_entries = []      
      # Format results into markdown text
      formatted_log_entries = []
      today_date = datetime.date.today()

      for row in results:
        timestamp, severity, message, content, distance = row # 'distance' is selected for ordering, but not used in output string
        full_log_entry = json.loads(content)
        try:
          source = full_log_entry['resource']['labels']['container_name']
        except Exception as e:
          source = '-'
        log_entries.append({
          'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
          'severity': severity,
          'message': message, 
          'source': source, 
          'details': {'distance': distance}
        })
      
        # Format timestamp based on today's date
        if timestamp.date() == today_date:
          formatted_timestamp = timestamp.strftime('%H:%M:%S')
        else:
          formatted_timestamp = timestamp.strftime('%Y-%m-%d %H:%M:%S')

        # Construct the log line in markdown code format
        formatted_log_entries.append(f"`[{formatted_timestamp}] {severity}: {message}`")

      return "\n".join(formatted_log_entries)

    except Exception as e:
      logger.error("Log Entries SQL error: {}".format(e), exc_info=True)
      return []  # Return empty list on error

def delete_logs():
  success = True
  with database.batch() as batch:
    try:
      batch.delete("KgLogEntryNode", spanner.KeySet(all_=True))
    except Exception as e:
      logger.error("Spanner Delete error: {}".format(e))
      success = False
  return success