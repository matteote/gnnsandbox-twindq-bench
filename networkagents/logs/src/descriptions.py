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

description="""
A logs agent. The logs agent can query the network agent logs. 

The logs agent can help the user issue a query against the logs table 
and return relevant log entries, sorted by descending timestamp and
possibly limited to a certain time window and severity levels
"""

tags=['chat']

examples=[
    "Are there any errors in the logs related to cellsite1?",
    "Any errors occured in the logs over the past 10 minutes?",
    "Any errors occured in the logs related to dnn between 10am and noon today?"
]
