# Network Simulator

The network simulator runs [VyOS routers](https://vyos.io/) as a set of containers inside a GCE Virtual Machine. Vyos containers are connected over linux bridges to mimick physical connections.

The drawing below shows a sample virtual network deployment in a GCE virtual machine. This is dynamically orchestrated by a network operator running in GKE with performance a logging sent to Cloud Monitoring. 

![virtual network](/docs/drawings/networking.drawio.svg)

The following Linux networking components make up a virtual network as follows:

* **Containers**: Vyos Routers and end host/free5gc devices are deployed in docker containers.
* **Veth Pair**: Virtual ethernet pairs are added to each container to add interfaces inside the container. 
* **Linux Bridge**: the other end of the veth pair is added to a linux bridge which provides a l2 connection between 2 container interfaces. A special management bridge connects all mgmt interfaces for each container allowing their endpoints to be scraped. 

The virtual network lifecycle Automation & monitoring components are as follows:

* [**GKE Operator**](/operator/Readme.md): a k8s operator manages a set of CRDs that provision complete virtual networks. 
* **OpsAgent**: each vyos router container deployed provides a prometheus endpoint that can be scraped by an OpsAgent also installed to the network virtual machine. Vyos syslog is also sent to OpsAgent.
* **Cloud Monitoring**: OpsAgent sends everything it sees and scrapes to Cloud Monitoring. 
* **EventArc**: An eventarc and cloud function [collect metrics](/docs/network/metrics.md) and logs from cloud monitoring and updates Spanner. 

## Sample Transport Network

The diagram below illustrates a [hub-and-spoke L3VPN topology](/environment/telco-lab/l3vpn-hub-spoke.yaml) provided as an example by the system.

![l3vpn](/docs/drawings/transport/l3vpn-example.drawio.svg)

Two spoke sites connect via CE routers to Provider Edge (PE) routers at 100 Mbps, which feed into an MPLS core running OSPF, LDP, and iBGP (AS 65001) across four P-routers at 1 Gbps. Route Reflectors (RR 1 and RR 2) distribute iBGP routes across the core. VRF routing policy is defined by `BLUE_HUB` (imports routes from both hub and spoke targets) and `BLUE_SPOKE` (imports only hub routes), enabling hub-controlled traffic flow between spokes.

[More details on the L3VPN model can be found here.](/docs/network/l3vpn.md)

## Traffic Simulator

The [TrafficTest CRD](/operator/config/traffic.yaml) defines end-to-end network traffic testing scenarios between devices in the virtual network. 

A test specifies one or more source devices and a single destination, the transport protocol (TCP or UDP), a target bandwidth, and a duration. Multiple sources are supported simultaneously, enabling aggregate load tests — for example, several branch-office CPEs all sending to a central hub at the same time. 

Traffic behaviour is controlled by a configurable pattern type: a steady `constant` rate for baseline measurements, a `periodic` wave (sine, square, or sawtooth) for cyclical load, a `burst` mode that alternates high-rate bursts with idle periods to simulate bursty applications, or a `poisson` mode that models realistic random user arrivals with configurable session lengths and think times. Optional per-interval metrics collection (throughput, latency, packet loss, jitter, active connections, and TCP retransmissions) can be enabled at a chosen sampling frequency.

The operator tracks test execution through a `status` subresource that progresses through the phases `Pending → Deploying → Running → Completed / Failed / Stopped`. During execution, live metrics are reported both per source device and as aggregate totals across all sources, giving a real-time view of total throughput, average latency, and average packet loss across the network path under test. Start and end timestamps are recorded alongside a human-readable message that describes the current state or provides troubleshooting guidance if something goes wrong.

[A sample test for the L3 VPN topology is provided](/environment/telco-lab/l3vpn-test.yaml)

## Virtual Mobile Network

free5gc containers can be attached to a vyos transport network as seen in the picture below. This is work in progress as of now. 

![free5gc](/docs/drawings/free5gc/l3vpn-example.drawio.svg)



