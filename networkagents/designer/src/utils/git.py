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

import os
import base64
import logging
import logging
import kubernetes
import json
from gitea import *
from utils.k8s import get_client

logger = logging.getLogger(__name__)

from gitea import *

# FIXME: change this user credentials into an authentication token
# generated at Gitea server creation time 
USER = os.environ['WEBAPPS_LOGIN']
PWD = os.environ['WEBAPPS_PWD']

DESIGN_REPO = 'networkdesign'
SERVICE_REPO = 'network'
MASTER_BRANCH = 'master'
UPDATE_BRANCH = 'update'
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

# get file from repo
def get_git_file(repo, filename):
  url = get_gitea_url()
  logger.debug(f"Gitea server at: {url}")
  gitea = Gitea(get_gitea_url(), auth=(USER, PWD), verify=False)
  repo = Repository.request(gitea, USER, repo)
  try:
    content = repo.get_git_content()
    readmes = [c for c in content if c.name == filename]
    file = repo.get_file_content(readmes[0])
    return base64.b64decode(file).decode("utf-8")
  except Exception as e:
    logger.error(f"Unexpected Gitea error: {e}")
    return None

# Commit service at file_path with commit message
# return True if commit went well, None otherwise
def commit_git_file(file_path, message, content):
  url = get_gitea_url()
  logger.info(f"Gitea server at: {url} {USER} {PWD}")
  gitea = Gitea(url, auth=(USER, PWD), verify=False)
  repo = Repository.request(gitea, USER, SERVICE_REPO)

  # Base64 encode file content before calling create / change file
  logger.debug(f"Committing file_path: {file_path}")
  b64_content = base64.b64encode(bytes(content, "utf-8"))
  try:
    if (file := git_file_exists(repo, file_path)) is not None:
      logger.debug(f"Changing git file path: {file.path}, sha: {file.sha}")
      ret = repo.change_file(file.path, file.sha, content=b64_content.decode("ascii"), 
                        data={'branch': UPDATE_BRANCH, 'message': message+' - Updated'})
    else:
      logger.debug(f"Creating git file path: {file_path}")
      ret = repo.create_file(file_path, content=b64_content.decode("ascii"), 
                      data={'branch': UPDATE_BRANCH, 'message': message})
    logger.debug(ret)
    return True
  except Exception as e:
    logger.error(f"Unexpected Gitea error: {e}")
    return None
  
# Delete service at file_path with commit message
# return True if  deletion went well, None otherwise
def delete_git_file(file_path, message):
  url = get_gitea_url()
  logger.debug(f"Gitea server at: {url}")
  gitea = Gitea(get_gitea_url(), auth=(USER, PWD), verify=False)
  repo = Repository.request(gitea, USER, SERVICE_REPO)
  try:
    if file := git_file_exists(repo, file_path):
      repo.delete_file(file_path, file.sha, data={'branch': MASTER_BRANCH, 'message': message})
      return True
  except Exception as e:
    logger.error(f"Unexpected Gitea error: {e}")
    return None

def git_file_exists(repo, file_path):
  contents = repo.get_git_content()
  files = [c for c in contents if c.name == file_path]
  if len(files) > 0:
    return files[0]
  else:
    return None