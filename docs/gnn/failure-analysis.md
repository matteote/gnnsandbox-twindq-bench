# L3VPN Failure Analysis — GNN Root Cause Analysis vs. Traditional Fault Management

This document analyses all fault injection scenarios for the telco-lab L3VPN network, grounded
in the actual topology and traffic descriptors, and explains how the GNN-based RCA approach
compares with traditional fault management for each failure type.

For the GNN model definition — graph schema, node features, edge types, model architecture, training pipeline, and fault classification algorithm — see [rca.md](rca.md).

---

## GNN Value Summary

| # | Fault | Impact | Traditional Approach | GNN Approach | Value |
|---|---|---|---|---|---|
| 1 | **MTU Mismatch** | Large packets silently dropped on PE1 uplink; TCP sessions stall intermittently with no visible cause | No alarm fires. Requires manual `ping -s 1400` probing from the correct vantage — typically triggered hours after customer complaint | Detects `tx_util`/`rx_util` asymmetry across the connected-interface edge in 5 min; `packet_loss_pct` on flow nodes confirms customer impact | 🟡 Passive detection with no probing |
| 2 | **Hub CE Session Down** | Total BLUE VPN blackout — all spoke-to-hub traffic fails immediately | BGP trap fires instantly but generates 3–4 cascade VRF alarms; NOC must manually determine a single root cause | Common-path analysis collapses cascade to 1 root-cause alert; `vrf_active_sessions` and `active_sessions_norm` on flow nodes quantify SLA breach | 🟢 Alarm noise reduction — N→1 |
| 3 | **RR1 Process Crash** | All VPN traffic disrupted for up to 90 seconds during route-reflector reconvergence | 4+ simultaneous BGP alarms fire; NOC investigates each PE independently, unaware of the shared RR root cause | Common-path analysis across all failing sessions identifies RR1 as the sole cause; at production scale (50 PEs) 50 alarms become 1 | 🟢 **50× alarm reduction at scale** |
| 4 | **Wrong Import RT on PE3** | Spoke2 (Liverpool) silently isolated — d2-blue has no route to hub while the hub still reaches d2-blue, creating a deceptive one-way illusion | Zero alarms. All sessions Established. Dashboard shows green. Only discovered via customer complaint and manual `show vrf` audit | `rt_import_hash` on VRF node `BLUE_SPOKE@PE3` deviates from trained baseline in 5 min — policy misconfiguration detected directly without any inference | 🔴 **Zero-alarm silent misconfiguration — only GNN** |
| 5 | **Degrading SFP** | Gradual CRC error injection simulates a failing optical SFP; retransmissions rise unnoticed for 30+ minutes before routing protocols destabilise | Traditional alarm fires at t=45–55 min once absolute error thresholds are exceeded — 15–25 minutes after SLA breach has begun | `rx_err_gradient` trend crosses anomaly threshold at t=30 min; `packet_loss_pct` on affected flow nodes rises progressively — proactive alert before any SLA impact | 🔴 **Predictive — 35 min early warning** |
| 5b | **Link Down** | Immediate traffic rerouting or blackout depending on redundancy; OSPF and BGP cascade alarms follow | Binary alarm fires in < 1 second via SNMP linkDown trap — fastest traditional detection of any fault type | Suppresses cascade alarms; `(flow, transits)` edges identify exactly which customer flows are affected and which recover via backup paths | 🟡 Blast radius analysis; RCA suppression |
| 6 | **OSPF Area Mismatch** | P2-P4 OSPF adjacency silently fails; physical link UP but L3 paths lost; PE4-originating traffic reroutes over longer detour paths | Zero alarms — SNMP sees interface UP, BGP Established. The L3 failure is completely invisible to single-layer monitoring | Cross-layer contradiction: `ospf_num_routes` drops while `tx_util≈0` on an UP interface — a signal traditional tools cannot form without correlating two separate data sources | 🔴 Cross-layer L1/L3 contradiction — only GNN |
| 7 | **Duplicate IP** | P3 claims a P1 address causing ARP cache poisoning on P4; traffic intermittently black-holes on a 20–30 minute ARP timeout cycle | Zero alarms — ARP table changes are not surfaced by SNMP. Pings frequently succeed when NOC investigates, masking the fault | `session_uptime_norm` oscillation and overlapping peer IP across two BGP session nodes infers the ARP conflict indirectly from its effect on routing stability | 🔴 ARP-layer conflict inferred from BGP signal |
| **8** | **TX Queue Starvation** ⭐ NEW | Hub PE2 uplink txqueuelen shrunk to 20 packets (a Linux kernel parameter); queue overflows constantly under load causing 30–60% throughput loss on all hub downloads | Not detectable by any tool — kernel parameter is absent from VyOS config, GitOps, and all config management systems; `ifOutDiscards` threshold never triggers | `tx_queue_len_norm=0.02` directly flags the misconfiguration; corroborated by `jitter_norm` spike on the constant 8 Mbps hub UDP monitoring flow | 🔴 **Kernel-layer misconfiguration — only GNN** |
| **9** | **OSPF Cost Inflation** ⭐ NEW | OSPF cost set to 65535 on P2-P1 link; all Brighton/Cardiff PE traffic reroutes via 3-hop detour congesting the 100 Mbps P3-PE2 link; OSPF adjacency stays Full | Not detectable — cost 65535 is a legal configuration value; all adjacencies Full, all interfaces UP, all BGP sessions Established | `tx_util≈0` on a Full-adjacency core link is uniquely contradictory; `latency_ms_norm` spikes and `egresses_at` edge shifts across multiple flow nodes confirm topology-wide rerouting | 🔴 **Legal-but-wrong config — only GNN** |
| *(10)* | *(BGP Update Storm)* | *RR1 CPU saturated by 10,000-prefix route-flap injection; BGP keepalive processing delayed; forwarding latency increases for all transit traffic* | *CPU visible in SNMP `hrProcessorLoad` but not correlated to service impact by standard NMS* | *`bgp_update_rate` spike on RR1 directly flags RESOURCE_EXHAUSTION; validates the cpu/mem RCA classifier branch currently untested by F1–F9* | *🔴 CPU/resource exhaustion — validates GNN classifier* |
| *(11)* | *(Cross-VPN Route Leak)* | *BLUE spoke routes accidentally exported into RED VPN RIB; RED VPN traffic to matching prefixes is misdirected; completely silent from control-plane perspective* | *More routes in a session is never alarmed; completely silent until customers complain about misdirected traffic* | *`rt_export_hash` deviation on VRF node + anomalous `leaks_to` cross-VPN edge directly detected; the `leaks_to` edge has never appeared in training — structurally anomalous* | *🔴 Multi-VPN policy violation — only GNN* |

**Legend**: 🔴 = GNN is the **only** detection path  ·  🟡 = GNN significantly improves on traditional  ·  🟢 = GNN reduces alarm noise on top of existing detection

