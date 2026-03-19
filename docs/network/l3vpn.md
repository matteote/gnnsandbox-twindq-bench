# L3 VPN Model

The example in the demo is a L3 VPN with 3 sites provisioned on a number of VyOS routers. This section describes the physical and logical models. 

## L3 VPN

L3VPN consists of multiple access links, VPN routing and forwarding (VRF) tables, and MPLS paths or P2MP LSPs. A L3VPN can be configured to connect two or more customer sites. In hub-and-spoke MPLS L3VPN environments, the spoke routers have unique Route Distinguisers (RDs). The spoke sites export their routes to the hub. Spokes can talk to hubs, but never have direct paths to other spokes. All traffic is controlled and delivered over the hub site. 

![example](/docs/drawings/transport/l3vpn-example.drawio.svg)

In the demo example above, PE1 is the hub PE and has the main VRF (BLUE_HUB), its own Route-Distinguisher (RD) and route-targer import/export lists. Multi-protocol BGP (MP-BGP) delivers L3VPN related control plane information to the nodes across network where the PE spokes import the route-target 60535:1030 (export route-target of vrf BLUE_HUB) and export its own route-target 60535:1011 (this is BLUE_SPOKE export route-target). 

The customer edge nodes can only learn the network prefixes of the hub site. 

Route-Reflector devices are used to simplify network routes exchange and minimise iBGP peerings between devices. 

| Node | Role | VRF        | RD              | RT Import              | RT Export |
|------|------|------------|-----------------|------------------------|-----------|
| PE2  | Hub  | BLUE_HUB   | 10.80.80.1:1011 | 65035:1011, 65035:1030 | 65035:1030|
| PE1  | Spoke| BLUE_SPOKE | 10.50.50.1:1011 | 65035:1030             | 65035:1011|
| PE3  | Spoke| BLUE_SPOKE | 10.60.60.1:1011 | 65035:1030             | 65035:1011|

The following sections break this L3 VPN topology into logically separate models that have different but interlinked lifecycles. These models provide the schema for representing the network in the network topology service. 

## Physical Model

This model represents the physical connectivity and capacity available in the network. Modelling vendor and location aspects of the physical routers and the physical links connecting router ports together. 

![physical model](/docs/drawings/transport/physical_model.drawio.svg)

### Nodes

The following nodes are modelled. 

* __Physical_Router:__ Represents any physical router device (PE, P).
    * __Properties:__ router_id, name, make, model, serial_number, location, role (e.g., "PE", "P")
* __Physical_Interface:__ Represents a physical port on a Physical_Router.
    * __Properties:__ interface_id, name (e.g., "GigabitEthernet0/1"), port_speed, media_type (e.g., "fiber", "copper"), ip_address (if used for routing on a physical link, distinct from logical subinterfaces)
* __Physical_Link:__ Represents a direct physical cable connection between two Physical_Interfaces.
    * __Properties:__ link_id, bandwidth, status (e.g., "up", "down")

### Relationships:

#### HAS_PHYSICAL_INTERFACE:
Direction: Physical_Router -> Physical_Interface
Description: A physical router has multiple physical interfaces.

#### CONNECTS_PHYSICALLY_TO:
Direction: Physical_Interface -> Physical_Link
Description: A physical interface is an endpoint of a physical link. (Or direct between interfaces, but using a Physical_Link node allows adding link-specific properties like status, bandwidth, etc.)

#### PART_OF_PHYSICAL_LINK:

Direction: Physical_Link -> Physical_Interface (inverse of above, for clarity)
Description: A physical link connects two specific physical interfaces.

#### FORWARDS_TRAFFIC_VIA: (Implicit, but useful for understanding the overlay on underlay)

Direction: PE_Router (logical) -> Physical_Router (physical)
Description: A logical PE router entity is instantiated on a specific physical router. (This links the two models)

### Example Instance of Physical Underlay Model

Let's assume the following simple physical underlay for our tenant-a-l3vpn-network:

```
PE-East-1 is a physical router.
PE-West-1 is a physical router.
P-Core-1 is a physical core router.
P-Core-2 is another physical core router.
```

And the following physical connections:

