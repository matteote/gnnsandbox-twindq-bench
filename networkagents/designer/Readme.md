# Network Designer Agent

Design a low level network design based on high level natural language description of intent. 

The agent uses the following knowledge to reason this high level intent into actionable change requests.

* Design rules: Version controlled document describing the rules on how physical and network componenents should be connected or configured to deliver various 
* Current topology: Spanner holds the current and historical network topology and configuration
* Low level lifecycle APIs: GKE Network Operator published 