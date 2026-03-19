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

import asyncio
import os
import logging
import ansible_runner
import utils.constants as constants
from utils.compute import get_ip
import kopf

logger = logging.getLogger(__name__)

VYOSVM_NAME = "networkvm"


def extract_routers_from_spec(spec: dict) -> list:
    """
    Extract the management-network router list from a VyOSInfrastructure spec.

    Returns a list of dicts::

        [{"name": "r1", "ip_address": "192.168.122.11"}, ...]

    Only routers connected to a network with ``network_type: management`` are
    included, because those IPs are reachable from the monitoring VM where the
    Ops Agent runs.
    """
    routers = []
    for network in spec.get("networks", []):
        if network.get("network_type") == "management":
            for cr in network.get("connected_routers", []):
                routers.append({
                    "name": cr["router_name"],
                    "ip_address": cr["ip_address"],
                })
    logger.info(routers)
    return routers


async def update_opsagent_config(namespace: str, routers: list) -> None:
    """
    Re-render the Ops Agent config on the VyOSVM and restart the agent.

    Args:
        namespace: Kubernetes namespace (used to look up the VyOSVM IP).
        routers:   List of {"name": str, "ip_address": str} dicts.
                   Pass an empty list to remove all Prometheus scrape targets
                   (e.g. on VyOSInfrastructure deletion).
    """
    logger.info(
        "Updating Ops Agent config on %s with %d router(s): %s",
        VYOSVM_NAME,
        len(routers),
        [r["name"] for r in routers],
    )

    # Resolve the VyOSVM management IP (used for the Ansible SSH connection)
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    extravars = {
        "routers": routers,
    }

    hosts = {
        "hosts": {
            "monitor": {
                "ansible_host": ip_address,
                "ansible_user": os.getenv("GOOGLE_VM_USER"),
                "ansible_connection": "ssh",
                "ansible_ssh_private_key_file": constants.basedir + "/google-compute",
                "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
            }
        }
    }

    logger.info("Running Ops Agent update playbook against %s", ip_address)
    logger.debug("Ansible extra vars: %s", extravars)

    def run_ansible():
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=constants.basedir + "/vyosinfrastructure/playbooks",
                inventory={"all": hosts},
                playbook="update-opsagent.yaml",
                extravars=extravars,
                quiet=False,
                verbosity=1,
            )
            thread.join()
            return runner
        except Exception as e:
            logger.error("Ansible execution failed: %s", e)
            return None

    # Get the semaphore used to throttle concurrent Ansible runs
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()

    async with semaphore:
        loop = asyncio.get_event_loop()
        runner = await loop.run_in_executor(None, run_ansible)

    if runner is None:
        logger.error("Failed to execute Ops Agent update playbook")
        return

    if runner.status == "successful":
        logger.info(
            "Ops Agent config updated successfully with %d router(s)", len(routers)
        )
    else:
        logger.error(
            "Ansible failed while updating Ops Agent config (status=%s)", runner.status
        )
