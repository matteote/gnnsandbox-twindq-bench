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
import time
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

async def get_active_users(time_window_seconds=20):
  with database.snapshot() as snapshot:
    # Calculate the timestamp threshold (current time - time_window_seconds)
    current_time_ms = int(time.time() * 1000)  # Convert to milliseconds
    threshold_time_ms = current_time_ms - (time_window_seconds * 1000)

    # Query to count unique userids in the past time_window_seconds
    sql = f"""
        SELECT COUNT(DISTINCT userid) as active_user_count
        FROM ServicePerformance 
        WHERE timestamp >= {threshold_time_ms/1000}
        AND userid IS NOT NULL
        AND userid != ''
    """
    
    logger.debug(f"Executing query: {sql}")
    results = snapshot.execute_sql(sql)
    
    # Extract the count from the result
    active_user_count = 0
    for row in results:
        active_user_count = row[0]
        break
    
    response_data = {
        'active_user_sessions': active_user_count,
        'time_window_seconds': time_window_seconds,
        'query_timestamp': current_time_ms,
        'threshold_timestamp': threshold_time_ms
    }
    
    logger.debug(response_data)
    
    return response_data


async def get_average_performance_by_service_type(time_window_seconds=20):
    """
    Calculate average performance metrics for each service type over a specified time window.
    
    Args:
        time_window_seconds (int): Time window in seconds to look back for performance data (default: 20)
    
    Returns:
        dict: Dictionary with service types as keys and their average performance metrics as values
              Format: {
                  'service_type': {
                      'avg_response_time_ms': float,
                      'total_requests': int,
                      'error_count': int,
                      'error_rate': float,
                      'unique_users': int,
                      'unique_nodes': int
                  }
              }
    """
    logger.debug(f"Calculating average performance by service type for {time_window_seconds} seconds")
    
    with database.snapshot() as snapshot:
        # Calculate the timestamp threshold (current time - time_window_seconds)
        current_time_ms = int(time.time() * 1000)  # Convert to milliseconds
        threshold_time_ms = current_time_ms - (time_window_seconds * 1000)
        
        # Query to get performance metrics grouped by service type
        sql = f"""
            SELECT 
                service_type,
                AVG(response_time_ms) as avg_response_time_ms,
                COUNT(*) as total_requests,
                COUNT(CASE WHEN error IS NOT NULL AND error != '' THEN 1 END) as error_count,
                COUNT(DISTINCT userid) as unique_users,
                COUNT(DISTINCT node) as unique_nodes
            FROM ServicePerformance 
            WHERE timestamp >= {threshold_time_ms/1000}
            AND service_type IS NOT NULL
            AND service_type != ''
            GROUP BY service_type
            ORDER BY service_type
        """
        
        logger.debug(f"Executing query: {sql}")
        results = snapshot.execute_sql(sql)
        
        # Process the results
        service_performance = {}
        for row in results:
            service_type, avg_response_time, total_requests, error_count, unique_users, unique_nodes = row
            
            # Calculate error rate
            error_rate = (error_count / total_requests) * 100 if total_requests > 0 else 0.0
            
            service_performance[service_type] = {
                'avg_response_time_ms': round(avg_response_time, 2) if avg_response_time else 0.0,
                'total_requests': total_requests,
                'error_count': error_count,
                'error_rate': round(error_rate, 2),
                'unique_users': unique_users,
                'unique_nodes': unique_nodes
            }
        
        response_data = {
            'service_performance': service_performance,
            'time_window_seconds': time_window_seconds,
            'query_timestamp': current_time_ms,
            'threshold_timestamp': threshold_time_ms,
            'total_service_types': len(service_performance)
        }
        
        logger.debug(f"Found performance data for {len(service_performance)} service types: {list(service_performance.keys())}")
        
        return response_data


async def get_user_session_details(time_window_seconds=20):
  logger.info("get active users called")

  with database.snapshot() as snapshot:
    # Calculate the timestamp threshold (current time - time_window_seconds)
    current_time_ms = int(time.time() * 1000)  # Convert to milliseconds
    threshold_time_ms = current_time_ms - (time_window_seconds * 1000)

    # Query to get detailed user session information
    sql = f"""
        SELECT 
            userid,
            COUNT(*) as request_count,
            AVG(response_time_ms) as avg_response_time,
            MIN(timestamp) as first_request,
            MAX(timestamp) as last_request,
            COUNT(CASE WHEN error IS NOT NULL AND error != '' THEN 1 END) as error_count
        FROM ServicePerformance 
        WHERE timestamp >= {threshold_time_ms/1000}
        AND userid IS NOT NULL
        AND userid != ''
        GROUP BY userid
        ORDER BY last_request DESC
    """
    
    logger.debug(f"Executing detailed query: {sql}")
    results = snapshot.execute_sql(sql)
    
    # Process the results
    user_sessions = []
    for row in results:
        userid, request_count, avg_response_time, first_request, last_request, error_count = row
        user_sessions.append({
            'userid': userid,
            'request_count': request_count,
            'avg_response_time_ms': avg_response_time,
            'first_request_timestamp': first_request,
            'last_request_timestamp': last_request,
            'error_count': error_count
        })
    
    response_data = {
        'active_user_sessions': len(user_sessions),
        'time_window_seconds': time_window_seconds,
        'query_timestamp': current_time_ms,
        'threshold_timestamp': threshold_time_ms,
        'user_sessions': user_sessions
    }
    
    return response_data

def clear_service_metrics():
  """
  Clears all records from the ServicePerformance table.
  
  Returns:
    bool: True if the operation was successful, False otherwise.
  """
  try:
    def delete_all(transaction):
      row_count = transaction.execute_update(
        "DELETE FROM ServicePerformance WHERE 1=1"
      )
      logger.info(f"Deleted {row_count} records from ServicePerformance table")
      return row_count
      
    row_count = database.run_in_transaction(delete_all)
    logger.info(f"Successfully cleared {row_count} records from ServicePerformance table")
    return True
  except Exception as e:
    logger.error(f"Failed to clear ServicePerformance table: {e}")
    return False