```
PE-East-1 (Gi0/1) connects to P-Core-1 (Gi0/1)
P-Core-1 (Gi0/2) connects to P-Core-2 (Gi0/1)
P-Core-2 (Gi0/2) connects to PE-West-1 (Gi0/1)
```

#### Nodes:

__*Physical_Routers:*__

__router_id: "Phy-PE-East-1"__
* name: "PE-East-1"
* make: "Cisco"
* model: "ASR9K"
* erial_number: "ABC12345"
* location: "East Data Center"
* role: "PE"

__router_id: "Phy-PE-West-1"__
* name: "PE-West-1"
* make: "Juniper"
* model: "MX204"
* serial_number: "DEF67890"
* location: "West Data Center"
* role: "PE"

__router_id: "Phy-P-Core-1"__
* name: "P-Core-1"
* make: "Cisco"
* model: "NCS5500"
* serial_number: "GHI11223"
* location: "Core DC-A"
* role: "P"

__router_id: "Phy-P-Core-2"__
* name: "P-Core-2"
* make: "Juniper"
* model: "PTX10008"
* serial_number: "JKL44556"
* location: "Core DC-B"
* role: "P"

__*Physical_Interface:*__

__interface_id: "Phy-PE-East-1_Gi0/1"__
* name: "GigabitEthernet0/1"
* port_speed: "10G"
* media_type: "fiber"
* ip_address: "192.168.10.1/30"

__interface_id: "Phy-P-Core-1_Gi0/1"__
* name: "GigabitEthernet0/1"
* port_speed: "10G"
* media_type: "fiber"
* ip_address: "192.168.10.2/30"

__interface_id: "Phy-P-Core-1_Gi0/2"__
* name: "GigabitEthernet0/2"
* port_speed: "100G"
* media_type: "fiber"
* ip_address: "192.168.20.1/30"

__interface_id: "Phy-P-Core-2_Gi0/1"__
* name: "GigabitEthernet0/1"
* port_speed: "100G"
* media_type: "fiber"
* ip_address: "192.168.20.2/30"

__interface_id: "Phy-P-Core-2_Gi0/2"__
* name: "GigabitEthernet0/2"
* port_speed: "10G"
* media_type: "fiber"
* ip_address: "192.168.30.1/30"

__interface_id: "Phy-PE-West-1_Gi0/1"__
* name: "GigabitEthernet0/1"
* port_speed: "10G"
* media_type: "fiber"
* ip_address: "192.168.30.2/30"

__*Physical_Link:*__

__link_id: "Link-PE-East-1_P-Core-1"__
* bandwidth: "10Gbps"
* status: "up"

__link_id: "Link-P-Core-1_P-Core-2"__
* bandwidth: "100Gbps"
* status: "up"

__link_id: "Link-P-Core-2_PE-West-1"__
* bandwidth: "10Gbps"
* status: "up"

### Relationships

```
Phy-PE-East-1 --HAS_PHYSICAL_INTERFACE--> Phy-PE-East-1_Gi0/1
Phy-P-Core-1 --HAS_PHYSICAL_INTERFACE--> Phy-P-Core-1_Gi0/1
Phy-P-Core-1 --HAS_PHYSICAL_INTERFACE--> Phy-P-Core-1_Gi0/2
Phy-P-Core-2 --HAS_PHYSICAL_INTERFACE--> Phy-P-Core-2_Gi0/1
Phy-P-Core-2 --HAS_PHYSICAL_INTERFACE--> Phy-P-Core-2_Gi0/2
Phy-PE-West-1 --HAS_PHYSICAL_INTERFACE--> Phy-PE-West-1_Gi0/1
Phy-PE-East-1_Gi0/1 --CONNECTS_PHYSICALLY_TO--> Link-PE-East-1_P-Core-1
Phy-P-Core-1_Gi0/1 --CONNECTS_PHYSICALLY_TO--> Link-PE-East-1_P-Core-1
Phy-P-Core-1_Gi0/2 --CONNECTS_PHYSICALLY_TO--> Link-P-Core-1_P-Core-2
Phy-P-Core-2_Gi0/1 --CONNECTS_PHYSICALLY_TO--> Link-P-Core-1_P-Core-2
Phy-P-Core-2_Gi0/2 --CONNECTS_PHYSICALLY_TO--> Link-P-Core-2_PE-West-1
Phy-PE-West-1_Gi0/1 --CONNECTS_PHYSICALLY_TO--> Link-P-Core-2_PE-West-1
```

