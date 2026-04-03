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

# Trap to clean up child processes on exit
cleanup() {
  # Kill all child processes
  pkill -P $$ 2>/dev/null || true
  # Wait for zombies to be reaped
  wait
  exit 0
}
trap cleanup EXIT SIGTERM SIGINT

# cd into the directory where this script is located
this_dir=$(dirname "$0")

cd $this_dir

if [ "$side" == "destination" ]; then
  # Start iperf3 server in background
  # ** CAUTION** USing the -D (dameon) option in iperf3 
  # creates Zombie processes when we kill the iperf3 process
  # (this happens because we are in a container and there is
  # no init process to claim terminated processes)
  iperf3 -s -p $dest_port -D --logfile iperf3_server.log &
elif [ "$side" == "source" ]; then
  python3 traffic_generator.py --config config_${dest_port}.json > traffic_test.log 2>&1 &
fi

TT_PID=$!
echo $TT_PID > traffic_test.pid

# Wait for the python process and reap it when done
wait $TT_PID
echo $? > exit_code

exit 0
