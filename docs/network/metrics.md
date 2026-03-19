# VyOS Metrics: Collection and Processing

This document summarises the raw metrics scraped from VyOS routers and the subset that the metrics collector selects and writes to Cloud Spanner.

---

## 1. Raw Metrics Available from VyOS

VyOS exposes two Prometheus-format metric endpoints on each router node. Both are collected by the Google Cloud Ops Agent and stored in Cloud Monitoring under the `prometheus.googleapis.com/` namespace.

### 1.1 System Metrics — `node_exporter` (port 9100)

Provided by the Linux **node_exporter** running inside the VyOS VM. These cover broad operating system health.

| Category | Metric Prefix | Description |
|---|---|---|
| **CPU** | `node_cpu_seconds_total` | Time spent in each CPU mode (idle, user, system, iowait, steal, softirq, nice, irq) per core |
| | `node_cpu_guest_seconds_total` | Time CPUs spent in guest VMs |
| | `node_load1/5/15` | 1-, 5- and 15-minute load averages |
| | `node_schedstat_running_seconds_total` | Seconds CPU was running processes |
| | `node_schedstat_waiting_seconds_total` | Seconds processes were waiting for CPU |
| | `node_schedstat_timeslices_total` | CPU timeslices executed |
| | `node_context_switches_total` | Total context switches |
| **Memory** | `node_memory_MemTotal_bytes` | Total physical memory |
| | `node_memory_MemFree_bytes` | Free physical memory |
| | `node_memory_MemAvailable_bytes` | Available memory (including reclaimable) |
| | `node_memory_Cached_bytes` | Page cache size |
| | `node_memory_Buffers_bytes` | Buffer cache size |
| | `node_memory_SwapTotal_bytes` / `SwapFree_bytes` | Swap size and usage |
| | `node_memory_Active_bytes` / `Inactive_bytes` | Active and inactive memory |
| | `node_memory_Slab_bytes` | Kernel slab allocator usage |
| | `node_memory_Committed_AS_bytes` | Total committed virtual address space |
| **Network Interfaces** | `node_network_up` | Interface operational state (1 = up) |
| | `node_network_carrier` | Physical carrier state |
| | `node_network_carrier_changes_total` | Total carrier state changes (flaps) |
| | `node_network_carrier_up_changes_total` | Carrier up transitions |
| | `node_network_carrier_down_changes_total` | Carrier down transitions |
| | `node_network_receive_bytes_total` | Total bytes received per interface |
| | `node_network_transmit_bytes_total` | Total bytes transmitted per interface |
| | `node_network_receive_packets_total` | Total packets received |
| | `node_network_transmit_packets_total` | Total packets transmitted |
| | `node_network_receive_errs_total` | Receive errors per interface |
| | `node_network_transmit_errs_total` | Transmit errors per interface |
| | `node_network_receive_drop_total` | Receive drops per interface |
| | `node_network_transmit_drop_total` | Transmit drops per interface |
| | `node_network_receive_multicast_total` | Multicast packets received |
| | `node_network_mtu_bytes` | Interface MTU |
| | `node_network_speed_bytes` | Interface speed |
| | `node_network_info` | Interface metadata (MAC, admin/oper state, duplex, alias) |
| **Disk** | `node_disk_read_bytes_total` / `writes_completed_total` | Disk read/write throughput and IOPS |
| | `node_disk_io_time_seconds_total` | Time spent doing disk I/O |
| **Filesystem** | `node_filesystem_size_bytes` / `free_bytes` / `avail_bytes` | Filesystem capacity, free and available space |
| **Network Statistics** | `node_netstat_Tcp_*` | TCP connection counts, retransmits, errors, opens |
| | `node_netstat_Udp_*` | UDP datagram counters and errors |
| | `node_netstat_Icmp_*` / `Icmp6_*` | ICMP message counts and errors |
| | `node_netstat_Ip_Forwarding` | IP forwarding enabled status |
| | `node_netstat_IpExt_InOctets` / `OutOctets` | Total IP octets in and out |
| **Conntrack** | `node_nf_conntrack_entries` | Current active connection tracking entries |
| | `node_nf_conntrack_entries_limit` | Maximum conntrack table size |
| | `node_nf_conntrack_stat_drop` / `early_drop` | Conntrack drops and forced evictions |
| **Socket Stats** | `node_sockstat_TCP_inuse` / `TCP_alloc` | In-use and allocated TCP sockets |
| | `node_sockstat_UDP_inuse` | In-use UDP sockets |
| | `node_sockstat_sockets_used` | Total IPv4 sockets in use |
| **Softnet** | `node_softnet_processed_total` | Packets processed by CPU softirq |
| | `node_softnet_dropped_total` | Packets dropped in softirq |
| | `node_softnet_times_squeezed_total` | Times packet processing ran out of quota |
| **System** | `node_boot_time_seconds` | Node boot timestamp |
| | `node_forks_total` | Total process forks |
| | `node_intr_total` | Total interrupts serviced |
| | `node_procs_running` / `procs_blocked` | Running and I/O-blocked process counts |
| | `node_pressure_cpu_waiting_seconds_total` | CPU pressure stall time |
| | `node_pressure_io_waiting_seconds_total` | I/O pressure stall time |
| | `node_entropy_available_bits` | Available entropy for random number generation |
| | `node_arp_entries` | ARP table entries per interface |
| | `node_os_info` | OS version and name labels |
| | `node_uname_info` | Kernel and hostname information |

### 1.2 Routing Metrics — `frr_exporter` (port 9101)

Provided by the **frr_exporter** running on each VyOS router. These cover the FRR (Free Range Routing) stack.

