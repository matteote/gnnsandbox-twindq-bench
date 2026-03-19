#!/usr/bin/bash
#
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

### N3iwf IPSec tunnel configuration

# As per https://github.com/free5gc/free5gc/issues/45#issuecomment-634012712
#     IKEBindAddress: dynamically computed by $(hostname -i | awk '{print $1}')
#     IPSecInterfaceMark: 5
#     IPSecInterfaceAddress: 10.0.0.1
#     IPSec subnet CIDR: /24
#     N3IWF tunnel interface: ipsec0
#     

ip link add name ipsec0 type vti local $(hostname -i | awk '{print $1}') remote 0.0.0.0 key 5
ip addr add 10.0.0.1/24 dev ipsec0
ip link set dev ipsec0 up