*Faults 8 and 9 are new silent-performance misconfigurations introduced specifically to demonstrate GNN capabilities against faults that exist outside all configuration management systems. Faults 10 and 11 are recommended additions to fill GNN feature coverage gaps.*

---

## Network Reference

### Physical Topology

```
              RR1 (Birmingham, 10.0.0.1)
             /        \
   P1 (London)  ──  P2 (Manchester)
   10.0.0.3          10.0.0.4
      / \   \           / \
  P3    PE1  PE2     P4   PE3  PE4
(Edinburgh) (Oxford)(Cambridge)(Leeds)(Brighton)(Cardiff)
10.0.0.5  10.0.0.7 10.0.0.8 10.0.0.6 10.0.0.10 10.0.0.11
  |  \                |             |      |       |
 PE2  PE1           P3             PE3   PE4      P2
      RR2 (Bristol, 10.0.0.2) connects P3 + P4
```

**Provider Core (1 Gbps links):**

| Link | Subnet | P Router | Interface | P Router | Interface |
|---|---|---|---|---|---|
| P1 ↔ P2 | 172.16.30.0/24 | p1 (London) | eth1 | p2 (Manchester) | eth1 |
| P1 ↔ P3 | 172.16.40.0/24 | p1 (London) | eth2 | p3 (Edinburgh) | eth2 |
| P2 ↔ P4 | 172.16.60.0/24 | p2 (Manchester) | eth2 | p4 (Leeds) | eth2 |
| P3 ↔ P4 | 172.16.50.0/24 | p3 (Edinburgh) | eth3 | p4 (Leeds) | eth3 |
| P1 ↔ RR1 | 172.16.10.0/24 | p1 (London) | eth4 | rr1 (Birmingham) | eth2 |
| P2 ↔ RR1 | 172.16.20.0/24 | p2 (Manchester) | eth3 | rr1 (Birmingham) | eth1 |
| P3 ↔ RR2 | 172.16.70.0/24 | p3 (Edinburgh) | eth4 | rr2 (Bristol) | eth2 |
| P4 ↔ RR2 | 172.16.80.0/24 | p4 (Leeds) | eth1 | rr2 (Bristol) | eth1 |

**PE Uplinks (100 Mbps, all PE routers are dual-homed):**

| Link | Subnet | P Router | Interface | PE Router | Interface |
|---|---|---|---|---|---|
| P1 ↔ PE1 | 172.16.90.0/24 | p1 (London) | eth3 | pe1 (Oxford) | eth1 |
| P3 ↔ PE1 | 172.16.160.0/24 | p3 (Edinburgh) | eth5 | pe1 (Oxford) | eth4 |
| P1 ↔ PE2 | 172.16.100.0/24 | p1 (London) | eth5 | pe2 (Cambridge) | eth1 |
| P3 ↔ PE2 | 172.16.110.0/24 | p3 (Edinburgh) | eth1 | pe2 (Cambridge) | eth2 |
| P4 ↔ PE3 | 172.16.140.0/24 | p4 (Leeds) | eth4 | pe3 (Brighton) | eth1 |
| P2 ↔ PE3 | 172.16.170.0/24 | p2 (Manchester) | eth5 | pe3 (Brighton) | eth4 |
| P2 ↔ PE4 | 172.16.150.0/24 | p2 (Manchester) | eth4 | pe4 (Cardiff) | eth1 |
| P4 ↔ PE4 | 172.16.180.0/24 | p4 (Leeds) | eth5 | pe4 (Cardiff) | eth4 |

### VPN Services

**BLUE VPN — Hub-and-Spoke:**

| Device | Site | PE Router | CE Router | LAN Subnet |
|---|---|---|---|---|
| dh-blue (10.100.2.10) | Nottingham (Hub) | PE2 (Cambridge) | ce1-hub | 10.100.2.0/24 |
| d1-blue (10.100.1.10) | Sheffield (Spoke1) | PE1 (Oxford) | ce1-spoke | 10.100.1.0/24 |
| d2-blue (10.100.3.10) | Liverpool (Spoke2) | PE3 (Brighton) | ce2-spoke | 10.100.3.0/24 |
| d3-blue (10.100.4.10) | Huddersfield (Spoke3) | PE4 (Cardiff) | ce3-spoke | 10.100.4.0/24 |

**RED VPN — Any-to-Any Mesh:**

| Device | Site | PE Router | CE Router | LAN Subnet |
|---|---|---|---|---|
| d1-red (10.101.1.10) | Norwich | PE1 (Oxford) | ce1-red | 10.101.1.0/24 |
| d2-red (10.101.2.10) | Coventry | PE2 (Cambridge) | ce2-red | 10.101.2.0/24 |
| d3-red (10.101.3.10) | Plymouth | PE3 (Brighton) | ce3-red | 10.101.3.0/24 |
| d4-red (10.101.4.10) | Leicester | PE4 (Cardiff) | ce4-red | 10.101.4.0/24 |

### Route Target Policy (BLUE VPN Hub-and-Spoke)

| Role | PE Router | VRF | Export RT | Import RT |
|---|---|---|---|---|
| Spoke | PE1 (Oxford) | `BLUE_SPOKE` | `65035:1011` | `65035:1030` |
| Spoke | PE3 (Brighton) | `BLUE_SPOKE` | `65035:1011` | `65035:1030` |
| Spoke | PE4 (Cardiff) | `BLUE_SPOKE` | `65035:1011` | `65035:1030` |
| Hub | PE2 (Cambridge) | `BLUE_HUB` | `65035:1030` | `65035:1011`, `65035:1030` |

> The hub imports both spoke routes (`65035:1011`) and its own routes (`65035:1030`) to enable spoke-to-spoke traffic via the hub. Fault 4 changes PE3's import RT to `65035:9999`, breaking this policy.

### Traffic Tests

**BLUE VPN Traffic Tests (`l3vpn-blue-test.yaml`):**

| Flow ID | Protocol | Direction | Rate Profile | Peak |
|---|---|---|---|---|
| `d1-to-hub-tcp` | TCP | d1-blue → dh-blue | multi_sine (daily + weekly cycle) | ~45 Mbps at 14:00 UTC |
| `d2-to-hub-udp` | UDP | d2-blue → dh-blue | schedule (business hours) | ~45 Mbps at 13:30 UTC |
| `d1-hub-tcp-bidir` | TCP | d1-blue ↔ dh-blue | upload: multi_sine; download: multi_sine (heavier) | Upload ~45 Mbps, Download ~70 Mbps |
| `d2-hub-udp-bidir` | UDP | d2-blue ↔ dh-blue | upload: schedule; download: **8 Mbps constant** | Upload ~45 Mbps, Hub push constant |

**RED VPN Traffic Tests (`l3vpn-red-test.yaml`):**

