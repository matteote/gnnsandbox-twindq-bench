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

"""
Git / Gitea utilities for the Supervisor agent.

This module consolidates all Gitea interaction into one place:

  1. SDK patches  – extends the upstream ``gitea`` library with the missing
                    ``delete_file`` method and a helper to build API URLs.
  2. URL discovery – ``get_gitea_url()`` reads the Gitea IP from the K8s
                    ``Gitea`` custom resource.
  3. Core helpers  – ``get_repo()``, ``git_file_exists()``,
                    ``commit_git_file()``, ``delete_git_file()``, and
                    ``delete_all_git_files()`` provide the low-level CRUD
                    operations used by the rest of the agent.
  4. Deployment    – ``deploy_descriptor_to_git()`` converts a full network
                    descriptor to YAML and commits each CRD to the Gitea
                    ``network`` repository so that Config Sync can reconcile
                    the cluster state.

Deploy order: infrastructure → underlay → vpns → traffic_tests

Teardown deletes *every* file currently in the ``network`` repo, ensuring a
true clean slate regardless of leftover files from previous deployments.
"""

import base64
import json
import logging
import os

import kubernetes
import yaml
from agent_library import get_client
from gitea import Gitea, Repository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# FIXME: change these user credentials into an authentication token
# generated at Gitea server creation time
USER          = os.environ['WEBAPPS_LOGIN']
PWD           = os.environ['WEBAPPS_PWD']
SERVICE_REPO  = 'core'
NETWORK_REPO  = 'network'
MASTER_BRANCH = 'master'

_GITEA_PORT = 3000

# ---------------------------------------------------------------------------
# SDK patches  (previously in gitea_extension.py)
# ---------------------------------------------------------------------------

def _local_get_url(self, endpoint: str) -> str:
    """Build a full Gitea API URL. Reimplemented here because the upstream
    ``Gitea.__get_url`` is private and therefore inaccessible externally."""
    url = self.url + "/api/v1" + endpoint
    self.logger.debug("Url: %s", url)
    return url


def _requests_delete_with_data(self, endpoint: str, data: dict = None):
    """Issue an HTTP DELETE request with a JSON body (not natively supported
    by the upstream Gitea client)."""
    if not data:
        data = {}
    request = self.requests.delete(
        self.local_get_url(endpoint), headers=self.headers, data=json.dumps(data)
    )
    if request.status_code not in [200, 204]:
        message = f"Received status code: {request.status_code} ({request.url})"
        self.logger.error(message)
        raise Exception(message)


def _delete_file(self, file_path: str, file_sha: str, data: dict = None):
    """Delete a file from the repository via the Gitea contents API."""
    if not data:
        data = {}
    url = f"/repos/{self.owner.username}/{self.name}/contents/{file_path}"
    data.update({"sha": file_sha})
    return self.gitea.requests_delete_with_data(url, data)


# Patch the upstream classes once at import time.
Gitea.local_get_url              = _local_get_url
Gitea.requests_delete_with_data  = _requests_delete_with_data
Repository.delete_file           = _delete_file

# ---------------------------------------------------------------------------
# URL discovery  (previously in gitea_extension.py)
# ---------------------------------------------------------------------------

def get_gitea_url() -> str | None:
    """Return the Gitea base URL by reading the ``Gitea`` K8s custom resource."""
    client       = kubernetes.dynamic.DynamicClient(get_client())
    resource_api = client.resources.get(api_version='google.dev/v1', kind='Gitea')
    try:
        namespace = 'automation'
        name      = 'gitea'
        gitea     = resource_api.get(namespace=namespace, name=name)
        ip        = gitea['status']['create_gitea']['external_ip_address']
        return f"https://{ip}:{_GITEA_PORT}"
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning("%s in namespace %s not found", name, namespace)
        else:
            logger.error("Exception raised while getting Gitea resource: %s %s", name, e.status)
            logger.debug(e)
    return None

