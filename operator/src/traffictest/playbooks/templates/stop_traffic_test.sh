#!/bin/bash

# $1 is the destination IP
# $2 is the destination port
# $3 is the side (source or destination)
dest_ip=$1
dest_port=$2
side=$3

if [ -z "$dest_ip" ] || ! [[ "$dest_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'ERROR: Invalid destination IP: not a number or empty'
  exit 1
fi

if [ -z "$dest_port" ] || ! [[ "$dest_port" =~ ^[0-9]+$ ]]; then
  echo 'ERROR: Invalid port: not a number or empty'
  exit 1
fi

if [ -z "$side" ] || [[ "$side" != "source" && "$side" != "destination" ]]; then
  echo 'ERROR: Invalid side: must be source or destination'
  exit 1
fi

# Find and kill the wrapper script and all its children
WRAPPER_PID=$(pgrep -f start_traffic_test.sh | head -1)
if [ -n "$WRAPPER_PID" ]; then
  # Kill the entire process tree
  pkill -P $WRAPPER_PID 2>/dev/null || true
  kill $WRAPPER_PID 2>/dev/null || true
  sleep 1
  kill -TERM $WRAPPER_PID 2>/dev/null || true
fi

if [ "$side" == "source" ]; then
  iperf_cmd="iperf3.*-c.*$dest_ip.*-p $dest_port"
  # Kill python process
  pkill -TERM -f "traffic_generator.py.*--config.*config_{$dest_port}.json" 2>/dev/null || true
fi

if [ "$side" == "destination" ]; then
  iperf_cmd="iperf3.*-s.*-p $dest_port"
fi

# Kill any orphaned iperf3 clients
pkill -TERM -f "$iperf_cmd" 2>/dev/null || true

sleep 2

# Kill any remaining zombie iperf3 processes
# First, find zombie processes
ZOMBIES=$(ps aux | grep iperf3 | grep defunct | awk "{print \$2}")

# If zombies exist, force their parent to reap them
if [ -n "$ZOMBIES" ]; then
  for pid in $ZOMBIES; do
    # Get parent PID
    PPID=$(ps -o ppid= -p $pid 2>/dev/null | tr -d " ")
    if [ -n "$PPID" ] && [ "$PPID" != "1" ]; then
      # Signal parent to reap child
      kill -CHLD $PPID 2>/dev/null || true
    fi
  done
  sleep 1
fi

# Final cleanup: kill any remaining process references
pkill -TERM -f "$iperf_cmd" 2>/dev/null || true

exit 0