| Flow ID | Protocol | Direction | Rate Profile | Peak |
|---|---|---|---|---|
| `d1-red-to-d2-red-tcp` | TCP | d1-red → d2-red | multi_sine | ~45 Mbps |
| `d3-red-to-d4-red-udp` | UDP | d3-red → d4-red | schedule (business hours) | ~45 Mbps |
| `d1-red-d3-red-bidir` | TCP | d1-red ↔ d3-red diagonal | upload: multi_sine; download: heavier | Upload ~45 Mbps, Download ~70 Mbps |
| `d2-red-d4-red-bidir` | UDP | d2-red ↔ d4-red diagonal | upload: schedule; download: **8 Mbps constant** | Upload ~45 Mbps, Hub push constant |

> **Key signal**: The `d2-hub-udp-bidir` and `d2-red-d4-red-bidir` hub-to-spoke reverse directions run a **constant 8 Mbps UDP stream 24/7**. Any fault that degrades this path produces a clean, unambiguous signal with no natural rate variation to hide behind — making it the most sensitive GNN training signal in the test suite.

---

> **GNN model details** — for the full graph schema (node types, features, edge types), model architecture, training pipeline, inference scoring, and fault classification algorithm, see [rca.md](rca.md).

---

## On Physical Link Down — Is GNN Appropriate?

Before the per-fault analysis, this question deserves a direct answer.

For a **clean physical link-down** (cable pull, port failure), traditional SNMP gives a faster first alarm (< 1 second vs. 5-minute GNN inference cycle). However the GNN is superior in three specific scenarios:

| Scenario | Traditional NMS | GNN |
|---|---|---|
| **First detection of binary link failure** | ✅ < 1 second (SNMP linkDown trap) | ❌ Up to 5 minutes |
| **Alarm suppression / root cause isolation** | ❌ Fires N alarms (one per BGP session, OSPF peer) | ✅ Common-path analysis → 1 root-cause alert |
| **Pre-failure degradation detection** | ❌ Alarms only after threshold crossed (45+ min) | ✅ Detects `rx_err_gradient` trend 35+ min before failure |
| **Cross-layer RCA** (L1 UP but L3 broken) | ❌ Requires manual correlation across layers | ✅ Graph structure enables simultaneous cross-layer inference |

**Recommendation**: Use traditional SNMP/syslog as the fast alarm layer for binary physical failures. Use the GNN as the root-cause isolation and proactive degradation detection layer. They are complementary, not competing.

---

## GNN Feature Coverage by Fault

The table below shows which GNN node features each fault exercises. A comprehensive test suite must exercise all features; gaps indicate RCA classifier branches that cannot be validated.

| GNN Feature | Node Type | Faults That Exercise It |
|---|---|---|
| `state` | router, interface | F3 (loopback disable), F5b (link down) |
| `cpu` | router | **F10** (BGP Update Storm) — gap in F1–F9 |
| `mem` | router | **F10** — gap in F1–F9 |
| `bgp_update_rate` | router | **F10** — primary `RESOURCE_EXHAUSTION` signal |
| `vrf_count` | router | **F11** (Cross-VPN Leak) |
| `fib_size_norm` | router | **F11** |
| `ospf_num_routes` | router | F6 (area mismatch), F3 (RR crash), F9 (cost inflation) |
| `pfx_count_norm` | router | F2 (hub CE down), F3 (RR crash), F4 (wrong RT) |
| `rx_drops` / `tx_drops` | interface | F8 (TX queue starvation) |
| `mtu_norm` | interface | F1 (MTU mismatch) |
| `tx_queue_len_norm` | interface | **F8** — direct detection (`txqueuelen 20` = 0.02 vs. healthy 1.0) |
| `rx_err_gradient` | interface | F5 (SFP degradation) |
| `tx_util` / `rx_util` | interface | F1, F8, F9 |
| `bgp_state` | bgp_session | F2 (CE session down), F3 (RR crash) |
| `pfx_count_norm` | bgp_session | F2, F3, F4, F11 (route leak) |
| `prefix_count_delta` | bgp_session | F2, F3, F4 |
| `session_uptime_norm` | bgp_session | F7 (duplicate IP, flapping) |
| `rt_import_count` | bgp_session | F11 |
| **`vrf_route_count`** | **vrf** | **F4, F2** |
| **`vrf_route_count_delta`** | **vrf** | **F4** |
| **`rt_import_hash`** | **vrf** | **F4** — direct detection of wrong RT |
| **`rt_export_hash`** | **vrf** | **F11** — direct detection of route leak |
| **`vrf_mem_bytes_norm`** | **vrf** | **F11** |
| **`vrf_active_sessions`** | **vrf** | **F2** |
| **`throughput_norm`** | **flow** | **F1, F2, F8, F9** |
| **`throughput_delta`** | **flow** | **F2, F8** |
| **`expected_rate_deviation`** | **flow** | **F8, F9** — primary signal for silent performance faults |
| **`jitter_norm`** | **flow** | **F8** — direct queue saturation signal |
| **`packet_loss_pct`** | **flow** | **F1, F5** |
| **`latency_ms_norm`** | **flow** | **F9** — direct OSPF rerouting signal |
| **`active_sessions_norm`** | **flow** | **F2, F3** |

---

## Fault-by-Fault Analysis

---

### Fault 1 — MTU Mismatch

| Property | Value |
|---|---|
| **Type** | `MTU_MISMATCH` |
| **File** | `l3vpn-hub-spoke-fault1-mtu.yaml` |
| **Target** | PE1 / eth1 (Oxford → London, 172.16.90.0/24) |
| **Alarms generated** | ❌ None |
| **Severity** | Performance degradation — intermittent |

**What happens**: PE1's uplink toward P1 (London) has its MTU reduced from 1500 to 1400 bytes. Any MPLS-encapsulated packets exceeding 1400 bytes (typical BGP UPDATE messages with many prefixes, and customer TCP segments with standard MSS) are silently dropped by the kernel. No ICMP fragmentation-needed is returned.

**Traffic flows affected**:

| Flow | Impact | Mechanism |
|---|---|---|
| `d1-to-hub-tcp` | ⚠️ Intermittent degradation | Sheffield → PE1/eth1 → P1: 45 Mbps peak with 10 TCP sessions generates frequent large segments |
| `d1-hub-tcp-bidir` (upload) | ⚠️ Degraded | Same outbound path |
| `d1-hub-tcp-bidir` (download) | ✅ Unaffected | Hub → d1 enters PE1 inbound — MTU on PE1/eth1 does not affect ingress |
| `d1-red-to-d2-red-tcp` | ⚠️ Degraded | Norwich (PE1) → PE1/eth1 → P1 → PE2 |
| `d1-red-d3-red-bidir` (d1 → d3) | ⚠️ Degraded | PE1/eth1 on outbound path |
| All d2, d3 flows | ✅ Unaffected | d2 uses PE3 (Brighton); d3 uses PE4 (Cardiff) |

**Backup path note**: PE1 also has eth4 (P3-PE1 link, 172.16.160.0/24). If OSPF ECMP distributes load across both uplinks, approximately 50% of d1 traffic may avoid the fault, making the impact intermittent and harder to reproduce on demand.

