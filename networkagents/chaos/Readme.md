# Chaos Agent

The Chaos Agent can introduce various failures into a VyOS network to demonstrate Graph Neural Network (GNN) root cause analysis.

Faults can target different layers of the network and simulate misconfigurations or hardware-level issues in the backbone.

## Supported Failure Types

1. **MTU Mismatch / Silent Drop**
   - **Type**: Interface Misconfiguration
   - **Target**: Provider Edge uplinks (e.g., PE1 `eth1`)
   - **Description**: Alters the MTU (e.g., from 1500 to 1400), causing large BGP updates and payload packets exceeding the MTU to be silently aggregated and dropped while leaving the VPNv4 control plane and OSPF intact.
   - **VyOS CLI Commands**:
     ```bash
     configure
     set interfaces ethernet eth1 mtu '1400'
     commit
     exit
     ```
   - **VyOS Infrastructure Operator**: MTU can be explicitly configured via the `VyOSRouter` interface configuration.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSRouter
     metadata:
       name: pe1
     spec:
       interfaces:
         - name: eth1
           mtu: 1400 # Inject fault: changed from 1500
     ```

2. **Session Teardown / BGP Down**
   - **Type**: Routing Protocol Disruption
   - **Target**: Hub/Provider Edge (e.g., PE2 to CE1-HUB)
   - **Description**: Shuts down the eBGP session between the Provider Edge and Customer Edge, causing a total blackout of spoke-to-spoke traffic as customer routes are withdrawn.
   - **VyOS CLI Commands**:
     ```bash
     configure
     delete protocols bgp vrf BLUE_HUB neighbor 10.80.80.2
     commit
     exit
     ```
   - **VyOS L3VPN Operator**: The operator currently does not support an explicit `enabled: false` on BGP neighbors, but you can remove the neighbor entry from the `VyOSL3VPN` or `VyOSUnderlay` spec to achieve the teardown.
     ```yaml
     # Remove neighbor from VyOSL3VPN spec under pe2 VRF
     # neighbors: [{ peer: "10.80.80.2", remote_as: 65035 }] -> []
     ```

3. **Process Crash / Service Outage**
   - **Type**: Node/Process Failure
   - **Target**: Route Reflectors (e.g., RR1)
   - **Description**: Simulates a crash of the BGP process or disables the loopback interface on a Route Reflector. This drops PE-to-RR BGP sessions and tests network reconvergence times and backup mechanisms.
   - **VyOS Container Commands**:
     ```bash
     # To crash the BGP process directly on the container:
     killall bgpd
     ```
   - **VyOS CLI Alternative** (Disabling loopback):
     ```bash
     configure
     set interfaces dummy dum0 disable
     commit
     exit
     ```
   - **Kubernetes Action**: You can simulate this by deleting the `VyOSRouter` pod directly (`kubectl delete pod vyos-rr1`) or by disabling the loopback interface in the `VyOSInfrastructure` spec.

4. **Route Target Misconfiguration**
   - **Type**: VRF / Route Policy Misconfiguration
   - **Target**: Provider Edge VRF configurations (e.g., PE3 `BLUE_SPOKE`)
   - **Description**: Modifies the VRF import or export route-target (e.g., changing `65035:1030` to `65035:9999`). This causes partial or one-way reachability failures and silent isolation by rejecting valid routes.
   - **VyOS CLI Commands**:
     ```bash
     configure
     delete vrf name BLUE_SPOKE protocols bgp route-target import '65035:1030'
     set vrf name BLUE_SPOKE protocols bgp route-target import '65035:9999'
     commit
     exit
     ```
   - **VyOS L3VPN Operator**: This can be directly modified in the `VyOSL3VPN` custom resource.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSL3VPN
     metadata:
       name: l3vpn-blue-service
     spec:
       routers:
         - name: "pe3"
           vrfs:
             - name: "BLUE_SPOKE"
               rt_import: ["65035:9999"] # Inject fault: changed from 65035:1030
     ```

