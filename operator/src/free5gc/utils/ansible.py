
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
logger = logging.getLogger(__name__)

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