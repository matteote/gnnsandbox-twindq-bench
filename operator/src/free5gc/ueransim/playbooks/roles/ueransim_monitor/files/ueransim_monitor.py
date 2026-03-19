#!/usr/bin/env python3
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

import time
import os
import psutil
import json

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

criticallogger = logging.getLogger("UERANSIMHEALTH")
basiclogger = logging.getLogger("LIVENESSHEALTH")

# --- Configuration ---
POLLING_INTERVAL_IN_SECONDS = 5
PROCESS_NAME = "./nr-gnb"
HOSTNAME = os.uname().nodename
SCRIPT_NAME = os.path.basename(__file__)

def is_process_running():
    """Check if the specified process is running and log error if not."""
    while True:
        process_found = False
        try:
            # Check all running processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # Check if the process name or command line contains our target process
                    if proc.info['cmdline'] and PROCESS_NAME in ' '.join(proc.info['cmdline']):
                        process_found = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    # Process might have terminated or we don't have access
                    continue

            if not process_found:
                error_msg = f"CRITICAL: Process {PROCESS_NAME} is not running on host {HOSTNAME}"
                error_payload = {
                    'process_name': PROCESS_NAME,
                    'hostname': HOSTNAME,
                    'error': error_msg
                }
                criticallogger.error(json.dumps(error_payload))

        except Exception as e:
            error_payload = {
                'process_name': PROCESS_NAME,
                'hostname': HOSTNAME,
                'error': f"An error occurred while checking for process {PROCESS_NAME}: {e}"
            }
            basiclogger.error(json.dumps(error_payload))
            print(f"An error occurred while checking for process {PROCESS_NAME}: {e}")

        time.sleep(POLLING_INTERVAL_IN_SECONDS)

def main():
  """Main function to start the monitor."""
  # Check if the GOOGLE_APPLICATION_CREDENTIALS env variable is set and file exists
  if 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ:
    basiclogger.error("GOOGLE_APPLICATION_CREDENTIALS env variable not set")
    exit(1)
  if not os.path.exists(os.environ['GOOGLE_APPLICATION_CREDENTIALS']):
    basiclogger.error(f"GOOGLE_APPLICATION_CREDENTIALS file {os.environ['GOOGLE_APPLICATION_CREDENTIALS']} doesn't exist")
    exit(1)

  basiclogger.info(f"Starting {SCRIPT_NAME} on host {HOSTNAME}")
  is_process_running()
  print("Monitor Started")

if __name__ == "__main__":
    main()
