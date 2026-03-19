#!/bin/bash

# Test script to send a new_incident notification to the supervisor agent
# This script demonstrates how to send a notification about a new incident

# Configuration
SUPERVISOR_HOST="127.0.0.1"
SUPERVISOR_PORT="9000"
SUPERVISOR_URL="http://${SUPERVISOR_HOST}:${SUPERVISOR_PORT}"

# Check if custom host/port provided
if [ "$1" != "" ]; then
    SUPERVISOR_URL="$1"
fi

echo "Testing new_incident notification to supervisor agent at: ${SUPERVISOR_URL}"
echo "=================================================="

# Test 1: Send a new_incident notification
echo "Test 1: Sending new_incident notification..."

curl -X POST "${SUPERVISOR_URL}/pushnotification" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "name": "Fault Service",
    "state": "new_incident",
    "task_id": 'task_id': 'd4f29de200e541ea800414fab88521b4',
    'context_id': 'd4f29de200e541ea800414fab88521b4',
    "content": "New Fault Recorded",
    "input_data": {
      "error": "INC-2025-001",
      "node": "cellsite1-ueransim",
      "timestamp": "2025-01-25T11:48:00Z",
      "location": "datacenter-west-1"
    }
  }' \
  -w "\nHTTP Status: %{http_code}\nResponse Time: %{time_total}s\n" \
  -s
