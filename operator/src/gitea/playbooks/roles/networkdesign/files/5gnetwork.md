# 5G Network Design

A fully operational 5G network service will need one control plane, one user plane, one data network and one or more radio simulators to function correctly. 

If you need to propose names for new network services or locations use the following guidelines:
- new network location names are at your discretion to propose
- When creating new network locations the following CIDR ranges are not to be used, i.e. these CIDRs are already used by the system
  - 10.0.0.0/24
  - 10.0.100.0/24
  - 10.60.0.0/24
- When creating new network locations check that the ip address with cidr for existing network locations
- new network service names are at your discretion
- new connectivity service names are at your discretion
- UserPlaneFunction and UERanSim network services must not be assigned the same network locations.
- The network location assigned to DataNetwork network service should be the same as the network location assigned to the UPF network location
- the dataplane network location is a reserved network location, you must not use it in your planned steps.

Network locations attached to UERanSim and UserPlaneFunction network services must be attached to a connectivity service so traffic can be carried between them. 

When connecting more than two network locations you should use a Mesh connectivity service with multiple interfaces.

## 5g mobile network service Orders

End customers can order their own network service instance. The parameters of a customer order can be seen in the table below. 

|order parameter      | value                                | Impact to network design                 |
|---------------------|--------------------------------------|------------------------------------------|
|sliceType            | type of mobile service embb or urllc | No impact right now                      |
|bandwidth            | speed of the network in mbps         | No impact right now                      |
|geographicArea       | array of geographic sites            | Cellsite needed for each site in the list|
|duration             | time to be active in days            | No Impact right now                      |


### Example Order

Given the following order:

| order parameter| value |
|----------------|-------|
|sliceType| embb |
|bandwidth|100mbps|
|geographicArea| 'london', 'ny' |
|duration|100days |
        
Right now the only parameter that impacts our design is the geographicArea parameter. For each value in the list we need to add a new cellsite with that name. Therefore the design for this order would be as follows

```
Create a 5g network with the following capabilities
* core network location with cidr 10.0.40.0/24
* internet network location with cidr 172.168.0.0/24
* cellsite network location with cidr 10.0.50.0/24
* Core Network Service called core1 connected to core network location
* UPF Network Service called upf1 connected to core network location and internet network location
* DNN network service called dnn1 connected to the internet network location
* 2 cellsites called london and ny connected to the cellsite network location
* mesh network connecting cellsite network location to core network location
```