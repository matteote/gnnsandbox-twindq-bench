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
import kubernetes
import json
from gitea import *
from utils.k8s import get_client

logger = logging.getLogger(__name__)

# FIXME: change this user credentials into an authentication token
# generated at Gitea server creation time
USER = os.environ['WEBAPPS_LOGIN']
PWD = os.environ['WEBAPPS_PWD']

DESIGN_REPO = 'networkdesign'
MASTER_BRANCH = 'master'
PORT = 3000


def get_gitea_url():
    """Get the Gitea URL from the K8s Gitea resource."""
    client = kubernetes.dynamic.DynamicClient(get_client())
    resource_api = client.resources.get(api_version='google.dev/v1', kind='Gitea')
    try:
        namespace = 'automation'
        name = 'gitea'
        gitea = resource_api.get(namespace=namespace, name=name)
        ip = gitea['status']['create_gitea']['external_ip_address']
        gitea_url = f"https://{ip}:{PORT}"
        return gitea_url
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning("%s in namespace %s not found", name, namespace)
        else:
            logger.error("Exception raised while getting Gitea resource: %s", name, e.status)
            logger.debug(e)


def get_git_file(repo, filename):
    """Retrieve a file from a Gitea repository by name."""
    url = get_gitea_url()
    logger.debug(f"Gitea server at: {url}")
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    repo = Repository.request(gitea, USER, repo)
    try:
        content = repo.get_git_content()
        matches = [c for c in content if c.name == filename]
        if not matches:
            logger.error(f"{filename} not found in repo")
            return None
        file = repo.get_file_content(matches[0])
        return base64.b64decode(file).decode("utf-8")
    except Exception as e:
        logger.error(f"Unexpected Gitea error: {e}")
        return None