### Linking Logical to Physical

This is a crucial part. The logical PE_Router nodes (PE-East-1, PE-West-1) from our previous model are realized by the physical Physical_Router nodes (Phy-PE-East-1, Phy-PE-West-1).

```
PE-East-1 (logical) --FORWARDS_TRAFFIC_VIA--> Phy-PE-East-1 (physical)
PE-West-1 (logical) --FORWARDS_TRAFFIC_VIA--> Phy-PE-West-1 (physical)
```

This combined model allows you to answer questions like:
* What physical router is hosting a specific logical PE?
* What physical links are traversed between two PE routers?
* What is the bandwidth of the physical link supporting a particular logical VPN segment?
* If a physical interface goes down, which logical services (L3VPNs, VRFs) might be affected?

This multi-layered graph model provides a powerful way to represent and analyze complex telco networks.

## Underlay Descriptor

The underlay configures P routers.

![underlay model](/docs/drawings/transport/underlay_logical_models.drawio.svg)


## L3VPN Service Descriptor

L3 VPN Service configured to PE/CE

![edge model](/docs/drawings/transport/edge_logical_model.drawio.svg)


```
Tenant_A --HAS_VPN--> L3VPN_SpokeHub
L3VPN_SpokeHub --USES_VRF (pe_id=PE1)--> VRF_TenantA_PE1
L3VPN_SpokeHub --USES_VRF (pe_id=PE2)--> VRF_TenantA_PE2

PE1 --HOSTS_VRF--> VRF_TenantA_PE1
PE1 --HAS_INTERFACE--> Int_PE1_Eth0
PE1 --HAS_INTERFACE--> Int_PE1_Eth1 (loopback)

CE_Hub --CONNECTS_TO_PE (int_ce=Int_CEHub_Eth0, int_pe=Int_PE1_Eth0)--> PE1
CE_Hub --PART_OF_SITE--> Tenant_A
CE_Hub --HAS_INTERFACE--> Int_CEHub_Eth0
CE_Hub --ADVERTISES_SUBNET--> Subnet_Hub_Data

Int_PE1_Eth0 --BELONGS_TO_VRF--> VRF_TenantA_PE1

PE1 --ESTABLISHES_BGP--> BGP_PE1_CEHub
CE_Hub --ESTABLISHES_BGP--> BGP_PE1_CEHub (connecting to the same session node)

VRF_TenantA_PE1 --ROUTES_TO_SUBNET--> Subnet_Hub_Data
```

Example yaml descriptor. 

```
apiVersion: network.example.com/v1alpha1
kind: L3vpnNetwork
metadata:
  name: tenant-a-l3vpn-network
spec:
  tenant: "Tenant-A"
  type: "hub-and-spoke"
  hub:
    name: "Hub-Site-1"
    ceRouter: "CE-Hub-1"
    subnets: ["10.0.1.0/24", "10.0.2.0/24"]
    peConnection:
      peRouter: "PE-East-1"
      peInterface: "GigabitEthernet1/0/1.100"
      ceInterface: "GigabitEthernet0/0/0"
      bgp:
        localAs: 65001
        remoteAs: 65000
        peerIp: "172.16.1.1" # PE's IP
        localIp: "172.16.1.2" # CE's IP
  spokes:
    - name: "Spoke-Site-1"
      ceRouter: "CE-Spoke-1"
      subnets: ["10.0.3.0/24"]
      peConnection:
        peRouter: "PE-West-1"
        peInterface: "GigabitEthernet1/0/2.200"
        ceInterface: "GigabitEthernet0/0/0"
        bgp:
          localAs: 65002
          remoteAs: 65000
          peerIp: "172.16.2.1"
          localIp: "172.16.2.2"
    - name: "Spoke-Site-2"
      ceRouter: "CE-Spoke-2"
      subnets: ["10.0.4.0/24"]
      peConnection:
        peRouter: "PE-West-1"
        peInterface: "GigabitEthernet1/0/3.300"
        ceInterface: "GigabitEthernet0/0/0"
        bgp:
          localAs: 65003
          remoteAs: 65000
          peerIp: "172.16.3.1"
          localIp: "172.16.3.2"
```


