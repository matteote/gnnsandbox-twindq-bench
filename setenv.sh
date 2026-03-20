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
#
# **IMPORTANT NOTE**
# Copy this file in your home environment and
# replace the value placeholders. NEVER publish this
# file.
#
# Prior to running the installation script, you must set and export the following environment variables (see ./setenv.sh):
export GOOGLE_PROJECT=<YOUR PROJECT>  # the GCP project name hosting the NW Agent demo (You MUST create it first on GCP)
export GOOGLE_USER=<GCP_USERNAME>  # the user you authenticate with on GCP. It MUST be the owner of the GOOGLE_PROJECT (e.g. john.doe@mydomain.com)
export GOOGLE_VM_USER=<GCE_VM_USERNAME>  # the default user name on GCE VMs (usually john_doe_mydomain_com but to be sure create a VM, SSH connect from the web console, type whoami', delete VM)
export GOOGLE_REGION=<YOUR_REGION>  # the GCP region to host the demo environment (e.g. europe-west1)
export GOOGLE_ZONE=<YOUR_ZONE>  # the GCP zone in the region to host the demo environment (e.g.europe-west1-c)
export WEBAPPS_LOGIN=<YOUR_WEB_LOGIN>  # the login name to access web apps like the NW Agent UI or the Gitops Web UI
export WEBAPPS_PWD=<YOUR_WEB_PWD>  # the password to access the web apps
