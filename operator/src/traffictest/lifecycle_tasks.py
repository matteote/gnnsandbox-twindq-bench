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

"""TrafficTest lifecycle tasks — daemon HTTP implementation.

Replaces the previous Ansible-based implementation.  All traffic
control happens via direct HTTP calls to the traffic-agent daemon
running inside each Device container on port 9090, proxied through
the network VM using ``utils.ssh_http.agent_request``.

Flow create sequence (per source device):
  1. POST /v1/flows  →  destination agent  (role=destination, port=N)
  2. sleep 1 s       →  give destination time to bind
  3. POST /v1/flows  →  source agent        (role=source, ...)

  When bidirectional: true, two additional steps per source:
  4. POST /v1/flows  →  source agent        (role=destination, reverse_port)
  5. sleep 1 s       →  give reverse listener time to bind
  6. POST /v1/flows  →  destination agent   (role=source, reverse_port, dest_ip=source_ip)

  Forward flow ID:  {name}_{source_device}
  Reverse flow ID:  {name}_{source_device}_rev

Flow delete:
  DELETE /v1/flows/{flow_id}      →  source agent
  DELETE /v1/flows/{flow_id}      →  destination agent
  DELETE /v1/flows/{flow_id}_rev  →  source agent        (bidirectional only)
  DELETE /v1/flows/{flow_id}_rev  →  destination agent   (bidirectional only)

Flow metrics are collected by the Ops Agent Prometheus scrape on each device
and written to Cloud Monitoring — the operator does not poll agent status.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from utils.ssh_http import agent_request

logger = logging.getLogger(__name__)


#########################################################################
# Bandwidth helper
#########################################################################

def _parse_bandwidth_bps(bandwidth_str: str) -> int:
    """Convert a bandwidth string (e.g. "10Mbps", "1Gbps") to bps integer."""
    if not bandwidth_str:
        return 10_000_000  # fallback: 10 Mbps
    s = str(bandwidth_str).lower().strip()
    for suffix, mult in [
        ("gbps", 1_000_000_000),
        ("mbps", 1_000_000),
        ("kbps", 1_000),
        ("bps",  1),
    ]:
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mult)
            except ValueError:
                pass
    try:
        return int(s)
    except ValueError:
        logger.warning("Could not parse bandwidth '%s', defaulting to 10 Mbps", bandwidth_str)
        return 10_000_000


#########################################################################
# TrafficTest Management
#########################################################################

async def create_traffic_test(
    name: str,
    networkvm_ip_address: str,
    spec: Dict[str, Any],
    devices_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Start traffic flows by posting to the agent daemon on each device.

    For each source device:
      - POST destination agent /v1/flows  (role=destination)
      - POST source agent      /v1/flows  (role=source)

    When bidirectional: true, also starts a reverse flow per source:
      - POST source agent      /v1/flows  (role=destination, reverse port)
      - POST destination agent /v1/flows  (role=source,      reverse port)
    """
    destination_device = spec.get("destination_device")
    destination_ip = devices_info[destination_device]["ip"]
    dest_mgmt_ip = devices_info[destination_device].get("mgmt_ip")
    source_devices = spec.get("source_devices", [])

    bidirectional         = bool(spec.get("bidirectional", False))
    reverse_bandwidth_bps = _parse_bandwidth_bps(
        spec.get("reverse_bandwidth") or spec.get("bandwidth", "10Mbps")
    )
    reverse_pattern_type   = spec.get("reverse_pattern_type", "constant")
    reverse_pattern_config = spec.get("reverse_pattern_config", {})

    logger.info(
        "Creating TrafficTest %s: %d source(s) -> %s (%s)%s",
        name, len(source_devices), destination_device, destination_ip,
        " [bidirectional]" if bidirectional else "",
    )

    protocol         = spec.get("protocol", "TCP")
    duration         = spec.get("duration", 60)
    bandwidth_bps    = _parse_bandwidth_bps(spec.get("bandwidth", "10Mbps"))
    pattern_type     = spec.get("pattern_type", "constant")
    pattern_config   = spec.get("pattern_config", {})
    concurrent_users = spec.get("concurrent_users", 1)
    start_time       = datetime.now(timezone.utc).isoformat()

    failed_sources = []

    for source_device in source_devices:
        port           = devices_info[source_device]["port"]
        source_mgmt_ip = devices_info[source_device].get("mgmt_ip")
        source_ip      = devices_info[source_device].get("ip")
        flow_id        = f"{name}_{source_device}"

        logger.info(
            "Flow %s: %s (%s) -> %s:%d", flow_id, source_device, source_mgmt_ip, destination_device, port
        )

        # ── Step 1: start destination listener ───────────────────────────────
        if dest_mgmt_ip:
            dest_body: Dict[str, Any] = {
                "flow_id":      flow_id,
                "role":         "destination",
                "port":         port,
                "protocol":     protocol,
                "duration_sec": duration,
            }
            dest_result = await agent_request(
                networkvm_ip_address, dest_mgmt_ip, "POST", "/v1/flows", dest_body
            )
            if not dest_result["success"]:
                if dest_result.get("http_code") == 409:
                    # Flow is already running on the destination (e.g. from a
                    # previous attempt that was retried).  Treat as success so
                    # the create is idempotent — the listener is ready.
                    logger.info(
                        "Destination flow %s already running on %s — continuing (idempotent)",
                        flow_id, destination_device,
                    )
                else:
                    logger.error(
                        "Failed to start destination flow %s on %s: %s",
                        flow_id, destination_device, dest_result.get("error"),
                    )
                    failed_sources.append(source_device)
                    continue
            # Give the destination a moment to bind the port
            await asyncio.sleep(1)
        else:
            logger.warning(
                "No mgmt_ip for destination %s — skipping destination setup for flow %s",
                destination_device, flow_id,
            )

        # ── Step 2: start source sender ───────────────────────────────────────
        if source_mgmt_ip:
            src_body: Dict[str, Any] = {
                "flow_id":             flow_id,
                "role":                "source",
                "destination_ip":      destination_ip,
                "port":                port,
                "protocol":            protocol,
                "duration_sec":        duration,
                "pattern_type":        pattern_type,
                "bandwidth_bps":       bandwidth_bps,
                "concurrent_sessions": concurrent_users,
            }
            if pattern_config:
                src_body["pattern_config"] = pattern_config

            src_result = await agent_request(
                networkvm_ip_address, source_mgmt_ip, "POST", "/v1/flows", src_body
            )
            if not src_result["success"]:
                if src_result.get("http_code") == 409:
                    # Flow is already running on the source (e.g. from a
                    # previous attempt that was retried).  Treat as success so
                    # the create is idempotent — the sender is already active.
                    logger.info(
                        "Source flow %s already running on %s — continuing (idempotent)",
                        flow_id, source_device,
                    )
                else:
                    logger.error(
                        "Failed to start source flow %s on %s: %s",
                        flow_id, source_device, src_result.get("error"),
                    )
                    failed_sources.append(source_device)
        else:
            logger.error("No mgmt_ip for source device %s — cannot start flow %s", source_device, flow_id)
            failed_sources.append(source_device)

        # ── Steps 3 & 4: reverse flow (destination → source) ─────────────────
        if bidirectional and source_device not in failed_sources:
            rev_port    = devices_info[source_device].get("reverse_port")
            flow_id_rev = f"{name}_{source_device}_rev"

            if not rev_port:
                logger.warning(
                    "No reverse_port for source %s — skipping reverse flow %s",
                    source_device, flow_id_rev,
                )
            elif not source_ip:
                logger.warning(
                    "No data IP for source %s — skipping reverse flow %s",
                    source_device, flow_id_rev,
                )
            else:
                logger.info(
                    "Reverse flow %s: %s -> %s:%d",
                    flow_id_rev, destination_device, source_device, rev_port,
                )

                # Step 3: source device listens as destination for reverse flow
                if source_mgmt_ip:
                    rev_dest_body: Dict[str, Any] = {
                        "flow_id":      flow_id_rev,
                        "role":         "destination",
                        "port":         rev_port,
                        "protocol":     protocol,
                        "duration_sec": duration,
                    }
                    rev_dest_result = await agent_request(
                        networkvm_ip_address, source_mgmt_ip, "POST", "/v1/flows", rev_dest_body
                    )
                    if not rev_dest_result["success"]:
                        logger.error(
                            "Failed to start reverse destination flow %s on %s: %s",
                            flow_id_rev, source_device, rev_dest_result.get("error"),
                        )
                    else:
                        # Give the listener a moment to bind
                        await asyncio.sleep(1)

                        # Step 4: destination device sends as source for reverse flow
                        if dest_mgmt_ip:
                            rev_src_body: Dict[str, Any] = {
                                "flow_id":             flow_id_rev,
                                "role":                "source",
                                "destination_ip":      source_ip,
                                "port":                rev_port,
                                "protocol":            protocol,
                                "duration_sec":        duration,
                                "pattern_type":        reverse_pattern_type,
                                "bandwidth_bps":       reverse_bandwidth_bps,
                                "concurrent_sessions": concurrent_users,
                            }
                            if reverse_pattern_config:
                                rev_src_body["pattern_config"] = reverse_pattern_config

                            rev_src_result = await agent_request(
                                networkvm_ip_address, dest_mgmt_ip, "POST", "/v1/flows", rev_src_body
                            )
                            if not rev_src_result["success"]:
                                logger.error(
                                    "Failed to start reverse source flow %s on %s: %s",
                                    flow_id_rev, destination_device, rev_src_result.get("error"),
                                )
                        else:
                            logger.error(
                                "No mgmt_ip for destination %s — cannot start reverse source flow %s",
                                destination_device, flow_id_rev,
                            )

    if failed_sources:
        if len(failed_sources) == len(source_devices):
            return {
                "success": False,
                "error": f"All source devices failed to start: {', '.join(failed_sources)}",
            }
        return {
            "success": True,  # partial success
            "start_time": start_time,
            "message": (
                f"Partial start: {len(source_devices) - len(failed_sources)}/{len(source_devices)} "
                f"sources OK. Failed: {', '.join(failed_sources)}"
            ),
        }

    return {
        "success": True,
        "start_time": start_time,
        "message": f"Traffic test started with all {len(source_devices)} source(s)"
                   + (" (bidirectional)" if bidirectional else ""),
    }