## Security

Corporate security profile


## Application QoS

QoS profiles to prioritise traffic. 


## Network Intent

Requirements on how the network should perform. 

### Connectivity & Reachability:

#### Inter-Site Communication: 

All designated customer sites (e.g., Headquarters, Branch Offices, Data Centers, Cloud VPCs) must be able to securely communicate with each other over the VPN.

Example: Branch offices must be able to access applications hosted in the main data center and also communicate directly with other branch offices if required (depending on topology: hub-and-spoke vs. full mesh).

#### Application Access: 

Users and applications at any connected site must be able to reliably reach specific services and resources hosted at other connected sites.

Example: Employees at Spoke-Site-1 need access to the CRM server (10.0.1.10) in Hub-Site-1.

#### Internet Access (Optional): 

Define whether sites should have direct internet access, or if all internet traffic should be backhauled through a central site (e.g., the Hub) for centralized security.

Example: All internet bound traffic from Spoke-Sites must egress via the Hub-Site-1's internet gateway.

### Performance & Quality of Service (QoS):

#### Bandwidth: 

Sufficient bandwidth must be provisioned to support critical business applications and expected traffic volumes between sites.

Example: A minimum of 50 Mbps dedicated bandwidth is required between any spoke site and the hub site.

#### Latency: 

Acceptable latency limits for time-sensitive applications.

Example: Latency for VoIP traffic between any two connected sites must not exceed 100ms round-trip time (RTT).

#### Jitter & Packet Loss: 

Minimal jitter and packet loss to ensure quality for real-time applications like voice and video.

Example: Jitter for video conferencing must be below 20ms, and packet loss below 0.1%.

#### Traffic Prioritization: 

Ability to prioritize critical application traffic over less critical traffic (e.g., VoIP over file transfers).

Example: VoIP traffic must receive highest priority (e.g., DSCP EF marking) across the VPN, followed by critical business applications.

### Security & Isolation:

Data Confidentiality: All data transmitted over the VPN must be encrypted to prevent unauthorized access.

Example: All traffic within the VPN must be protected using AES-256 encryption.

#### Data Integrity: 

Ensure that data transmitted over the VPN has not been tampered with.

Example: Data integrity must be guaranteed using SHA-256 hashing.

#### Network Segmentation: 

The customer's network traffic must be logically isolated from other customers' traffic within the service provider's infrastructure.

Example: Our VPN traffic must be completely separate from any other customer's traffic on the service provider network.

#### Access Control: 

The ability to control which specific services or hosts can communicate across the VPN (e.g., using firewalls at the CE or PE edge).

Example: Only specific ports (e.g., 80, 443, 3389) should be allowed between Spoke-Site-1 and Hub-Site-1 for business applications.

### Reliability & Availability:

Uptime Guarantee (SLA): The VPN service must meet a specified uptime Service Level Agreement (SLA).

Example: The VPN service must have a minimum uptime of 99.9% (less than ~8.7 hours downtime per year).

#### Redundancy: 

Critical sites or connections should have redundant paths or failover mechanisms to prevent single points of failure.

Example: Hub-Site-1 must have redundant connections to two different PE routers or dual CE routers for high availability.

#### Disaster Recovery: 

The VPN must support disaster recovery scenarios, allowing critical sites to maintain connectivity even if a primary path or data center fails.

Example: In the event of a regional outage affecting our primary data center, traffic must automatically reroute to our secondary data center within 30 minutes.

### Management & Support:

Monitoring & Visibility: Ability to monitor the health, performance, and traffic of the VPN connections.

Example: We need access to a portal or reports showing bandwidth utilization, latency, and status of our VPN links.

#### Troubleshooting & Support: 

Clear process for reporting issues and guaranteed response times from the service provider.

Example: Critical issues must have a 1-hour response time and 4-hour resolution time as per our support agreement.

#### Scalability: 

The VPN solution must be scalable to accommodate future growth in sites, users, or bandwidth requirements.

Example: The VPN architecture must easily allow for adding new branch offices within a month's notice.


![intent](/docs/drawings/transport/customer_intent.drawio.svg)

