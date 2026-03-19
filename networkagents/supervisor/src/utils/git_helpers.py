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
logger = logging.getLogger(__name__)

from gitea import *
from utils.gitea_extension import *

# FIXME: change this user credentials into an authentication token
# generated at Gitea server creation time 
USER = os.environ['WEBAPPS_LOGIN']
PWD = os.environ['WEBAPPS_PWD']
SERVICE_REPO = 'core'
MASTER_BRANCH = 'master'

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
                        data={'branch': MASTER_BRANCH, 'message': message+' - Updated'})
    else:
      logger.debug(f"Creating git file path: {file_path}")
      ret = repo.create_file(file_path, content=b64_content.decode("ascii"), 
                      data={'branch': MASTER_BRANCH, 'message': message})
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