async def delete_traffic_test(
    name: str,
    networkvm_ip_address: str,
    spec: Dict[str, Any],
    devices_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Stop all flows for a TrafficTest by calling DELETE on each agent.

    For bidirectional tests the reverse flows ({name}_{source}_rev) are also
    deleted — source agent gets the destination-role DELETE, destination agent
    gets the source-role DELETE.
    """
    source_devices     = spec.get("source_devices", [])
    destination_device = spec.get("destination_device")
    dest_mgmt_ip       = devices_info.get(destination_device, {}).get("mgmt_ip")
    bidirectional      = bool(spec.get("bidirectional", False))
    end_time           = datetime.now(timezone.utc).isoformat()
    failed_deletes     = []

    for source_device in source_devices:
        flow_id        = f"{name}_{source_device}"
        source_mgmt_ip = devices_info.get(source_device, {}).get("mgmt_ip")

        # ── Forward flow: source → destination ───────────────────────────────

        # Stop source flow
        if source_mgmt_ip:
            result = await agent_request(
                networkvm_ip_address, source_mgmt_ip, "DELETE", f"/v1/flows/{flow_id}"
            )
            if not result["success"]:
                logger.warning(
                    "Failed to stop source flow %s on %s: %s",
                    flow_id, source_device, result.get("error"),
                )
                failed_deletes.append(f"{source_device}(src)")
        else:
            logger.warning("No mgmt_ip for source %s — skipping source flow deletion", source_device)

        # Stop destination flow (404 is fine — flow may have expired naturally)
        if dest_mgmt_ip:
            result = await agent_request(
                networkvm_ip_address, dest_mgmt_ip, "DELETE", f"/v1/flows/{flow_id}"
            )
            if not result["success"] and result.get("http_code", 0) != 404:
                logger.warning(
                    "Failed to stop destination flow %s: %s", flow_id, result.get("error")
                )
                failed_deletes.append(f"{destination_device}(dst)")

        # ── Reverse flow: destination → source (bidirectional only) ──────────
        if bidirectional:
            flow_id_rev = f"{name}_{source_device}_rev"

            # source device was the reverse destination — stop its listener
            if source_mgmt_ip:
                result = await agent_request(
                    networkvm_ip_address, source_mgmt_ip, "DELETE", f"/v1/flows/{flow_id_rev}"
                )
                if not result["success"] and result.get("http_code", 0) != 404:
                    logger.warning(
                        "Failed to stop reverse destination flow %s on %s: %s",
                        flow_id_rev, source_device, result.get("error"),
                    )
                    failed_deletes.append(f"{source_device}(rev-dst)")

            # destination device was the reverse source — stop its sender
            if dest_mgmt_ip:
                result = await agent_request(
                    networkvm_ip_address, dest_mgmt_ip, "DELETE", f"/v1/flows/{flow_id_rev}"
                )
                if not result["success"] and result.get("http_code", 0) != 404:
                    logger.warning(
                        "Failed to stop reverse source flow %s on %s: %s",
                        flow_id_rev, destination_device, result.get("error"),
                    )
                    failed_deletes.append(f"{destination_device}(rev-src)")

    return {
        "success": len(failed_deletes) == 0,
        "error": f"Failed to delete: {', '.join(failed_deletes)}" if failed_deletes else None,
        "end_time": end_time,
    }