| Category | Metric | Description |
|---|---|---|
| **BFD** | `frr_bfd_peer_count` | Number of BFD peers detected |
| **Collector Health** | `frr_collector_up` | Whether each FRR collector scraped successfully (collectors: bfd, bgp, bgp6, ospf, route) |
| | `frr_scrape_duration_seconds` | Time taken per collector scrape |
| | `frr_scrapes_total` | Total number of FRR scrapes performed |
| **OSPF** | `frr_ospf_neighbors` | Number of OSPF neighbours detected per interface and VRF |
| | `frr_ospf_neighbor_adjacencies` | Number of full OSPF adjacencies formed per interface and VRF |
| **Routing Table** | `frr_route_total` | Total routes in the routing table (by AFI: ipv4 / ipv6, VRF) |
| | `frr_route_total_fib` | Total routes installed in the FIB (forwarding table) |

> **Note:** The example topology has 5 OSPF-speaking interfaces (eth1.301, eth2.302, eth3.309, eth4.305, eth5.310), each with 1 neighbour and 1 adjacency. The routing table shows 35 IPv4 routes and 6 IPv6 routes, with 29 IPv4 and 6 IPv6 installed in the FIB.

---

## 2. Metrics Selected and Written to Cloud Spanner

The metrics collector (`logservices/metricscollector/src/main.py`) polls Cloud Monitoring every 20 seconds. Rather than ingesting all available metrics, it selects a focused subset relevant to network health and routing state. Only series labelled with `router_name` and belonging to the `vyos-lab` job are processed.

### 2.1 Spanner Table Schema

Metrics are written to the `NetworkMetrics` table with the following columns:

| Column | Description |
|---|---|
| `timestamp` | Metric sample end-time |
| `node_name` | Router name (from `router_name` label) |
| `metric_name` | Raw metric name (e.g. `node_network_up`) |
| `metric_type` | Prometheus type: `gauge` or `counter` |
| `kind` | Category: `SYSTEM` (node_ prefix) or `ROUTING` (frr_ prefix) |
| `value` | Numeric metric value (float) |
| `labels` | Full label set as JSON (includes device/interface, VRF, AFI, etc.) |
| `interface` | Value of the `device` label if present, else null |

### 2.2 Selected System Metrics

These are derived from **node_exporter** and categorised as `kind = SYSTEM`:

| Metric Name | Type | Description |
|---|---|---|
| `node_load1` | gauge | 1-minute CPU load average |
| `node_memory_SwapFree_bytes` | gauge | Free swap space in bytes |
| `node_memory_MemTotal_bytes` | gauge | Total physical memory in bytes |
| `node_network_up` | gauge | Interface operational state (1 = up, 0 = down) — per interface |
| `node_network_carrier` | gauge | Physical carrier state — per interface |
| `node_network_carrier_changes_total` | counter | Cumulative carrier flaps — per interface |
| `node_network_receive_bytes_total` | counter | Bytes received — per interface |
| `node_network_receive_drop_total` | counter | Receive drops — per interface |
| `node_network_receive_errs_total` | counter | Receive errors — per interface |
| `node_network_receive_packets_total` | counter | Packets received — per interface |
| `node_network_transmit_bytes_total` | counter | Bytes transmitted — per interface |
| `node_network_transmit_drop_total` | counter | Transmit drops — per interface |
| `node_network_transmit_errs_total` | counter | Transmit errors — per interface |
| `node_network_transmit_packets_total` | counter | Packets transmitted — per interface |

### 2.3 Selected Routing Metrics

These are derived from **frr_exporter** and categorised as `kind = ROUTING`:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `frr_bfd_peer_count` | gauge | — | Total BFD peers detected on the router |
| `frr_collector_up` | gauge | `collector` | Health of each FRR data collector (bfd, bgp, bgp6, ospf, route) |
| `frr_ospf_neighbor_adjacencies` | gauge | `area`, `iface`, `vrf` | Full OSPF adjacencies formed per interface |
| `frr_ospf_neighbors` | gauge | `area`, `iface`, `vrf` | OSPF neighbours detected per interface |
| `frr_route_total` | gauge | `afi`, `vrf` | Total routes in the routing table |
| `frr_route_total_fib` | gauge | `afi`, `vrf` | Total routes installed in the forwarding table |
| `process_open_fds` | gauge | — | Open file descriptors in the FRR exporter process |
| `process_network_receive_bytes_total` | counter | — | Bytes received by the FRR exporter process |
| `process_network_transmit_bytes_total` | counter | — | Bytes transmitted by the FRR exporter process |

### 2.4 Aggregation

Before storage, metrics are aligned over the poll window (20 seconds):

- **Counters** (`CUMULATIVE` kind): aligned using `ALIGN_RATE` — the value stored is the per-second rate of change over the window.
- **Gauges** (`GAUGE` kind): aligned using `ALIGN_MEAN` — the average value over the window.

### 2.5 Retention

A background thread runs every 20 minutes and deletes rows from `NetworkMetrics` older than **3 hours**, keeping the Spanner table compact and relevant for near-real-time analysis.

---

## 3. Data Flow Summary

```
VyOS Router
  ├── node_exporter  :9100  ──┐
  └── frr_exporter   :9101  ──┤
                              │  (Prometheus scrape)
                    GCP Ops Agent
                              │
                    Cloud Monitoring
                    (prometheus.googleapis.com/*)
                              │
                    metricscollector (Cloud Run)
                    - Polls every 20s
                    - Selects 23 specific metrics
                    - Filters to job="vyos-lab"
                    - Aggregates (rate or mean)
                              │
                    Cloud Spanner
                    NetworkMetrics table
```
