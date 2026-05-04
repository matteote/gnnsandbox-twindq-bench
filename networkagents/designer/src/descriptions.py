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
Network Designer Agent

Processes requests to change L3VPN network into VyOS and Device k8s descriptors and deploys the 
intended changes.
"""

tags=['chat', 'network-design']

examples=[
    "Create a L3VPN with 4 P routers, 2 route reflectors, 8 PE routers, each PE with 2 CE routers",
]
