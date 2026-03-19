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

#!/usr/bin/env python3
import requests
import time
import random
import os
import uuid
from urllib.parse import urlparse
import sys
import string
import json
from google.cloud import spanner

#----------------------Send logging to GCP ----------------------------
# Attach the Cloud Logging handler to the Python root logger
# by calling the setup_logging method. By doing so Cloud Logging
# will properly report the logs severity for instance. If we do it
# directly (as above) all logs are classified with ERROR severity
# (see https://cloud.google.com/logging/docs/setup/python)
import google.cloud.logging
logging_client = google.cloud.logging.Client()
import logging
logging_client.setup_logging(log_level=logging.INFO)

errorlogger = logging.getLogger("CRITICALSERVICEERROR")

def cleanup_logging():
    """Clean up logging handlers properly"""
    try:
        print("Logging cleanup completed")
    except Exception as e:
        print(f"Error during logging cleanup: {e}")

# For reference : Spanner DDL to create the ServicePerformance table
"""
  CREATE TABLE ServicePerformance (
    id STRING(36) NOT NULL,
    service_type STRING(MAX) NOT NULL,
    response_time_ms FLOAT64,
    timestamp INT64 NOT NULL,
    userid STRING(MAX),
    error STRING(MAX),
    node STRING(MAX)
  ) PRIMARY KEY (id);
"""

SQL_TEMPLATES = {
  'create_performance_metric': "INSERT ServicePerformance (id, service_type, response_time_ms, timestamp, userid, error, node)" 
                               " VALUES (@id, @service_type, @response_time_ms, @timestamp, @userid, @error, @node)",
}

REQUEST_PARAMS = {
    f'param{i}': ''.join(random.choices(
        string.ascii_letters + string.digits, k=1024
    )) for i in range(1, 6)
 }

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

database = spanner_connect()

def save_performance_metric(service_type, response_time_ms, userid, node, error=None):
  def sql_create_metric(transaction):
    timestamp = int(time.time())
    metric_id = str(uuid.uuid4())

    sql = SQL_TEMPLATES['create_performance_metric']
    return transaction.execute_update(
      sql,
      params={
        "id": metric_id,
        "service_type": service_type,
        "response_time_ms": response_time_ms,
        "timestamp": timestamp,
        "userid": userid,
        "error": error,
        "node": node
      },
      param_types={
        "id": spanner.param_types.STRING,
        "service_type": spanner.param_types.STRING,
        "response_time_ms": spanner.param_types.FLOAT64,
        "timestamp": spanner.param_types.INT64,
        "userid": spanner.param_types.STRING,
        "error": spanner.param_types.STRING,
        "node": spanner.param_types.STRING
      })
  
  try:
    database.run_in_transaction(sql_create_metric)
    if error:
        print(f"Error metric saved for service: {service_type}, user: {userid}")
    else:
        print(f"Metric saved for service: {service_type}, user: {userid}")
  except Exception as e:
    print(f"SQL error: {e}")


url = os.getenv("TEST_URL")
userid = os.getenv("USERID")
node = os.getenv("VMNAME")
timeout = int(os.getenv("REQUEST_TIMEOUT", "5"))

def send_request():
    try:
        # Generate 5 parameters, each with 1024 characters
        # This is to increase the traffic so that it is more visible
        # in the metrics.
        response = requests.get(url, params=REQUEST_PARAMS, timeout=timeout)
        response.raise_for_status()
        elapsed_ms = response.elapsed.total_seconds() * 1000
        print(f"url: {url}, status_code: {response.status_code}, latency: {elapsed_ms:.2f}ms")
        save_performance_metric("WEB", elapsed_ms, userid, node)
        print("request successful")
    except requests.exceptions.ConnectionError as e:
        error_msg = f"URL is not accessible - connection failed: {e}"
        error_payload = {
            'url': url,
            'userid': userid,
            'node': node,
            'error': error_msg
        }
        print(error_msg)
        errorlogger.error(json.dumps(error_payload))
        save_performance_metric("WEB", None, userid, node, error=error_msg)
    except requests.exceptions.Timeout as e:
        error_msg = f"URL is not accessible - request timed out after {timeout}s: {e}"
        error_payload = {
            'url': url,
            'userid': userid,
            'node': node,
            'error': error_msg
        }
        print(error_msg)
        errorlogger.error(json.dumps(error_payload))
        save_performance_metric("WEB", None, userid, node, error=error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        error_payload = {
            'url': url,
            'userid': userid,
            'node': node,
            'error': error_msg
        }
        errorlogger.error(json.dumps(error_payload))
        print(f"Error accessing {url}: {error_msg} for user {userid} at node {node}")
        save_performance_metric("WEB", None, userid, node, error=error_msg)

if __name__ == "__main__":
    try:
        if 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ:
            print("GOOGLE_APPLICATION_CREDENTIALS env variable not set")
            cleanup_logging()
            exit(1)
        if not os.path.exists(os.environ['GOOGLE_APPLICATION_CREDENTIALS']):
            print(f"GOOGLE_APPLICATION_CREDENTIALS file {os.environ['GOOGLE_APPLICATION_CREDENTIALS']} doesn't exist")
            cleanup_logging()
            exit(1)
        
        if not url:
            print("TEST_URL environment variable not set.")
            cleanup_logging()
            exit(1)

        if not userid:
            print("USERID environment variable not set.")
            cleanup_logging()
            exit(1)

        if not node:
            print("VMNAME environment variable not set.")
            cleanup_logging()
            exit(1)

        print("Starting performance monitoring service...")
        
        while True:
            for i in range(100): send_request()
            time.sleep(random.uniform(3, 5))
            
    except Exception as e:
        print(f"Unexpected error: {e}")
        cleanup_logging()
        sys.exit(1)
    finally:
        # Ensure cleanup happens even if something goes wrong
        cleanup_logging()
