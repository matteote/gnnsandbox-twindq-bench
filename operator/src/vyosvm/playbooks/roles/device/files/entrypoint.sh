#!/bin/sh

# Daemon mode: the traffic-agent is PID 1.
# It listens on port 9090 for flow control requests from the operator,
# manages running traffic flows, and shuts down gracefully on SIGTERM.

exec /usr/local/bin/traffic-agent daemon --control-port 9090