**Time-of-day dependency**: The fault is most visible at 14:00 UTC (45 Mbps peak with 10 TCP sessions generating maximum segment sizes). At 02:00 UTC (5 Mbps overnight minimum), smaller traffic volume produces fewer drop events and the anomaly score may fall below threshold.

**GNN detection**:
- `interface` node PE1/eth1: `tx_drops` increases; `tx_util` is high while P1/eth3 (connected interface) `rx_util` is lower than expected
- 2-hop message passing across the `connected_to` edge detects the tx/rx utilisation asymmetry
- Reconstruction error confined to PE1/eth1 — router and BGP session nodes healthy
- **Classifier output**: `INTERFACE → top_feature=mtu_norm → MTU_MISMATCH on PE1/eth1`

**Traditional tools**: Interface UP, OSPF Full, BGP Established. `ifOutDiscards` SNMP counter increases but absolute thresholds are typically calibrated for hardware failures, not gradual MTU-induced drops.

**GNN advantage**: Passive detection from standard telemetry counters. Traditional approach requires explicit MTU probing (`ping -s 1400`) from every link in both directions — not standard practice and must target exactly the right vantage point.

---

### Fault 2 — Hub CE Session Teardown

| Property | Value |
|---|---|
| **Type** | `BGP_SESSION_DOWN` |
| **File** | `l3vpn-hub-spoke-fault2-ce-down.yaml` |
| **Target** | PE2 / eBGP session to ce1-hub (10.80.80.0/24, VLAN 402) |
| **Alarms generated** | ✅ BGP session-down trap |
| **Severity** | Service outage — total BLUE VPN blackout |

**What happens**: The eBGP session between PE2 (Cambridge) and ce1-hub (Nottingham) is deleted. PE2 withdraws all hub customer routes. All spoke VRFs lose their imported hub routes immediately.

**Traffic flows affected**:

| Flow | Impact | Mechanism |
|---|---|---|
| `d1-to-hub-tcp` | 🔴 100% loss | dh-blue (10.100.2.10) is unreachable |
| `d2-to-hub-udp` | 🔴 100% loss | Same |
| `d1-hub-tcp-bidir` | 🔴 100% loss (both directions) | Hub cannot reach spokes either |
| `d2-hub-udp-bidir` | 🔴 100% loss | The constant 8 Mbps hub push drops to 0 — unambiguous signal |
| All RED VPN flows | ✅ Unaffected | RED VPN uses separate VRF and CE routers (ce2-red at PE2 is independent) |

**Diagnostic note**: The constant 8 Mbps hub monitoring push (`d2-hub-udp-bidir` reverse direction) is the most sensitive trigger. Any fault on the hub drops this to exactly 0, with no natural rate variation that could mask the outage.

**GNN detection**:
- `bgp_session` node PE2↔ce1-hub: `bgp_state` → 0.0, `pfx_count_norm` → 0.0, `prefix_count_delta` → large negative
- BGP session reconstruction error spikes; router and interface nodes remain healthy
- **Classifier output**: `BGP_SESSION → parent role=CE → count=1 → Local Access Failure on PE2/ce1-hub`

**Traditional tools**: ✅ BGP session-down trap fires within seconds. However in a network with many VRFs, this generates one alarm per spoke VRF that loses hub routes (~3–4 cascade alarms). The GNN suppresses these and issues a single alert pointing to the root session.

**GNN advantage**: In a production network with 50 PE routers, one hub CE session failure can cascade into 50+ downstream alarms. The GNN collapses this to 1 root-cause alert.

---

### Fault 3 — RR1 Process Crash

| Property | Value |
|---|---|
| **Type** | `PROCESS_CRASH` |
| **File** | `l3vpn-hub-spoke-fault3-rr1-crash.yaml` |
| **Target** | RR1 (Birmingham, 10.0.0.1) — bgpd kill or loopback disable |
| **Alarms generated** | ✅ 4+ BGP session-down traps simultaneously |
| **Severity** | Route reflection instability — up to 90 second disruption |

**What happens**: RR1's BGP daemon crashes or its loopback (source of iBGP router-ID) is disabled. All 4 PE-to-RR1 sessions drop simultaneously. Traffic re-reflects via RR2 (Bristol, connected to P3 and P4), but reconvergence takes up to 90 seconds.

**Traffic flows affected during reconvergence window**:

| Flow | Impact | Mechanism |
|---|---|---|
| ALL flows (both VPNs) | ⚠️ Up to 90s disruption | VPNv4 route re-reflection via RR2 required |
| `d2-hub-udp-bidir` (8 Mbps constant) | 🎯 Clearest detector | Constant baseline makes even a 5-second interruption unambiguous |
| `d1-hub-tcp-bidir` peak at 14:00 UTC | 🔴 Severe | TCP retransmissions + new session re-establishment during convergence |

After reconvergence (~90 seconds), all flows recover. The event appears as a "network hiccup" in hindsight.

**GNN detection**:
- All 4 PE-to-RR1 session embeddings spike simultaneously
- Common-path analysis: all failing sessions share RR1 as parent router
- **Classifier output**: `BGP_SESSION → parent role=RR → count=4 → RR_CRASH → root cause: rr1`
- 4 downstream PE-level alarms suppressed; 1 RR alert issued

**Traditional tools**: ✅ 4+ BGP alarms fire simultaneously. Without common-path analysis, the NOC sees "4 BGP sessions down across 4 sites" and starts individual per-site investigations. Root cause (single RR1) is not obvious from the alarm stream.

**GNN advantage**: This is the GNN's clearest win for traditional alarm reduction. At scale (50 PEs, 2 RRs), one RR crash generates 50 simultaneous BGP alarms — the GNN collapses this to 1 root-cause alert.

---

### Fault 4 — Wrong Import Route-Target on PE3

| Property | Value |
|---|---|
| **Type** | `BGP_SESSION_DOWN` / VRF misconfiguration |
| **File** | `l3vpn-hub-spoke-fault4-rt-import.yaml` |
| **Target** | PE3 (Brighton) — `BLUE_SPOKE` VRF import RT changed from `65035:1030` to `65035:9999` |
| **Alarms generated** | ❌ None |
| **Severity** | Silent isolation of Spoke2 (Liverpool) |

**What happens**: PE3's BLUE_SPOKE VRF no longer imports hub routes (RT `65035:1030` is rejected). PE3's VRF routing table empties of hub prefixes. d2-blue cannot reach dh-blue. However, ce2-spoke still exports its own prefix correctly, so the hub can still see PE3's routes and sends packets toward d2-blue that arrive but get no response.

**Traffic flows affected**:

| Flow | Impact | Mechanism |
|---|---|---|
| `d2-to-hub-udp` | 🔴 Silent failure | d2-blue (Liverpool) has no route to hub — packets blackholed at PE3 |
| `d2-hub-udp-bidir` (d2 → hub) | 🔴 Fails | Same |
| `d2-hub-udp-bidir` (hub → d2, 8 Mbps constant) | ⚠️ One-way illusion | Hub can still send to d2; d2 receives but cannot respond — one-way connectivity |
| All d1, d3 flows | ✅ Unaffected | PE1 and PE4 VRFs have correct RT |
| All RED VPN flows | ✅ Unaffected | Separate VRF — RED VPN PE3 VRF unaffected |

**The one-way deception**: Pings from dh-blue → d2-blue **succeed** (hub→spoke path works). Pings from d2-blue → dh-blue **fail**. A naive NOC test from the hub side concludes "connectivity OK." Only bidirectional end-to-end testing from d2-blue's vantage reveals the fault.

**GNN detection**:
- PE3's iBGP sessions toward RR show anomalous `pfx_count_norm` (receiving fewer VPN routes than the baseline)
- HetGNN isolates deviation to PE3's VRF config sub-embedding — the RT import hash deviates from training baseline
- D-GAT detects asymmetric reachability (CE2 advertises normally; hub→PE3 imports zero)
- **Classifier output**: `BGP_SESSION → asymmetric import pattern on PE3 → VRF_RT_MISCONFIGURATION`

**Traditional tools**: ❌ Zero alarms. All sessions Established. BGP prefix counts appear normal from the hub's perspective. RT policy is not monitored by standard NMS.

**GNN advantage**: No traditional tool passively monitors RT import policy compliance. This fault is only discoverable via explicit VRF audit scripts or customer complaint.

---

### Fault 5 — Degrading SFP on P1-P3 Link

| Property | Value |
|---|---|
| **Type** | `PACKET_CORRUPTION` (tc netem) |
| **Target** | P1 / eth2 (London → Edinburgh, 172.16.40.0/24) |
| **Alarms generated** | ⚠️ Late alarm at ~45 minutes |
| **Severity** | Gradual hardware degradation → eventual link failure |

**What happens**: Progressive packet corruption is injected on P1/eth2 simulating a failing optical SFP. Errors start at <1% and accelerate over 55 minutes until the link is unusable.

**Degradation timeline vs. traffic flows**:

| Time | Error Rate | Traffic Impact | GNN Signal |
|---|---|---|---|
| t=0–30 min | <1% | Imperceptible — TCP absorbs retransmissions | `rx_err_gradient` rising on P1/eth2; score below threshold |
| t=30 min | ~2–3% | Visible TCP retransmits at 45 Mbps peak | **GNN anomaly score crosses threshold** — alert: hardware degradation |
| t=45 min | ~8% | OSPF LSA drops; link metric instability; ECMP shifts | `rx_err_gradient` high; P1 `ospf_num_routes` fluctuates |
| t=55 min | >20% | LDP drops, routing instability, re-route | Full reconstruction error spike; traditional alarm fires |

**Traffic flows affected** (flows using P1-P3 path):

| Flow | Impact | Why |
|---|---|---|
| `d1-hub-tcp-bidir` (download, 70 Mbps peak) | 🔴 High degradation at peak | PE2→P3→PE1 path (or PE1→P1→P3→PE2) uses P1-P3 link under ECMP |
| `d1-red-d3-red-bidir` diagonal | ⚠️ Degraded | PE1↔PE3 diagonal traverses P1-P3 under some OSPF paths |
| Flows not on P1-P3 path | ✅ Initially unaffected | OSPF ECMP shifts load; other paths pick up the slack |

**GNN advantage**: At t=30 minutes, GNN raises `HARDWARE_DEGRADATION` alert on P1/eth2 with `top_feature=rx_err_gradient`. Traditional monitoring does not alarm until t=45–55 minutes. The GNN provides **15–25 minutes of early warning** — enough time for proactive SFP replacement before SLA breach.

**GNN detection**:
- `interface` node P1/eth2: `rx_err_gradient` rises steadily across successive inference cycles
- Trajectory analysis: steadily increasing anomaly score = hardware degradation (vs. single spike = transient noise)
- **Classifier output**: `INTERFACE → top_feature=rx_err_gradient → HARDWARE_DEGRADATION → root cause: P1/eth2`

**Traditional tools**: ⚠️ Late alarm. Fires only when CRC errors exceed absolute SNMP thresholds (~45+ minutes into the fault). By then, SLA is already breached for d1 and d1-red customers.

---

### Fault 5b — Link Down (Physical)

| Property | Value |
|---|---|
| **Type** | `LINK_DOWN` |
| **Target** | Any router interface — e.g., P1/eth2 (P1-P3 link) |
| **Alarms generated** | ✅ Immediate (SNMP linkDown trap < 1s) |
| **Severity** | Immediate traffic rerouting or blackout |

**GNN role for physical link-down**: Traditional SNMP wins on detection speed. The GNN's value here is:
1. **Alarm suppression**: One link down cascades into OSPF adjacency failures, BGP session drops, and prefix withdrawals. The GNN issues one root-cause alert rather than N cascade alarms.
2. **Blast radius analysis**: The graph structure shows exactly which flows are affected and which have backup paths.
3. **Disambiguation**: Distinguishes a single link failure from a router failure (which would take down all links simultaneously).

**GNN detection**:
- `interface` node: `state` → 0.0; `tx_util` → 0.0; `rx_util` → 0.0
- Connected router node: `ospf_num_routes` drops; `pfx_count_norm` changes
- **Classifier output**: `INTERFACE → top_feature=state → INTERFACE_DOWN on [router]/[interface]`

---

### Fault 6 — OSPF Area Mismatch

| Property | Value |
|---|---|
| **Type** | `OSPF_AREA_MISMATCH` |
| **Target** | P2 / eth2 (Manchester → Leeds, 172.16.60.0/24) — area set to `0.0.0.99` instead of `0.0.0.0` |
| **Alarms generated** | ❌ None (physical link remains UP) |
| **Severity** | OSPF traffic-engineering lost on P2-P4 path |

**What happens**: P2-P4 OSPF adjacency fails (will not reach Full state). Physical link remains UP and transmitting — only L3 forwarding is affected. MPLS LDP may stay up, but OSPF-computed paths through P2-P4 are lost.

**Traffic flows affected**:

| Flow | Impact | Why |
|---|---|---|
| `d3-blue-to-hub` (Huddersfield/PE4 → hub) | ⚠️ Rerouted | PE4→P4→[no P2 OSPF]→must detour via P3→P1→PE2 or P3→PE2 |
| `d3-red-to-d4-red-udp` | ⚠️ Rerouted | PE3→PE4 path normally via P2-P4 direct; now detours |
| `d2-red-d4-red-bidir` | ⚠️ Rerouted | PE2↔PE4 diagonal return path affected |
| BLUE d1 flows (PE1↔PE2) | ✅ Mostly unaffected | PE1↔PE2 via P1 or P3 direct; doesn't require P2-P4 |
| RED d1-d2 flows (PE1↔PE2) | ✅ Unaffected | PE1→P1→PE2 direct path |

