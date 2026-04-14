# Crash Tests

This directory contains fault-injected variants of the baseline
[`l3vpn-hub-spoke.yaml`](../l3vpn-hub-spoke.yaml) descriptor. Each file is a
complete, self-contained topology definition with a **single targeted fault**
introduced to simulate a real-world failure scenario.

## Common differences vs the baseline

All fault files share two cosmetic/minor divergences from the baseline:

- **YAML style**: unquoted strings and expanded block style are used throughout
  (vs the baseline's quoted strings and inline flow style `[{...}]`). This is
  purely formatting.
- **`rr1` / `rr2` role**: both route reflectors carry `role: P` instead of
  `role: RR`.

## File-by-file fault summary

| File | Fault type | What's changed | Affected node(s) |
|---|---|---|---|
| `fault1-mtu` | Interface MTU mismatch | `mtu: 1400` added to pe1 eth1 | pe1 ↔ p1 link |
| `fault2-ce-down` | CE / BGP session down | pe2 BLUE\_HUB BGP neighbors emptied (`[]`) | pe2 / ce1-hub |
| `fault3-rr1-crash` | Route reflector crash | rr1 BGP config block removed entirely | rr1 |
| `fault4-rt-import` | Wrong RT import | pe3 BLUE\_SPOKE `rt_import` set to `65035:9999` | pe3 / ce2-spoke |
| `fault5-sfp` | Physical link degradation | Traffic shaping policy on p1 eth2 (10 ms delay, 5 % loss, 1 % corruption) | p1 ↔ p3 link |
| `missing-config` | Incomplete VPN config push | pe1 VRF missing `rt_export`, `rt_import` and BGP neighbour entirely | pe1 / ce1-spoke |

---

### fault1-mtu — MTU mismatch on pe1

**Location**: `VyOSInfrastructure` → pe1 interfaces → eth1

pe1's uplink to p1 (`p1-pe1` network) is given a non-standard MTU:

```yaml
- name: eth1
  network: p1-pe1
  mtu: 1400
```

The rest of the network uses the default MTU. This causes MPLS/LDP
fragmentation issues on the pe1↔p1 link and can result in traffic blackholing
for larger packets once the VPN label stack is added.

---

### fault2-ce-down — Hub CE BGP session dropped

**Location**: `VyOSL3VPN` → pe2 → BLUE\_HUB VRF → bgp → neighbors

The BGP neighbour list for the hub VRF on pe2 is cleared:

```yaml
# Baseline:
neighbors: [{ peer: "10.80.80.2", remote_as: 65035 }]

# Fault:
neighbors: []
```

This simulates `ce1-hub` going offline or its BGP session being torn down.
Spoke sites can still establish their PE-CE sessions but all traffic destined
for the hub (and inter-spoke traffic that must hair-pin through it) is dropped.

---

### fault3-rr1-crash — RR1 loses its BGP configuration

**Location**: `VyOSUnderlay` → rr1 → protocols

rr1's entire `bgp:` block is removed. In the baseline rr1 is an iBGP route
reflector with four PE clients; in this fault file it only has OSPF and MPLS:

```yaml
# Baseline rr1 has:
bgp:
  as_number: 65001
  router_id: 10.0.0.1
  route_reflector: true
  neighbors: [ pe1, pe2, pe3, pe4 ]

# Fault: bgp block is absent
```

rr2 remains intact so VPN routes continue to be reflected, but all PE sessions
to `10.0.0.1` will fail. Any PE relying solely on rr1 for VPN prefix
reachability will lose routes until BGP reconverges through rr2.

---

### fault4-rt-import — Wrong RT import on pe3

**Location**: `VyOSL3VPN` → pe3 → BLUE\_SPOKE VRF → rt\_import

The import Route Target for pe3's spoke VRF is set to a non-matching community:

```yaml
# Baseline:
rt_import: ["65035:1030"]

# Fault:
rt_import: ["65035:9999"]
```

The hub PE (pe2) exports `65035:1030`. Because pe3 no longer imports that
community, `ce2-spoke` (behind pe3) will never receive hub routes. The fault is
silent — BGP sessions remain up but the VPN route table on pe3 is empty,
causing all traffic from spoke2 toward the hub or other spokes to be
black-holed.

---

### fault5-sfp — SFP / physical link degradation on p1

**Location**: `VyOSUnderlay` → p1 → traffic\_policy

A network-emulator traffic policy is applied to p1's `eth2` interface (the
`p1-p3` backbone link):

```yaml
traffic_policy:
  network_emulator:
  - name: SFP_DEGRADE
    delay: 10ms
    loss: 5%
    corruption: 1%
  apply:
  - interface: eth2
    out: SFP_DEGRADE
```

This simulates a degraded optical transceiver introducing latency, packet loss
and bit corruption on the p1↔p3 link without taking the link completely down.
OSPF and LDP remain up but traffic quality degrades, exercising fault detection
based on metrics rather than adjacency state.

---

### missing-config — Incomplete VPN config push to pe1

**Location**: `VyOSL3VPN` → pe1 → BLUE\_SPOKE VRF

pe1's VRF entry is missing its route-target policy and has no BGP section:

```yaml
# Baseline pe1 VRF:
vrfs:
  - name: BLUE_SPOKE
    table: 200
    rd: "10.50.50.1:1011"
    rt_export: ["65035:1011"]   # <-- missing
    rt_import: ["65035:1030"]   # <-- missing
    interfaces: ["eth2"]
bgp:
  vrfs:
    - name: BLUE_SPOKE
      neighbors: [{ peer: "10.50.50.2", remote_as: 65035 }]  # <-- missing

# Fault: rt_export, rt_import and the bgp block are all absent
```

This models a partial or failed config-push where the VRF was created but the
routing policy and CE-facing BGP session were never applied. `ce1-spoke` has
no PE-CE BGP session and pe1 exports/imports no VPN prefixes, completely
isolating spoke1 from the VPN.
