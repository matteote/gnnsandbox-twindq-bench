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

if [ "$side" == "source" ]; then
  iperf_cmd="iperf3.*-c.*$dest_ip.*-p $dest_port"
elif [ "$side" == "destination" ]; then
  iperf_cmd="iperf3.*-s.*-p $dest_port"
fi

# Check if iperf3 process is running
if pgrep -f "$iperf_cmd" > /dev/null 2>&1; then
  echo 'running'
else
  echo 'not_running'
fi

exit 0