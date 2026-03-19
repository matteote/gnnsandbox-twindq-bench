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
A test agent that helps users manage traffic tests across their network infrastructure. 

The test agent can help with:
- Creating traffic tests between network devices with various traffic patterns
- Configuring test parameters (bandwidth, duration, protocol, concurrent users)
- Monitoring running traffic tests and viewing their status
- Deleting traffic tests
- Multi-source load testing scenarios

Supports multiple traffic patterns:
- Constant: Steady bandwidth throughout test
- Periodic: Sine/square/sawtooth wave patterns for business hours simulation
- Burst: High-traffic bursts with idle periods
- Poisson: Realistic user arrival patterns
"""

tags=['chat', 'network-testing', 'performance-testing']

examples=[
    "Create a multi-source load test from dev1 and dev2 to devhub using TCP with 5Mbps bandwidth for 2 hours",
    "Run a constant traffic test from dev1 to devhub at 10Mbps for 5 minutes",
    "Set up a burst pattern test with 100Mbps bursts every 5 minutes between spoke devices and hub-router",
    "Create a periodic sine wave traffic test from customer-a-device to customer-b-device simulating daily usage patterns",
    "Show me all running traffic tests",
    "Delete the traffic test named multi-source-load-test",
]