**Key nuance**: PE3 (Brighton) has **two uplinks** — to P4 (Leeds) and to P2 (Manchester) directly. Even if P2-P4 adjacency fails, PE3 can still reach P2 directly via its own PE3-P2 link (172.16.170.0/24). So PE3-sourced traffic is less affected than PE4-sourced traffic (which must route to P2 via P2-P4 or P4-P3-P1-P2 detour).

**GNN detection**:
- P2 router node: `ospf_num_routes` drops (SPF tree loses P4's LSAs)
- P2/eth2 interface node: `tx_util ≈ 0.0` despite `state=UP` — link transmitting but carrying no routed traffic
- D-GAT: `ospf_peer` edge P2↔P4 shows anomalous OSPF state while physical link state=UP (cross-layer mismatch)
- **Classifier output**: `ROUTER → ospf_num_routes drop + INTERFACE tx_util=0 on UP link → OSPF_AREA_MISMATCH on P2/eth2`

**Traditional tools**: ❌ No alarm. Physical link UP. BGP Established. Unless OSPF adjacency state is explicitly monitored (non-default in most NMS tools), this is invisible. Models an operator copy-paste error during a maintenance window.

**GNN advantage**: The cross-layer contradiction — L1 says "UP", L3 says "no routes via this link" — is precisely what the GNN's multi-layer graph captures and traditional single-layer monitoring cannot.

---

### Fault 7 — Duplicate IP Address

| Property | Value |
|---|---|
| **Type** | `DUPLICATE_IP` |
| **Target** | P3 / eth3 (Edinburgh → Leeds, 172.16.50.0/24) — duplicate of P1/eth1 address `172.16.30.1` |
| **Alarms generated** | ❌ None |
| **Severity** | Intermittent black-holing on P3-P4 transit paths |

**What happens**: P3 (Edinburgh) claims the IP address `172.16.30.1/24` which legitimately belongs to P1 (London) on the P1-P2 link. P3 sends gratuitous ARPs claiming this address on the P3-P4 segment. P4 (Leeds) receives conflicting ARP entries. Any traffic P4 forwards toward `172.16.30.1` may be misdirected to P3 instead of P1, depending on which ARP entry is cached at any given moment.

**Traffic flows affected** (intermittently):

| Flow | Impact | Why |
|---|---|---|
| `d3-blue-to-hub` | ⚠️ Intermittent | PE4→P4→P2→P1→PE2 path: P4 may misdirect 172.16.30.1-bound traffic |
| `d3-red-to-d4-red-udp` | ⚠️ Intermittent | PE3→P4→PE4: P4 ARP confusion |
| `d2-red-d4-red-bidir` | ⚠️ Intermittent | PE4 as endpoint; P4 as transit |
| d1/d2 BLUE and d1-d2 RED flows | ✅ Mostly unaffected | Primarily use P1-P2 direct paths, not P3-P4 segment |

**The intermittent pattern**: Impact is worst immediately after P3 sends gratuitous ARPs. It fades as ARP entries timeout (20–30 minutes), then returns on the next gratuitous ARP cycle. This creates a cycling availability pattern — extremely hard to diagnose because pings often succeed when the NOC investigates.

**GNN detection**:
- `session_uptime_norm` on BGP sessions belonging to P4-adjacent routers oscillates — sessions reset as routing breaks intermittently
- `prefix_count_delta` oscillates as routes withdraw and return
- Two routers show anomalous sessions with overlapping peer IP space
- **Classifier output**: `BGP_SESSION → session_uptime_norm low on 2+ routers → IP_OVERLAP → rogue session on P3`

**Traditional tools**: ❌ Zero alarms. ARP table changes are not surfaced by standard NMS. The intermittent pattern means test pings frequently succeed during investigation.

**GNN advantage**: The GNN infers ARP-level conflicts from their effect on BGP session stability — an indirect signal that no SNMP MIB directly exposes.

---

### Fault 8 — TX Queue Starvation ⭐ NEW — Silent Performance

| Property | Value |
|---|---|
| **Type** | `TXQUEUE_STARVATION` (new type) |
| **Target** | PE2 / eth2 (Cambridge → Edinburgh, 172.16.110.0/24) — `txqueuelen` 1000 → 20 |
| **Alarms generated** | ❌ None |
| **Severity** | 30–60% hub throughput loss on Edinburgh-bound paths |

**What happens**: The Linux transmit queue length on PE2's P3-facing uplink is reduced from the default 1000 packets to 20 via `ip link set eth2 txqueuelen 20`. This is a kernel parameter — it does not appear in VyOS running-config, is not stored in the VyOS commit history, and is not captured by any configuration management or GitOps system. Under the aggregate hub traffic load, the 20-packet queue fills and overflows thousands of times per second.

**Why PE2/eth2 is the highest-impact target**: PE2 (Cambridge) is the hub router for BLUE VPN. PE2/eth2 is the P3 (Edinburgh) uplink — traffic from PE2 toward Edinburgh-routed paths (PE1 via P3, and ECMP-distributed hub downloads) exits here.

**Traffic flows affected**:

| Flow | Time of Day | Impact | Mechanism |
|---|---|---|---|
| `d1-hub-tcp-bidir` (hub → d1 downloads) | 14:00 UTC peak (70 Mbps) | 🔴 Severe — 30–60% throughput loss | Hub distributes downloads via PE2/eth2 if ECMP routes Oxford-bound traffic via P3 |
| `d2-hub-udp-bidir` (hub → d2, **8 Mbps constant**) | 24/7 | ⚠️ Detectable — constant baseline makes deviations unambiguous | Any congestion on PE2/eth2 immediately shows in `jitter_ms` and `packet_loss_pct` of the constant stream |
| `d2-to-hub-udp` return path | Business hours | ⚠️ Congested | Hub response traffic exits PE2/eth2 toward Liverpool-bound path |
| BLUE d3 hub traffic | Business hours | ⚠️ Congested | If routed via P3 from PE2 |

**Time-of-day pattern**: Queue starvation is worst during 09:00–17:00 UTC when multiple flows compete for PE2/eth2. At 02:00 UTC (5 Mbps overnight minimum), the 20-packet queue overflows less frequently — anomaly score may fall below threshold briefly, making the fault appear "intermittent" to traditional monitoring even if it were detectable.

**GNN detection**:
- `interface` node PE2/eth2: `tx_drops` → high; `tx_util` → high; while P3/eth1 (connected interface) `rx_util` → lower than expected
- 2-hop message passing detects the transmit/receive utilisation asymmetry across the `connected_to` edge
- Reconstruction error concentrated on PE2/eth2 `interface` node
- **Classifier output**: `INTERFACE → top_feature=tx_drops → TX_QUEUE_STARVATION on PE2/eth2`

**Traditional tools**: ❌ `txqueuelen` is a kernel parameter, not in any config management system. `ifOutDiscards` SNMP counter increases, but absolute thresholds are calibrated for hardware failure rates, not kernel config drift. During business-hours peak, drops are significant but masked by traffic variability.

**GNN advantage**: This fault exists entirely outside every configuration management system. No config audit, no VyOS diff, no compliance tool can detect it. The GNN detects it purely from telemetry behaviour — the only observable signal this fault produces.

---

### Fault 9 — OSPF Interface Cost Inflation ⭐ NEW — Silent Performance

| Property | Value |
|---|---|
| **Type** | `OSPF_COST_INFLATION` (new type) |
| **Target** | P2 / eth1 (Manchester → London, 172.16.30.2) — OSPF cost 1 → 65535 |
| **Alarms generated** | ❌ None |
| **Severity** | 3-hop detour for Brighton/Cardiff traffic; 100 Mbps P3-PE2 link becomes congestion point |

**What happens**: The OSPF interface cost on P2's eth1 (P2-to-P1 link, Manchester side) is changed from 1 to 65535. The OSPF adjacency on P2/eth1 remains **Full** — cost changes never break adjacencies. OSPF SPF recalculates: the P2→P1 direction is now prohibitively expensive. Traffic that previously used the direct P2→P1 path reroutes via P2→P4→P3→P1.

**OSPF path change** (P2→P1 direction only — asymmetric):

| Before | After |
|---|---|
| PE3 → P2 → P1 → PE2 (2 hops, cost 3) | PE3 → P4 → P3 → PE2 (3 hops, but cost 3) |
| PE4 → P2 → P1 → PE2 (2 hops, cost 3) | PE4 → P4 → P3 → PE2 (3 hops, cost 3) |
| P2 → P1 (direct, cost 1) | P2 → P4 → P3 → P1 (cost 65537 vs. detour) |

**Critical bottleneck created**: The P3-PE2 link (172.16.110.0/24) is a **100 Mbps link**. With P2/eth1 cost inflated, traffic from PE3 (Brighton) and PE4 (Cardiff) returning toward PE2 (Cambridge) reroutes through P4→P3→PE2, converging on this single 100 Mbps link. BLUE VPN hub downloads (up to 70 Mbps) and RED VPN diagonal traffic (up to 45 Mbps) may both reroute through it simultaneously.

**Traffic flows affected**:

| Flow | Before Fault | After Fault | Impact |
|---|---|---|---|
| `d2-to-hub-udp` (Liverpool/PE3 → Cambridge/PE2) | PE3→P2→P1→PE2 (direct) | PE3→P4→P3→PE2 (via 100 Mbps P3-PE2) | ⚠️ Higher latency; potential congestion |
| `d2-hub-udp-bidir` reverse (hub → d2, constant) | PE2→P1→P2→PE3 | PE2→P3→P4→PE3 (P3-PE2 now bidirectional) | 🔴 Congestion + jitter on constant 8 Mbps — clean signal |
| `d3-blue-to-hub` (Huddersfield/PE4 → hub) | PE4→P2→P1→PE2 | PE4→P4→P3→PE2 | ⚠️ Increased latency |
| `d2-red-d4-red-bidir` return | PE2→P1→P2→PE4 | PE2→P3→P4→PE4 | ⚠️ Return path via congested P3-PE2 link |
| `d3-red-to-d4-red-udp` | PE3→P2→P4→PE4 | PE3→P4→PE4 (shorter!) | ✅ Slightly improved |
| BLUE d1 flows (PE1↔PE2) | PE1→P1→PE2 (direct) | ✅ Unchanged | Doesn't traverse P2 Manchester |
| RED d1-d2 flows (PE1↔PE2) | PE1→P1→PE2 | ✅ Unchanged | Doesn't traverse P2 Manchester |

**Cross-VPN impact**: OSPF is shared infrastructure across both VPNs. One P-router misconfiguration degrades both BLUE and RED VPN simultaneously.

**GNN detection (multi-node)**:
- **P2/eth1 interface**: `tx_util ≈ 0.0` despite `state=UP` and OSPF Full adjacency — never seen during training. **Highest individual anomaly score.**
- **P3/eth1 (P3-PE2 link) interface**: `tx_util` and `rx_util` elevated above training baseline — congestion
- **P4/eth3 (P3-P4 link) interface**: elevated from rerouted traffic
- **Router nodes P3, P4**: elevated `cpu` from increased forwarding load
- **Classifier output**: `INTERFACE → state=UP + OSPF_Full + tx_util≈0 on P2/eth1 → OSPF_COST_INFLATION`

**Traditional tools**: ❌ OSPF cost 65535 is a legal configured value. All adjacencies Full. All interfaces UP. BGP Established. Identifying root cause requires manually running `show ip ospf interface` on every P router — impractical without the GNN's graph-wide visibility.

**GNN advantage**: The GNN knows that a Full OSPF adjacency on a core link should carry traffic proportional to its position in the topology. P2/eth1 with zero traffic but Full adjacency is a contradiction the model has never seen — the reconstruction error reveals the anomaly without any operator intervention.

---

### Fault 10 — BGP Update Storm / CPU Resource Exhaustion ⭐ RECOMMENDED GAP-FILLER

| Property | Value |
|---|---|
| **Type** | Candidate new type: `BGP_UPDATE_STORM` |
| **Target** | RR1 (Birmingham) — controlled route-flap injection from a test peer |
| **Alarms generated** | ❌ None (BGP sessions remain Established) |
| **Severity** | CPU saturation on RR1 and P-routers; potential keepalive delays |

**Why this fault is needed**: The GNN RCA classifier contains the branch `CASE dominant layer = 'router': IF top_feature IN ('cpu', 'mem') → RESOURCE_EXHAUSTION` — but **no current fault exercises the `cpu` and `mem` features**. Without a training/validation scenario for this path, the classifier branch is implemented but unvalidated.

**What happens**: A controlled BGP peer connected to RR1 advertises ~10,000 prefixes with rapid withdraw/re-advertise cycles. RR1's bgpd process CPU spikes to 70–80%. P-routers receiving frequent route updates via RR1 spend excessive time on SPF recalculation, competing with packet forwarding interrupt handling.

**Traffic impact**: At 70–80% CPU on RR1, BGP keepalive processing is delayed. If hold-timers are tight (default 90s/30s keepalive), sessions may reset. At moderate levels, forwarding latency increases for all transit traffic.

**GNN detection**:
- RR1 `router` node: `cpu` feature spikes to values never seen in training
- `ospf_num_routes` fluctuates as updates compete with forwarding
- **Classifier output**: `ROUTER → top_feature=cpu → RESOURCE_EXHAUSTION on rr1`

**Traditional tools**: ❌ BGP sessions remain Established. CPU is visible in SNMP `hrProcessorLoad` MIB, but most NMS tools don't correlate CPU spikes with specific service impact without additional rules.

---

### Fault 11 — Cross-VPN Route Leak ⭐ RECOMMENDED GAP-FILLER

| Property | Value |
|---|---|
| **Type** | Candidate new type: `VRF_RT_EXPORT_LEAK` |
| **Target** | PE1 (Oxford) — BLUE_SPOKE VRF accidentally exports with RED VPN RT |
| **Alarms generated** | ❌ None |
| **Severity** | RED VPN traffic attracted to BLUE spoke prefixes; potential misdirection |

**Why this fault is needed**: The RED and BLUE VPNs coexist with separate VRFs and RT policies, but **no fault exercises the interaction between them**. A realistic operator error during a maintenance window (adding wrong RT to an export policy) causes routes from one VPN to appear in the other's RIB.

**What happens**: PE1's BLUE_SPOKE VRF export RT is extended to include the RED VPN export RT. PE1 now advertises BLUE spoke routes (10.100.1.0/24) into the RED VPN's RIB. RED VPN devices that have routes overlapping with BLUE spoke prefixes may have their traffic misdirected.

**Traffic impact**: RED VPN d1-red (Norwich/PE1) traffic to any destination matching the leaked prefix gets misdirected. Variable packet loss depending on prefix overlap.

**GNN detection**:
- `pfx_count_norm` on RED VPN BGP sessions toward PE1 increases anomalously (more prefixes than the model learned for RED sessions)
- HetGNN isolates the deviation to PE1's bgp_session nodes with anomalous prefix counts
- **Classifier output**: `BGP_SESSION → pfx_count_norm above baseline on RED sessions at PE1 → VRF_RT_EXPORT_LEAK on PE1`

**Traditional tools**: ❌ More routes in a BGP session is typically not alarmed. The leak is completely silent until customer complaint.

---

## Consolidated Comparison Table

| # | Fault | Target | Traffic Flows Affected | Alarms | Traditional Detection | GNN Detection Time | GNN Advantage |
|---|---|---|---|---|---|---|---|
| 1 | MTU Mismatch | PE1/eth1 (Oxford→London) | d1 BLUE TCP, d1-red TCP | ❌ | Manual MTU probe — hours | 5 min (tx/rx asymmetry) | Passive; no probing needed |
| 2 | Hub CE Session Down | PE2 ↔ ce1-hub (Nottingham) | ALL BLUE VPN — total blackout | ✅ BGP trap | Seconds | 5 min + cascade suppression | Reduces ~4 VRF alarms → 1 |
| 3 | RR1 Crash | RR1 (Birmingham) | ALL traffic — 90s disruption | ✅ 4+ BGP traps | Seconds (noisy) | 5 min, 1 RR alert | **4× alarm reduction; 50× at scale** |
| 4 | Wrong RT on PE3 | PE3 BLUE_SPOKE VRF | d2 BLUE UDP — silent isolation | ❌ | Customer complaint | 5 min (pfx_count drop on PE3) | Zero-alarm scenario: only GNN |
| 5 | Degrading SFP | P1/eth2 (London↔Edinburgh) | d1 TCP, d1-red diagonal | ⚠️ 45+ min late | 45+ min after degradation | **t=30 min — 35 min earlier** | Proactive vs. reactive |
| 5b | Link Down | Any P/PE interface | Depends on redundancy | ✅ Immediate | < 1 second | 5 min | RCA suppression + blast radius |
| 6 | OSPF Area Mismatch | P2/eth2 (Manchester↔Leeds) | PE4 flows; some PE3 flows | ❌ | Never | 5 min (cross-layer L1/L3) | Cross-layer correlation |
| 7 | Duplicate IP | P3/eth3 (Edinburgh→Leeds) | Intermittent P4-transit flows | ❌ | Never | 5 min (session_uptime oscillation) | Infers ARP conflict from BGP |
| **8** | **TX Queue Starvation** | **PE2/eth2 (Cambridge→Edinburgh)** | **Hub downloads; d2 constant UDP** | **❌** | **Never (not in config)** | **5 min (tx_drops + asymmetry)** | **Kernel param: only GNN can detect** |
| **9** | **OSPF Cost Inflation** | **P2/eth1 (Manchester→London)** | **d2/d3 BLUE; d3/d4 RED diagonal** | **❌** | **Never (legal config value)** | **5 min (tx_util≈0 on Full link)** | **Legal-but-wrong: only GNN** |
| *(10)* | *(BGP Update Storm)* | *(RR1 Birmingham)* | *(All flows — CPU pressure)* | *❌* | *CPU MIB only — no service correlation* | *5 min (cpu feature)* | *Validates RESOURCE_EXHAUSTION path* |
| *(11)* | *(Cross-VPN Route Leak)* | *(PE1 Oxford — BLUE/RED VRF)* | *(RED VPN d1-red misdirection)* | *❌* | *Never (more routes ≠ alarm)* | *5 min (pfx_count anomaly)* | *Multi-VPN interaction: only GNN* |

*Faults 10 and 11 are recommended additions to fill identified GNN feature coverage gaps.*

---

## Why GNN Is Superior: Three Core Principles

### 1. Behavioural Baseline, Not State Transitions

Traditional tools alarm when something **transitions** from a known-good state to a known-bad state (UP → DOWN, Established → Idle). This is powerful for binary failures but completely blind to misconfigurations that remain in valid states.

The GNN alarms when **behaviour deviates** from the learned normal pattern, regardless of protocol state. Faults 1, 4, 6, 7, 8, and 9 all maintain perfectly healthy protocol states — the GNN is the only system that can detect them.

### 2. Graph-Wide Simultaneous Inference

When Fault 9 (OSPF cost inflation on P2/eth1) causes P2/eth1 `tx_util ≈ 0`, congestion on P3/eth1, elevated CPU on P3 and P4, and latency increases on PE3/PE4 — traditional monitoring tools see each of these as independent signals. A NOC engineer must manually correlate "P2 link traffic dropped" + "P3 link congestion" + "PE3 customers slow" and connect them to a single cause.

The GNN sees all nodes simultaneously in one inference pass. The `ospf_peer` edge structure tells the model exactly which router-router pairs are OSPF adjacencies — allowing it to reason that P2/eth1 with zero traffic but a Full adjacency is anomalous relative to the graph topology, and that the congestion pattern on the detour path is the expected consequence.

### 3. Temporal Feature Engineering — Trends, Not Snapshots

`rx_err_gradient` (hardware degradation) and `session_uptime_norm` (IP conflict flapping) encode *rates of change* and *normalised ages* — features that capture trends over time, not just the current state. Traditional monitoring systems compare each poll to a static threshold. The GNN's gradient features enable it to detect "this metric is getting worse at an accelerating rate" and alert proactively 35+ minutes before the failure becomes severe.

The **constant 8 Mbps monitoring push** in both BLUE (`d2-hub-udp-bidir` reverse) and RED (`d2-red-d4-red-bidir` reverse) traffic tests is specifically designed to serve as a high-sensitivity GNN training signal. Any deviation from a perfectly flat 8 Mbps UDP stream is immediately anomalous — providing a clean, unambiguous signal for any fault that degrades the hub-to-spoke or diagonal-mesh return paths.
