# Transport RCA Scenario

This script details how to demonstrate a transport network root cause analysis scenario. 

## Pre-requisites

* [Install GNN Sandbox](/INSTALL.md) 

## Create l3 vpn transport network

Create an L3 hub and spoke VPN. From the gnnsandbox base directory run the following command.

```
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml
```

You can see the current state of the routers by running the following command. All VyosRouters and Devices should have a state of `Ready`.

```
kubectl get vyosrouter -n default
kubectl get devices -n default
```

You can demonstrate [spanner queries](/docs/spanner/demo.md) in Spanner Studio that show the network topology.

The network topology and state can be viewed in the network dashboard. Find the `network-dashboard` cloud run service url and log in using the `WEBAPPS_LOGIN` and `WEBAPPS_PWD` set in your environment. 

In future releases creating vpn services will be available through an agentic chat interface. 

## Run traffic

Vyos Routers [send metrics to Cloud Monitoring](/docs/network/metrics.md), the next task is to generate  traffic to represent what `Normal` looks like. 

Run the following command to deploy simulated traffic across the l3 vpn.

```
kubectl apply -f environment/telco-lab/l3vpn-test.yaml
```

This will run a test that initiates traffic from both spoke devices to the hub device. The test should run for an hour to collect the data needed to train the GNN. 

In the network dashboard UI click on the performance icon on the top right to show the current router performance.

## Train & Deploy GNN

TBD

```
./install.sh --deploy gnn
```

## Introduce Faults

[A set of faults can be introduced to the l3 vpn network](/docs/network/FAULT_INJECTION.md)

Apply a configuration fault by applying a fault configuration in the `environment/telco-lab` directory, e.g. 

```
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault1-mtu.yaml
```

This will update the configuration of one of the routers to __misconfigure__ an interface mtu.

## Show Anomalies

In the dashboard UI nodes with high anomaly are highlighted and the anomalies can be investigated further. 

