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


def extract_devices_from_spec(spec: dict) -> list:
    """
    Extract device name and management IP from a VyOSInfrastructure spec.

    Returns a list of dicts::

        [{"name": "dev1", "mgmt_ip": "192.168.122.50"}, ...]

    Only devices with both ``name`` and ``mgmt_ip`` set are included.
    These are the devices running traffic-agent in daemon mode; their
    Prometheus metrics endpoint is reachable on port 9091 via the
    management network from the monitoring VM.
    """
    devices = []
    for device in spec.get("devices", []):
        name = device.get("name", "")
        mgmt_ip = device.get("mgmt_ip", "")
        if name and mgmt_ip:
            devices.append({"name": name, "mgmt_ip": mgmt_ip})
    logger.info("Extracted %d device(s) for Ops Agent scrape config: %s",
                len(devices), [d["name"] for d in devices])
    return devices


async def update_opsagent_config(namespace: str, routers: list, devices: list = None) -> None:
    """
    Re-render the Ops Agent config on the VyOSVM and restart the agent.

    Args:
        namespace: Kubernetes namespace (used to look up the VyOSVM IP).
        routers:   List of {"name": str, "ip_address": str} dicts for VyOS
                   router scrape targets (node_exporter :9100, frr_exporter :9101).
                   Pass an empty list to remove all router targets.
        devices:   List of {"name": str, "mgmt_ip": str} dicts for traffic-agent
                   Prometheus targets (port 9091).  Defaults to [] when omitted.
    """
    if devices is None:
        devices = []

    logger.info(
        "Updating Ops Agent config on %s with %d router(s) and %d device(s): routers=%s devices=%s",
        VYOSVM_NAME,
        len(routers),
        len(devices),
        [r["name"] for r in routers],
        [d["name"] for d in devices],
    )

    # Resolve the VyOSVM management IP (used for the Ansible SSH connection)
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    extravars = {
        "routers": routers,
        "devices": devices,
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

    ANSIBLE_TIMEOUT_SECONDS = 60

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
            thread.join(timeout=ANSIBLE_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.error(
                    "Ansible playbook 'update-opsagent.yaml' did not finish within "
                    f"{ANSIBLE_TIMEOUT_SECONDS}s — treating as failure"
                )
                return None
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