# ---------------------------------------------------------------------------
# Core git helpers  (previously in git_helpers.py)
# ---------------------------------------------------------------------------

def get_repo(repo_name: str = SERVICE_REPO) -> Repository:
    """Open and return the named Gitea repository."""
    url   = get_gitea_url()
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    return Repository.request(gitea, USER, repo_name)


def git_file_exists(repo: Repository, file_path: str):
    """Return the content object for *file_path* in *repo*, or ``None``."""
    try:
        contents = repo.get_git_content()
    except Exception:
        # Empty repo causes the Gitea API to return HTTP 409, which the
        # gitea client raises as an exception rather than returning None.
        # Treat any error here as "file not found".
        return None
    if not contents:
        return None
    files = [c for c in contents if c.name == file_path]
    return files[0] if files else None


def commit_git_file(
    file_path: str,
    message: str,
    content: str,
    repo_name: str = SERVICE_REPO,
) -> bool | None:
    """Create or update *file_path* in *repo_name* with *content*.

    Returns ``True`` on success, ``None`` on failure.
    """
    url   = get_gitea_url()
    logger.info("Gitea server at: %s %s %s", url, USER, PWD)
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    repo  = Repository.request(gitea, USER, repo_name)

    logger.debug("Committing file_path: %s to repo: %s", file_path, repo_name)
    b64_content = base64.b64encode(bytes(content, "utf-8"))
    try:
        if (file := git_file_exists(repo, file_path)) is not None:
            logger.debug("Changing git file path: %s, sha: %s", file.path, file.sha)
            repo.change_file(
                file.path, file.sha,
                content=b64_content.decode("ascii"),
                data={'branch': MASTER_BRANCH, 'message': message + ' - Updated'},
            )
        else:
            logger.debug("Creating git file path: %s", file_path)
            repo.create_file(
                file_path,
                content=b64_content.decode("ascii"),
                data={'branch': MASTER_BRANCH, 'message': message},
            )
        return True
    except Exception as e:
        logger.error("Unexpected Gitea error: %s", e)
        return None


def delete_git_file(
    file_path: str,
    message: str,
    repo_name: str = SERVICE_REPO,
) -> bool | None:
    """Delete *file_path* from *repo_name*.

    Returns ``True`` on success, ``None`` on failure.
    """
    url   = get_gitea_url()
    logger.debug("Gitea server at: %s", url)
    gitea = Gitea(url, auth=(USER, PWD), verify=False)
    repo  = Repository.request(gitea, USER, repo_name)
    try:
        if file := git_file_exists(repo, file_path):
            repo.delete_file(
                file_path, file.sha,
                data={'branch': MASTER_BRANCH, 'message': message},
            )
            return True
    except Exception as e:
        logger.error("Unexpected Gitea error: %s", e)
    return None


def delete_all_git_files(
    message: str,
    repo_name: str = NETWORK_REPO,
) -> tuple[list[str], list[str]]:
    """Delete every file currently in *repo_name*.

    Useful for a full clean-slate teardown where the set of files in the repo
    may differ from what is described in a stored network descriptor (e.g.
    files left over from a previous deployment).

    Args:
        message:   Git commit message applied to each deletion.
        repo_name: Target Gitea repository (default: ``NETWORK_REPO``).

    Returns:
        Tuple of ``(deleted_filenames, failed_filenames)``.
    """
    deleted: list[str] = []
    failed:  list[str] = []

    try:
        url   = get_gitea_url()
        gitea = Gitea(url, auth=(USER, PWD), verify=False)
        repo  = Repository.request(gitea, USER, repo_name)
        try:
            contents = repo.get_git_content()
        except Exception:
            # Empty repo causes the Gitea API to throw instead of returning None.
            logger.debug(
                "delete_all_git_files: repo '%s' is already empty "
                "(or get_git_content raised)", repo_name
            )
            return deleted, failed
        if not contents:
            logger.debug("delete_all_git_files: repo '%s' is already empty", repo_name)
            return deleted, failed
        for file in contents:
            try:
                repo.delete_file(
                    file.name, file.sha,
                    data={'branch': MASTER_BRANCH, 'message': message},
                )
                deleted.append(file.name)
                logger.debug("Deleted '%s' from repo '%s'", file.name, repo_name)
            except Exception as e:
                failed.append(file.name)
                logger.error("Failed to delete '%s' from repo '%s': %s", file.name, repo_name, e)
    except Exception as e:
        logger.info("Failed to list/delete files from repo '%s': %s", repo_name, e)
        failed.append(str(e))

    return deleted, failed