5. **Hardware Degradation / Packet Corruption**
   - **Type**: Physical Layer Impairment
   - **Target**: Provider Core links (e.g., P1 to P3 link)
   - **Description**: Progressively injects CRC errors or packet drops using `tc` (traffic control) to simulate a failing optical SFP. This causes slow hardware failures, microburst packet drops, and eventual routing flaps.
   - **VM/Container CLI Commands** (using `tc`):
     ```bash
     # Introduce 5% packet loss on eth2
     tc qdisc add dev eth2 root netem loss 5%
     
     # To inject packet corruption (mimicking CRC errors):
     tc qdisc change dev eth2 root netem corrupt 2%
     ```
   - **VyOS Router Operator**: The operator supports `traffic_policy.network_emulator` to inject `packet-loss`, `network-delay`, and `packet-corruption` at the configuration level.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSRouter
     metadata:
       name: p1
     spec:
       traffic_policy:
         network_emulator:
           - name: SFP_DEGRADE
             loss: 5%
             corruption: 2%
         apply:
           - interface: eth2
             out: SFP_DEGRADE
     ```

6. **Physical Link Disconnection / Cable Pull**
   - **Type**: Physical Interface Down
   - **Target**: Any router interface (e.g., P1 to P2 link)
   - **Description**: Simulates a physical cable being unplugged or a port failing at the hardware level. This triggers immediate L1/L2 down events and forces OSPF/LDP to recalculate paths.
   - **Linux Container/VM Commands**:
     ```bash
     # To simulate a physical cable disconnect:
     ip link set dev eth2 down
     ```
   - **VyOS Infrastructure Operator**: You can disable the interface explicitly.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSRouter
     metadata:
       name: p1
     spec:
       interfaces:
         - name: eth2
           enabled: false # Inject fault: administratively down
     ```

7. **Logical Misconfiguration / OSPF Area Mismatch**
   - **Type**: Logical Routing Protocol Misconfiguration
   - **Target**: Provider Core routers (e.g., P2)
   - **Description**: Places an interface in the wrong OSPF area, causing an adjacency failure without bringing down the physical link. Simulates operator error during maintenance.
   - **VyOS CLI Commands**:
     ```bash
     configure
     delete protocols ospf area 0.0.0.0 network 10.0.0.0/24
     set protocols ospf area 0.0.0.1 network 10.0.0.0/24
     commit
     exit
     ```
   - **VyOS Underlay Operator**: You can misconfigure the OSPF area assignment in the `VyOSUnderlay` custom resource for a specific router.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSUnderlay
     metadata:
       name: l3vpn-hub-spoke-underlay
     spec:
       routers:
         - name: "p2"
           protocols:
             ospf:
               areas: [{ area: "0.0.0.1", type: "stub" }] # Inject fault: wrong area
     ```

8. **IP Address Overlap / Duplicate IP**
   - **Type**: IP Addressing Conflict
   - **Target**: Any router or VM (e.g., P3 or a new rogue container)
   - **Description**: Configures an interface with an IP address that is already in use by another critical infrastructure node (like a core router's loopback or P-to-P link). This causes severe routing instability, intermittent connectivity, and MAC/ARP flapping.
   - **VyOS CLI Commands**:
     ```bash
     configure
     set interfaces ethernet eth2 address '10.0.0.1/24' # Assuming 10.0.0.1 is already used
     commit
     exit
     ```
   - **Linux Container/VM Commands**:
     ```bash
     ip addr add 10.0.0.1/24 dev eth2
     ```
   - **VyOS Infrastructure Operator**: Change the IP address assigned to a link to collide with an existing router's IP.
     ```yaml
     apiVersion: google.dev/v1
     kind: VyOSInfrastructure
     metadata:
       name: l3vpn-hub-spoke-infra
     spec:
       networks:
         - name: p1-p3
           connected_routers:
             - router_name: p3
               interface: eth2
               ip_address: "172.16.40.1" # Inject fault: overlaps with p1
     ```

## Agent Behaviour

* Every X number of mins
  * read topology
  * pick a few errors & area of network to apply
  * apply errors
  * document error and area of network applied
