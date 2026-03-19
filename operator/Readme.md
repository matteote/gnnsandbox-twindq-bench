# Network Operator

This kubernetes operator manages the lifecycle of network functions and network services used in the demo, along with automation of some of the demo infrastructure itself.

The operator code is based on the [kopf](https://kopf.readthedocs.io/en/latest/) operator framework and embeds [ansible playbooks](https://docs.ansible.com/) to run commands inside the network VM.

## Build and deploy the operator

The operator is automatically deployed when __install.sh -s__ is run. To update to a new version of the operator run the following command from the NetworkAgent base directory.

```
./install.sh -o
```

To see operator logs run the following commands. 

```
kubectl get pods
NAME                          READY   STATUS      RESTARTS   AGE
networkoperator-<UUID>        1/1     Running     0          88s
kubectl logs -f networkoperator-<UUID>
```

## Running the operator locally on your laptop

Ensure the GOOGLE_PROJECT, GOOGLE_REGION and GOOGLE_ZONE environment variables are set (as described in the initial GCP setup readme)

Run the following to start the operator on your laptop in the __NetworkAgena/operator__ directory. 

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd src
kopf run main.py --verbose
```