# ---------------------------------------------------------------------------
# Deployment helpers  (previously in git_deploy.py)
# ---------------------------------------------------------------------------

def _cr_filename(body: dict) -> str:
    """Return ``{metadata.name}.yaml`` for a CRD body dict."""
    name = body.get("metadata", {}).get("name", "unknown")
    return f"{name}.yaml"


def _ordered_resources(descriptor: dict) -> list[tuple[dict, str]]:
    """Return an ordered list of ``(crd_body, label)`` tuples from a descriptor.

    Deploy order: infrastructure → underlay → vpns → traffic_tests
    """
    resources: list[tuple[dict, str]] = []
    if descriptor.get("infrastructure"):
        resources.append((descriptor["infrastructure"], "infrastructure"))
    if descriptor.get("underlay"):
        resources.append((descriptor["underlay"], "underlay"))
    for vpn in descriptor.get("vpns", []):
        resources.append((vpn, "vpn"))
    for test in descriptor.get("traffic_tests", []):
        resources.append((test, "traffic_test"))
    return resources


async def deploy_descriptor_to_git(
    descriptor: dict,
    *,
    sio=None,
    network_id: str = "",
) -> dict:
    """Convert every CRD in *descriptor* to YAML and commit it to the Gitea
    ``network`` repository.

    Config Sync will reconcile the cluster state to match the committed files.

    Emits ``deploy_progress`` Socket.IO events for each committed file so
    the dashboard can show per-resource progress.

    Args:
        descriptor: Full network descriptor dict (infrastructure, underlay,
                    vpns, traffic_tests).
        sio:        Optional Socket.IO server instance for progress events.
        network_id: Forwarded to every progress event.

    Returns:
        dict with keys ``committed`` (list[str]) and ``failed`` (list[str]).
    """
    committed: list[str] = []
    failed:    list[str] = []

    async def _emit(stage: str, **extra) -> None:
        if sio is None:
            return
        try:
            await sio.emit("deploy_progress", {"network_id": network_id, "stage": stage, **extra})
        except Exception as e:
            logger.warning("Failed to emit deploy_progress: %s", e)

    for body, label in _ordered_resources(descriptor):
        kind      = body.get("kind", "Unknown")
        name      = body.get("metadata", {}).get("name", "unknown")
        file_path = _cr_filename(body)
        message   = f"Deploy {kind}/{name} for network {network_id}"
        cr_yaml   = yaml.dump(body, indent=2, allow_unicode=True, default_flow_style=False)

        logger.debug("Committing %s/%s as '%s' to '%s'", kind, name, file_path, NETWORK_REPO)
        ok = commit_git_file(file_path, message, cr_yaml, repo_name=NETWORK_REPO)
        if ok:
            committed.append(f"{kind}/{name}")
            await _emit("git_committed", kind=kind, name=name, file=file_path)
        else:
            failed.append(f"{kind}/{name}")
            await _emit("git_commit_failed", kind=kind, name=name, file=file_path)

    logger.debug(
        "Git deploy of '%s' complete — committed: %d, failed: %d | %s | %s",
        network_id, len(committed), len(failed), committed, failed,
    )
    return {"committed": committed, "failed": failed}
