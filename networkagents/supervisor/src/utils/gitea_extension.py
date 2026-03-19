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
import kubernetes
import json
from gitea import *
from agent_library import get_client

logger = logging.getLogger(__name__)

PORT=3000

# Get the Gitea URL from the K8s resource description
def get_gitea_url():
  client = kubernetes.dynamic.DynamicClient(get_client())
  resource_api = client.resources.get(api_version='google.dev/v1', kind='Gitea')
  try:
    namespace='automation'
    name='gitea'
    gitea = resource_api.get(namespace=namespace, name=name)
    ip  = gitea['status']['create_gitea']['external_ip_address']
    gitea_url = f"https://{ip}:{PORT}"
    return gitea_url
  except kubernetes.client.rest.ApiException as e:
    if e.status == 404:
      logger.warning("%s in namespace %s not found", name, namespace)
    else:
      logger.error("Exception raised while getting Gitea resource: %s", name, e.status)
      logger.debug(e)

# Add the missing delete_file function to the Gitea SDK 
# Repository class and a few other functions that must be 
# modified too 
def requests_delete_with_data(self, endpoint: str, data: dict = None):
    if not data:
      data = {}
    request = self.requests.delete(
       self.local_get_url(endpoint), headers=self.headers, data=json.dumps(data)
    )
    if request.status_code not in [200, 204]:
        message = f"Received status code: {request.status_code} ({request.url})"
        self.logger.error(message)
        raise Exception(message)
  
# redefine Gitea.__get_url locally because it is not accessible 
# from outside of the Gitea class
def local_get_url(self, endpoint):
        url = self.url + "/api/v1" + endpoint
        self.logger.debug("Url: %s" % url)
        return url

Gitea.local_get_url = local_get_url
Gitea.requests_delete_with_data = requests_delete_with_data

def delete_file(
    self, file_path: str, file_sha: str, data: dict = None
):
  """https://try.gitea.io/api/swagger#/repository/repoCreateFile"""
  if not data:
      data = {}
  url = f"/repos/{self.owner.username}/{self.name}/contents/{file_path}"
  data.update({"sha": file_sha})
  return self.gitea.requests_delete_with_data(url, data)

Repository.delete_file = delete_file
