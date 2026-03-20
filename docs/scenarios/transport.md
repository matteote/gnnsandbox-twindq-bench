# Transport RCA Scenario

This script details how to demonstrate a transport network root cause analysis scenario. 

## Create l3 vpn transport network

Create l3vpn hub and spoke. From the gnnsandbox directory run the following command.

```
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke.yaml
```

You can see the current state of the routers by running the following command. All VyosRouters should have a state of `Ready`.

```
kubectl get vyosrouter -n default
```

You can demonstrate some [spanner queries](/docs/spanner/demo.md) directly in Spanner Studio that render the network topology. 

## Run traffic

Vyos Routers are sending metrics to Cloud Monitoring, the next task is to generate some traffic to represent what `Normal` looks like. Run the following command to deploy some traffic simulation across the l3 vpn.

```
kubectl apply -f environment/telco-lab/l3vpn-test.yaml
```

This will run a test that initiates traffic from both spoke devices to the hub device. The test should run for an hour to collect the data needed to train the GNN. 

## Train & Deploy GNN

```
./install.sh --deploy gnn
```

## Show Network State

The dashboard UI shows the current and historical network topology and associated state. 

## Introduce Faults

[A set of faults can be introduced to the l3 vpn network](/docs/network/FAULT_INJECTION.md)

You can apply a configuration fault by applying a fault configuration in the `environment/telco-lab` directory, e.g. 

```
kubectl apply -f environment/telco-lab/l3vpn-hub-spoke-fault1-mtu.yaml
```

This will update the configuration of one of the routers to __misconfigure__ an interface mtu.

## Show Anomalies

In the dashboard UI nodes with high anomaly are highlighted and the anomalies can be investigated further. 