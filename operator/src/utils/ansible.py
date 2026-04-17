
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

import asyncio
import logging
logger = logging.getLogger(__name__)

########################################################
# Concurrency management in ansible
# Two separate semaphores so monitoring playbooks can
# never starve create/delete/configure operations.
#   - Operational: create / delete / configure  (7 slots)
#   - Monitor:     status checks                (3 slots)
# Total = 10, same as before — but now guaranteed split.
########################################################
ANSIBLE_OPERATIONAL_LIMIT = 7
ANSIBLE_MONITOR_LIMIT     = 3

ansible_operational_semaphore = asyncio.Semaphore(ANSIBLE_OPERATIONAL_LIMIT)
ansible_monitor_semaphore     = asyncio.Semaphore(ANSIBLE_MONITOR_LIMIT)

# Keep the old name as an alias so callers that haven't been
# updated yet continue to get the operational semaphore.
ansible_semaphore = ansible_operational_semaphore
MAX_WORKERS = 10

def get_ansible_semaphore():
    """Backward-compat: returns the operational semaphore."""
    return ansible_operational_semaphore

def get_ansible_operational_semaphore():
    """For create / delete / configure playbooks (7 slots)."""
    return ansible_operational_semaphore

def get_ansible_monitor_semaphore():
    """For status / monitoring playbooks (3 slots)."""
    return ansible_monitor_semaphore

########################################################
# Ansible event handler
########################################################
# Ansible doc (see https://ansible.readthedocs.io/projects/runner/en/latest/intro/#artifactevents)
def event_handler(data):
    if "stdout" in data:
        logger.info(data['stdout'])
    elif "stderr" in data:
        logger.info(data['stderr'])
    else:
        # log the whole data structure so that
        # we see how to handle it in the log_capture
        # cloud function
        logger.info(data)