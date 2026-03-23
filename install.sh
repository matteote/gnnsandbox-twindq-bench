
#!/usr/bin/bash
#
# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


############################################################
# Display current work environment information             #
############################################################
DisplayGCPEnv()
{
    echo "Gathering active GCP environment information..."
    echo "  - GCP user: $(gcloud config list account --format="value(core.account)")"
    echo "  - GCP project: $(gcloud config get-value project 2>/dev/null)"
    echo "  - Active configuration: $(gcloud config configurations list --filter="IS_ACTIVE=True" --format="value(NAME)")"
    echo "  - GKE context: $(kubectl config current-context)"
}

############################################################
# Check the work environment                               #
############################################################
CheckGCPEnv()
{
    echo; echo -n "Checking your work environment..."

    # test if gcloud exists
    if ! command -v gcloud &> /dev/null
    then
        echo "gcloud could not be found, you must install it"
        exit 1
    fi

    # test if jinja exists
    if ! command -v jinja &> /dev/null
    then
        echo "jinja could not be found, you must run 'pip install jinja-cli'"
        exit 1
    fi

    # test if ansible exists
    if ! command -v ansible &> /dev/null
    then
        echo "ansible could not be found, you must run 'pip install ansible'"
        exit 1
    fi

    # test if flutter exists
    if ! command -v flutter &> /dev/null
    then
        echo "flutter could not be found, install from https://flutter.dev'"
        exit 1
    fi

    # test if kfp (Kubeflow Pipelines SDK) is installed — required by DeployGNN
    if ! python3 -c "import kfp" &> /dev/null
    then
        echo "kfp Python SDK could not be found."
        echo "Install it with: python3 -m pip install -r gnn/requirements.vertex.txt"
        exit 1
    fi

    # The WEBAPPS_PWD and WEBAPPS_LOGIN used for all web front ends like Gitea, Streamlit NW Agent
    # This is to avoid hard coding the passwd in source code
    if [ -z "${GOOGLE_PROJECT}" ] || [ -z "${GOOGLE_REGION}" ] || \
    [ -z "${GOOGLE_ZONE}" ] || [ -z "${GOOGLE_USER}" ] || [ -z "${GOOGLE_VM_USER}" ] || \
    [ -z "${WEBAPPS_PWD}" ] || [ -z "${WEBAPPS_LOGIN}" ]; then
        cat << EOF
Prior to running the installation script, you must set and export the following environment variables (see ./SetDemoEnv.sh):
    export GOOGLE_PROJECT=<YOUR PROJECT>  # the GCP project name hosting the NW Agent demo (You MUST create it first on GCP)
    export GOOGLE_USER=<GCP_USERNAME>  # the user you authenticate with on GCP. It MUST be the owner of the GOOGLE_PROJECT (e.g. john.doe@mydomain.com)
    export GOOGLE_VM_USER=<GCE_VM_USERNAME>  # the default user name on GCE VMs (usually john_doe_mydomain_com but to be sure create a VM, SSH connect from the web console, type whoami', delete VM)
    export GOOGLE_REGION=<YOUR_REGION>  # the GCP region to host the demo environment (e.g. europe-west1)
    export GOOGLE_ZONE=<YOUR_ZONE>  # the GCP zone in the region to host the demo environment (e.g.europe-west1-c)
    export WEBAPPS_LOGIN=<YOUR_WEB_LOGIN>  # the login name to access web apps like the NW Agent UI or the Gitops Web UI
    export WEBAPPS_PWD=<YOUR_WEB_PWD>  # the password to access the web apps
EOF
        exit 1
    fi

    # Check GCP project is valid
    gcloud projects describe $GOOGLE_PROJECT > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "**ERROR** GCP project $GOOGLE_PROJECT is invalid. Please set the GOOGLE_PROJECT environment variable with a valid project name"
        exit 1
    fi

    # Check GCP region is valid
    gcloud services enable --project=$GOOGLE_PROJECT compute.googleapis.com --quiet
    gcloud compute regions describe $GOOGLE_REGION --project $GOOGLE_PROJECT > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "**ERROR** GCP Region $GOOGLE_REGION is invalid. Please set the GOOGLE_REGION environment variable with a valid region name"
        exit 1
    fi

    # Check GCP zone is valid
    gcloud compute zones describe $GOOGLE_ZONE --project $GOOGLE_PROJECT > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "**ERROR** GCP zone $GOOGLE_ZONE is invalid. Please set the GOOGLE_ZONE environment variable with a valid zone name"
        exit 1
    fi

    # Check GCP user is the owner of GCP project
    result=$(gcloud projects get-iam-policy "$GOOGLE_PROJECT" --flatten=bindings \
        --filter="bindings.members:user:$GOOGLE_USER AND bindings.role=roles/owner" --format="value(bindings.members)")
    if [[ -z "$result" ]]; then
        echo "**ERROR** GCP user $GOOGLE_USER is not the Owner of project $GOOGLE_PROJECT. Please assign 'roles/owner' permission to $GOOGLE_USER."
        exit 1
   fi

    # Make sure the declared Google user is the active one
    active_gcp_user=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2> /dev/null)
    if [[ ! "$active_gcp_user" = "$GOOGLE_USER" ]]; then
        echo "**ERROR** the currently GCP active user ($active_gcp_user) doesn't match GOOGLE_USER ($GOOGLE_USER)"
        echo "Please issue the following command to authenticate with GCP:"
        echo "  gcloud auth login $GOOGLE_USER"
        exit 1
    fi

    # Make sure that the designated project has a billing account. If not all else will fail
    gcloud beta billing projects describe $GOOGLE_PROJECT > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Project $GOOGLE_PROJECT has no billing account. Billing must be enabled prior to activation of GCP services"
        exit 1
    fi

    # Check that we can use non shielded VMs
    shielded_vm_enforced=$(gcloud resource-manager org-policies describe compute.requireShieldedVm --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
    if [ "$shielded_vm_enforced" = "True" ]; then
        echo "compute.requireShieldedVm is enforced on this project. Please change this org Policy to False before proceeding"
        exit 1
    fi

    # Check that we can use external IP addresses (needed by the gitea VM)
    external_ip_access=$(gcloud resource-manager org-policies describe compute.vmExternalIpAccess --project $GOOGLE_PROJECT --effective --format="value(listPolicy.allValues)")
    if [ "$external_ip_access" = "DENY" ]; then
        echo "compute.vmExternalIpAccess is denied on this project. Please change this org Policy to ALLOW before proceeding"
        exit 1
    fi

    # Check that VM can IP forward (needed by the gitea VM)
    vm_can_ip_forward=$(gcloud resource-manager org-policies describe compute.vmCanIpForward --project $GOOGLE_PROJECT --effective --format="value(listPolicy.allValues)")
    if [ "$vm_can_ip_forward" = "DENY" ]; then
        echo "compute.vmCanIpForward is denied on this project. Please change this org Policy to ALLOW before proceeding"
        exit 1
    fi

    # Check that account can be created on service accounts
    svc_account_key_disabled=$(gcloud resource-manager org-policies describe iam.disableServiceAccountKeyCreation --project $GOOGLE_PROJECT --effective --format="value(booleanPolicy.enforced)")
    if [ "$svc_account_key_disabled" = "True" ]; then
        echo "iam.disableServiceAccountKeyCreation is enforced on this project. Please change this org Policy to False before proceeding"
        exit 1
    fi

    # Check that the GCP user (GOOGLE_USER) doesn't have 'admin' or 'user'
    # before the @ sign
    if [[ "$GOOGLE_USER" == "admin@"* ]] || [[ "$GOOGLE_USER" == "user@"* ]]; then
        echo "**ERROR** GCP user $GOOGLE_USER contains 'admin@' or 'user@'."
        echo "This is not allowed both for security reasons and because it"
        echo "conflicts with internal VM user accounts."
        echo "Please use a different GCP user account and don't forget to"
        echo "update the GOOGLE_VM_USER accordingly"
        exit 1
    fi

    # Check that the OS Login flag is set to true at the project level
    # It's needed when the script ssh into the gitea VM
    oslogin_flag=$(gcloud compute project-info describe --project=$GOOGLE_PROJECT --format="value(commonInstanceMetadata.items.enable-oslogin)")
    if [[ $oslogin_flag == "false" ]]; then
        echo "The OS Login flag is false. It must be set to true at the project level"
        exit 1
    fi
    
    echo " all good!"
}

############################################################
# Set the work environment                                 #
############################################################
SetDemoEnv()
{
    echo "Setting your demo environment..."
    # Create a gcloud configuration for this demo project 
    gcloud_config="${GOOGLE_PROJECT}-config"
    gcloud config configurations describe $gcloud_config > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating a specific gcloud config ($gcloud_config) for this project.."
        gcloud config configurations create $gcloud_config
    fi
    gcloud auth application-default set-quota-project $GOOGLE_PROJECT > /dev/null 2>&1
    gcloud config configurations activate $gcloud_config
    gcloud config set core/project $GOOGLE_PROJECT        
    gcloud config set core/account $GOOGLE_USER
    gcloud config set core/disable_usage_reporting False

    # register gcloud as a Docker credential helper
    gcloud auth configure-docker $GOOGLE_REGION-docker.pkg.dev --quiet > /dev/null 2>&1

    export GOOGLE_PROJECT_NUMBER=`gcloud projects describe $GOOGLE_PROJECT --format="value(projectNumber)"`
    if [[ "$GOOGLE_PROJECT_NUMBER" = "" ]]; then
        echo "Could not determine project number. Check that GOOGLE_PROJECT is set properly"
        exit 1
    fi
    export GOOGLE_REPO="networkagent"
    export GOOGLE_NAMESPACE="automation"
    export GOOGLE_SPANNER_INSTANCE="networktopology-instance"
    export GOOGLE_SPANNER_DATABASE="networktopology-db"
    export GOOGLE_GNN_BUCKET="${GOOGLE_PROJECT}-gnn-artifacts"
    export GOOGLE_PIPELINE_ROOT="gs://${GOOGLE_GNN_BUCKET}/pipeline-runs"
    export GOOGLE_ORG_NAME=$(gcloud organizations list --format "value(name)")

    export SINK_NAME="nwoplogs-sink"
    export TOPIC_NAME="nwoplogs-topic"
    export CAPTURE_LOG_FUNCTION="capture_log"
    export NETWORK_OPERATOR="free5gc-operator"
    export GIT_OPERATOR="gitea-operator"

    export FAULT_SINK_NAME="network-fault-sink"
    export FAULT_TOPIC_NAME="network-fault"

    # If running from a Cloud Shell session fix a few problems
    # with preinstalled flutter
    if [[ $GOOGLE_CLOUD_SHELL="true" ]]; then
        git config --global --add safe.directory /google/flutter
        flutter --version >/dev/null # finishes the pre-installation properly
    fi

    # Display current work environment information
    DisplayGCPEnv

    echo "done!"
}

############################################################
# Create keys and manifest files                           #
############################################################
Create()
{
    echo "########################################"
    echo "Setting project to $GOOGLE_PROJECT"
    echo "########################################"
    gcloud config set project $GOOGLE_PROJECT

    # Make sure the active GCP user has proper permissions
    echo "########################################"
    echo "Grant GCP permissions to GCP user: $GOOGLE_USER"
    echo "########################################"
    for role in "roles/logging.logWriter" "roles/spanner.databaseReader" \
                "roles/artifactregistry.admin"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="user:$GOOGLE_USER" --role="$role" --no-user-output-enabled
        # roles/spanner.databaseReader needed for the COlab Notebook to access the graph database
    done

    # enable GCP Services API needed
    echo "########################################"
    echo "Enabling required GCP services API for project $GOOGLE_PROJECT"
    echo "########################################"
    gcloud services enable --project=$GOOGLE_PROJECT artifactregistry.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudbuild.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT cloudfunctions.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT eventarc.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT compute.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT container.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT gkehub.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT anthos.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT run.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT bigquery.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT spanner.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT pubsub.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT logging.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT monitoring.googleapis.com
    # For vertex AI workbench
    gcloud services enable --project=$GOOGLE_PROJECT notebooks.googleapis.com
    # for colab enterprise in addition to compute engine api
    gcloud services enable --project=$GOOGLE_PROJECT aiplatform.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT dataform.googleapis.com
    # For Free5GC london cluster resources management
    gcloud services enable --project=$GOOGLE_PROJECT cloudresourcemanager.googleapis.com
    # For Vertex AI Pipelines (KFP) and Cloud Scheduler (GNN inference trigger)
    gcloud services enable --project=$GOOGLE_PROJECT cloudscheduler.googleapis.com
    gcloud services enable --project=$GOOGLE_PROJECT storage.googleapis.com

    # Configure Cloud Build service account
    echo "########################################"
    echo "Setup Cloud Build service account permissions "
    echo "########################################"
    CLOUD_BUILD_COMPUTE_SVC_ACCOUNT="${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    echo "Granting roles to Cloud Build service account ${CLOUD_BUILD_COMPUTE_SVC_ACCOUNT}..."
    for role in "roles/storage.objectUser" "roles/logging.logWriter" "roles/artifactregistry.writer" "roles/cloudbuild.builds.builder"; do
        echo "$role"
        gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" \
          --role="$role" --no-user-output-enabled
    done

    # Test if google compute ssh keys exist, it not generate them
    if ! test -f google-compute; then
        echo "#############################################################"
        echo "SSH key google-compute does not exist, generating new keys..."
        echo "#############################################################"
        ssh-keygen -o -a 100 -t ed25519 -f google-compute -C networkagent -P ""
    fi

    echo "###################################################"
    echo "Found google-compute ssh keys, copying where needed"
    echo "###################################################"
    cp google-compute operator/src
    cp google-compute.pub operator/src

    echo "Add ssh key to OS login"
    if ! gcloud compute os-login ssh-keys list --project=$GOOGLE_PROJECT | grep -q "$(cat google-compute.pub)"; then
        gcloud compute os-login ssh-keys add --key-file=google-compute.pub --project=$GOOGLE_PROJECT --ttl=100d
    else
        echo "SSH key already exists in OS login."
    fi

    echo "Templating k8s manifest files"

    # grab the public ssh key for templating into VM manifests
    export GOOGLE_SSH_KEY=$(cat google-compute.pub)

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT="networkagent@${GOOGLE_PROJECT}.iam.gserviceaccount.com"
    gcloud iam service-accounts describe $GOOGLE_SERVICE_ACCOUNT > /dev/null 2>&1

    # Create the service account if it doesn't exist
    if [[ $? -ne 0 ]]; then
        echo "########################################"
        echo "No Service Account, trying to create one"
        echo "########################################"
        gcloud iam service-accounts create networkagent --description="Network Agent Service Account" --display-name="Network Agent"
        if [[ $? -ne 0 ]]; then
            echo "Creation of the GKE cluster service account failed. Fix the error and re-run the install command"
            exit 1
        fi

        echo "########################################"
        echo "Waiting for service account to be available..."
        echo "########################################"
        sleep 15
        max_attempts=10
        attempt=0
        while [ $attempt -lt $max_attempts ]; do
            if gcloud iam service-accounts describe $GOOGLE_SERVICE_ACCOUNT > /dev/null 2>&1; then
                echo "Service account $GOOGLE_SERVICE_ACCOUNT is ready!"
                break
            fi
            attempt=$((attempt + 1))
            echo "Waiting for service account to be available... (attempt $attempt/$max_attempts)"
            sleep 10
        done

        if [ $attempt -eq $max_attempts ]; then
            echo "**ERROR** Service account $GOOGLE_SERVICE_ACCOUNT is not available after $max_attempts attempts"
            echo "Please check your GCP project permissions and try again"
            exit 1
        fi

        echo "Granting permissions to the GKE Cluster service account..."
        for role in "roles/editor" "roles/container.admin" "roles/compute.admin" \
            "roles/compute.networkAdmin" "roles/iam.serviceAccountAdmin" "roles/monitoring.metricWriter" \
            "roles/aiplatform.user" "roles/aiplatform.admin" "roles/logging.logWriter" "roles/run.admin" "roles/spanner.databaseUser" \
            "roles/pubsub.editor" "roles/pubsub.subscriber" "roles/monitoring.viewer" \
            "roles/storage.objectAdmin" "roles/iam.serviceAccountTokenCreator"; do
            echo "$role"   
            gcloud projects add-iam-policy-binding $GOOGLE_PROJECT --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" \
              --role="$role" --no-user-output-enabled
        done

        # Allow GOOGLE_USER to act as the networkagent SA (required by Vertex AI
        # pipeline submission and Cloud Run deployments using --service-account)
        echo "Granting $GOOGLE_USER permission to act as $GOOGLE_SERVICE_ACCOUNT..."
        gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
            --member="user:$GOOGLE_USER" \
            --role="roles/iam.serviceAccountUser" \
            --project=$GOOGLE_PROJECT --no-user-output-enabled

    fi

    # if the creadential file doesn't exist or as a zero byte size 
    # then create it
    if [[ ! -f "networkagent.json" ]] || [[ ! -s "networkagent.json" ]]; then
        echo "Creating the application credential file for service account $GOOGLE_SERVICE_ACCOUNT..."
        gcloud iam service-accounts keys create "networkagent.json" --iam-account=$GOOGLE_SERVICE_ACCOUNT
        if [[ $? -ne 0 ]]; then
            echo "Creation of key for service account $GOOGLE_SERVICE_ACCOUNT failed. Exiting."
            exit 1
        fi        
    fi

    # check networkagent.json is not zero size and copy around if it is
    if [[ -s "networkagent.json"  ]]
    then
        echo "#########################"
        echo "copying networkagent.json"
        echo "#########################"
        cp networkagent.json operator/src
        cp networkagent.json tools/src
        cp networkagent.json gnn/src
        cp networkagent.json networkagents/supervisor/src
        cp networkagent.json networkagents/tester/src
        cp networkagent.json networkagents/logs/src
        cp networkagent.json logservices/metricscollector/src
    else
        echo "#############################################################"
        echo "networkagent.json is empty, check your project is allowed to "
        echo "create service account keys or if you have exceeded the number "
        echo "of keys allowed."
        echo "##############################################################"
        exit 1
    fi

    echo "####################################################"
    echo "generating environment yaml files"
    echo "####################################################"
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_PROJECT_NUMBER -E GOOGLE_SERVICE_ACCOUNT environment/logsink.j2 >  environment/logsink.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_SPANNER_DATABASE -E GOOGLE_SPANNER_INSTANCE -E GOOGLE_NAMESPACE environment/spanner.j2 >  environment/spanner.yaml
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE environment/configconnector.j2 > environment/configconnector.yaml

    echo "#######################################################"
    echo "generating networkagent and operator yaml files"
    echo "#######################################################"
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO -E WEBAPPS_LOGIN \
          -E WEBAPPS_PWD -E NETWORK_OPERATOR -E GIT_OPERATOR -E GOOGLE_ORG_NAME operator/deployment.j2 > operator/deployment.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO operator/cloudbuild.j2 > operator/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO tools/cloudbuild.j2 > tools/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO gnn/cloudbuild.j2 > gnn/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagents/tester/cloudbuild.j2 > networkagents/tester/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagents/logs/cloudbuild.j2 > networkagents/logs/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO networkagents/supervisor/cloudbuild.j2 > networkagents/supervisor/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO ui/dashboard/cloudbuild.j2 > ui/dashboard/cloudbuild.yaml
    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO logservices/metricscollector/cloudbuild.j2 > logservices/metricscollector/cloudbuild.yaml

    echo "##############################################################"
    echo "generating GNN pipeline submission script from template"
    echo "##############################################################"
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_PIPELINE_ROOT -E GOOGLE_GNN_BUCKET -E GOOGLE_REPO \
        gnn/src/pipeline/submit_pipeline.j2 > gnn/src/pipeline/submit_pipeline.py
    echo "  -> generated gnn/src/pipeline/submit_pipeline.py"

}

#################################################
# Individual components (Re)deploy functions    #
#################################################
#
# All the fucntions below must be idempotent

DeploySpanner()
{
    # Delete current instance
    echo "Deleting current spanner DB..."
    kubectl delete -f environment/spanner.yaml
    # regenerate the spanner spec file
    echo "Regenerating spanner manifest file..."
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_SPANNER_DATABASE -E GOOGLE_SPANNER_INSTANCE environment/spanner.j2 >  environment/spanner.yaml

    # Setup Spanner and wait until it's ready as we need it to be up and
    # running before the Operator is deployed so as not to miss any
    # creation events in the operator (especially on the networking part)
    # 
    echo "####################################"
    echo "Waiting for Spanner DB to come up..."
    echo "####################################"

    echo "Creating Spanner database ${GOOGLE_SPANNER_INSTANCE}..."
    kubectl apply -f environment/spanner.yaml -l "kind=spanner-instance"
    while [[ $(kubectl get spannerinstance $GOOGLE_SPANNER_INSTANCE -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Spanner instance ready !"

    # Work around because the edition spec is not supported in the manifest file
    # Same for backup schedule updated to None as backup creation make the DB deletion
    # more complex (not needed in this PoC)
    # (See https://b.corp.google.com/issues/372631209)
    echo "Updating Spanner instance to Enterprise Edition"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --edition=ENTERPRISE &
    job_id=$!

    # Sometimes changing to Enterprise edition hangs.. but it actually does the job
    # so simply kill after a timeout
    timeout 2m sh -c "while kill -0 $job_id 2>/dev/null; do sleep 1; done"
    if [ $? -eq 124 ]; then
        kill -TERM $job_id
        # Do nothing for now
    fi

    echo "Updating Spanner instance to no backup schedule"
    gcloud spanner instances update $GOOGLE_SPANNER_INSTANCE --default-backup-schedule-type=NONE

    echo "Creating Spanner database ${GOOGLE_SPANNER_DATABASE}..."
    kubectl apply -f environment/spanner.yaml -l "kind=spanner-database"
    while [[ $(kubectl get spannerdatabase $GOOGLE_SPANNER_DATABASE -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Spanner database ready !"
}

############################################################
# Start GKE, config connector and customer sites           #
############################################################
Start()
{
    echo "####################################"
    echo "Starting the network agent services"
    echo "####################################"

   # Create artifact repository
    echo "###########################"
    echo "Create Artifact Repository "
    echo "############################"
    gcloud artifacts repositories describe $GOOGLE_REPO --location=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        gcloud artifacts repositories create $GOOGLE_REPO --repository-format=docker --location=$GOOGLE_REGION --description="Network Agent Repository" --quiet
    fi

    # check if SERVICE ACCOUNT exists
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`
    echo "GKE Cluster Service Account: $GOOGLE_SERVICE_ACCOUNT"

    # Create the service account if it doesnt exist
    if [ -z "${GOOGLE_SERVICE_ACCOUNT}" ]; then
        echo "Cannot find the service account - run this script with the -c option first"
        exit 1
    fi

    echo "#####################"
    echo "Creating mgmt network"
    echo "#####################"
    (gcloud compute networks describe mgmt > /dev/null 2>&1) || \
        gcloud compute networks create mgmt --subnet-mode=custom
    (gcloud compute networks subnets describe mgmt-subnet --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute networks subnets create mgmt-subnet --network=mgmt --range=10.0.100.0/24 --region=$GOOGLE_REGION
    (gcloud compute firewall-rules describe mgmt-ingress > /dev/null 2>&1) || \
        gcloud compute firewall-rules create mgmt-ingress --network=mgmt --allow=tcp,udp,icmp --source-ranges="0.0.0.0/0"
    (gcloud compute routers describe mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers create mgmt --network mgmt --region=$GOOGLE_REGION
    (gcloud compute routers nats describe mgmt --router=mgmt --region=$GOOGLE_REGION > /dev/null 2>&1) || \
        gcloud compute routers nats create mgmt --router=mgmt --region=$GOOGLE_REGION --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --enable-logging

    # create the GKE cluster
    echo "###################################################"
    echo "Creating GKE cluster - this will take a few minutes"
    echo "###################################################"

    # NOTE: release channel to None is the only way to prevent GKE control plane 
    # auto upgrade
    (gcloud container clusters describe networkautomation --zone=$GOOGLE_ZONE > /dev/null 2>&1) || \
    gcloud container clusters create networkautomation \
        --no-enable-autoupgrade \
        --release-channel=None \
        --addons ConfigConnector \
        --enable-ip-alias \
        --service-account $GOOGLE_SERVICE_ACCOUNT\
        --scopes "default,storage-full,cloud-platform,bigquery" \
        --workload-pool $GOOGLE_PROJECT.svc.id.goog \
        --zone $GOOGLE_ZONE\
        --node-locations $GOOGLE_ZONE \
        --num-nodes 2 \
        --machine-type "n1-standard-4" \
        --enable-fleet \
        --network mgmt \
        --subnetwork mgmt-subnet 

    if [ $? -ne 0 ]; then
      echo "ERROR while creating the networkautomation cluster. Exiting"
    fi

    # disable auto upgrade
    gcloud container node-pools update default-pool \
    --cluster networkautomation \
    --location $GOOGLE_ZONE \
    --no-enable-autoupgrade

    # On glinux machines gcloud components cannot be installed
    # through gcloud. apt must be used instead
    if [[ `uname -v` =~ "rodete" ]]; then
        for p in kubectl google-cloud-cli-gke-gcloud-auth-plugin; do
            (dpkg -s $p &> /dev/null) || sudo apt install $p
        done
    else
        gcloud components install kubectl
        gcloud components install kpt
        gcloud components install gke-gcloud-auth-plugin # for GKE 1.26+
    fi

    gcloud container clusters get-credentials networkautomation --region=$GOOGLE_ZONE

    # Give the Kubernetes ServiceAccount access to impersonate the IAM service account
    # See https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#authenticating_to
    gcloud iam service-accounts add-iam-policy-binding \
    $GOOGLE_SERVICE_ACCOUNT \
        --member="serviceAccount:$GOOGLE_PROJECT.svc.id.goog[cnrm-system/cnrm-controller-manager]" \
        --role="roles/iam.workloadIdentityUser"

    # Setup the GKE namespace we'll be using
    kubectl create namespace $GOOGLE_NAMESPACE
    kubectl annotate namespace $GOOGLE_NAMESPACE cnrm.cloud.google.com/project-id=$GOOGLE_PROJECT
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE

    # create and attach operator service account to networkagent service account for workload identity
    kubectl create serviceaccount networkoperator-account --namespace $GOOGLE_NAMESPACE
    gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
        --role roles/iam.workloadIdentityUser \
        --member "serviceAccount:$GOOGLE_PROJECT.svc.id.goog[$GOOGLE_NAMESPACE/networkoperator-account]"

    # Grant access permissions to the GKE cluster
    # See https://cloud.google.com/spanner/docs/connect-gke-cluster
    # For an unknown reason granting to the service account (line below) doesn't work...
    # gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    #  --member="principal://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/subject/ns/${GOOGLE_NAMESPACE}/sa/${GOOGLE_SERVICE_ACCOUNT}" \
    #  --role=roles/spanner.databaseUser --condition=None
    #
    # So here is a variant that grants the spanner permission to all service accounts
    # in the designated namespace. This one works.
    #
    # Same to give the operator access to the Vertex AI prediction API
    for role in "roles/spanner.databaseUser" "roles/aiplatform.user" "roles/monitoring.metricWriter"; do
        gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
            --member="principalSet://iam.googleapis.com/projects/${GOOGLE_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GOOGLE_PROJECT}.svc.id.goog/namespace/${GOOGLE_NAMESPACE}" \
            --role="$role" --condition=None --no-user-output-enabled
    done   
    echo "done."

    # Setup the one config connector we will be using 
    kubectl apply -f environment/configconnector.yaml

    echo "################################################"
    echo "Waiting for cnrm-controller-manager-0 to start... "
    echo "################################################"

    # kubectl wait -n cnrm-system --for=condition=Ready pod cnrm-controller-manager-0
    while [[ $(kubectl get pods -n cnrm-system cnrm-controller-manager-0 -o 'jsonpath={..status.conditions[?(@.type=="Ready")].status}' 2>/dev/null) != "True" ]]; do
        sleep 20
        echo "sleeping for 20 secs..."
    done
    echo "Ready !"

    # Start ConfigSync operator in cluster
    gcloud beta container fleet config-management enable --project=$GOOGLE_PROJECT
    gcloud beta container fleet config-management apply --membership=networkautomation --config=./environment/configsync.yaml --project=$GOOGLE_PROJECT

    DeploySpanner

    DeployOperator


    # I tried hard to create the Log Sink to BQ or PubSub with Config Connector
    # to no avail. I couldn't fix the dataset or topic access permission problem :-(
    # That's why it is created from the shell script
    # kubectl apply -f environment/logsink.yaml
    DeployLogCapture

    # start the network and git repos
    kubectl apply -f environment/networkvm.yaml

    echo "############################################################################"
    echo "Waiting for VyosVM networkvm to be ready (this can take up to 10 minutes)..."
    echo "############################################################################"
    while [[ $(kubectl get vyosvm networkvm -n $GOOGLE_NAMESPACE -o 'jsonpath={..status.phase}' 2>/dev/null) != "Ready" ]]; do
        sleep 60
        echo "waiting for networkvm to be ready, sleeping for 60 secs..."
    done
    echo "VyosVM networkvm is Ready!"

    # kubectl apply -f environment/bigquery.yaml

    # DeployGit
}

############################################################
# Delete GKE, config connector and customer sites          #
############################################################
Delete()
{
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]]; then
        read -p "Are you sure you want to delete the environment configuration (y/n)? " choice
        case "$choice" in 
            y|Y ) echo "proceeding to delete environment configuration";;
            n|N ) exit 0;;
            * ) echo "please enter y/n";;
        esac
    elif [[ $NO_FLAG == "y" ]]; then
        echo "Skipping environment configuration deletion (NO_FLAG set)"
        exit 0
    fi

    echo "######################"
    echo "Deleting Artifact Repo"
    echo "######################"
    (gcloud artifacts repositories describe $GOOGLE_REPO --location=$GOOGLE_REGION > /dev/null 2>&1) && \
    gcloud artifacts repositories delete $GOOGLE_REPO --location=$GOOGLE_REGION --quiet

    echo "#######################################"
    echo "Deleting environment manifests and keys"
    echo "#######################################"
    rm -f operator/deployment.yaml \
        operator/cloudbuild.yaml \
        operator/src/google-compute* \
        operator/src/networkagent.json \
        \
        tools/src/networkagent.json \
        tools/cloudbuild.yaml \
        gnn/src/networkagent.json \
        gnn/cloudbuild.yaml \
        networkagents/supervisor/src/networkagent.json \
        networkagents/supervisor/cloudbuild.yaml \
        networkagents/tester/src/networkagent.json \
        networkagents/tester/cloudbuild.yaml \
        networkagents/logs/src/networkagent.json \
        networkagents/logs/cloudbuild.yaml \
        \
        ui/dashboard/cloudbuild.yaml \
        \
        logservices/metricscollector/src/networkagent.json \
        logservices/metricscollector/cloudbuild.yaml \
        \
        environment/bigquery.yaml \
        environment/spanner.yaml \
        environment/configconnector.yaml \
        environment/logsink.yaml \
        \
        networkagent.json \
        google-compute* \
        gnn/src/pipeline/submit_pipeline.py

}

############################################################
# Kill the environment resources                           #
############################################################
Kill()
{
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]]; then
        read -p "Are you sure you want to kill the environment(y/n)? " choice
        case "$choice" in 
            y|Y ) echo "proceeding to kill the environment";;
            n|N ) exit 0;;
            * ) echo "please enter y/n";;
        esac
    elif [[ $NO_FLAG == "y" ]]; then
        echo "Skipping environment kill (NO_FLAG set)"
        exit 0
    fi
    echo "##############################################"
    echo "Killing the environment - will take a few mins"
    echo "##############################################"
    kubectl config set-context --current --namespace $GOOGLE_NAMESPACE
    
    kubectl delete -f environment/git.yaml
    # kubectl delete -f environment/bigquery.yaml
    kubectl delete -f environment/networkvm.yaml
    kubectl delete -f environment/spanner.yaml
    # Sometimes kopf finalizers are not removed from the network resources
    # and the kubectl command below hangs for ever. So clear the finalizers after
    # a certain timeout if it is still hanging 

    # Delete log sink, pub/sub topic and log processing Cloud Function
    gcloud logging sinks delete $SINK_NAME --quiet
    gcloud pubsub topics delete $TOPIC_NAME --quiet
    gcloud functions delete $CAPTURE_LOG_FUNCTION --region=$GOOGLE_REGION  --quiet

    # Delete Vertex AI GNN resources
    echo "Cleaning up Vertex AI GNN resources..."
    
    # Get all endpoints and undeploy models first
    ENDPOINTS=$(gcloud ai endpoints list --region=$GOOGLE_REGION --format="value(name)" 2>/dev/null | grep "gnn-endpoint" || true)
    if [ -n "$ENDPOINTS" ]; then
        for endpoint in $ENDPOINTS; do
            echo "Undeploying models from endpoint: $endpoint"
            DEPLOYED_MODELS=$(gcloud ai endpoints describe $endpoint --region=$GOOGLE_REGION --format="value(deployedModels[].id)" 2>/dev/null || true)
            if [ -n "$DEPLOYED_MODELS" ]; then
                for model_id in $DEPLOYED_MODELS; do
                    echo "  Undeploying model: $model_id"
                    gcloud ai endpoints undeploy-model $endpoint --region=$GOOGLE_REGION --deployed-model-id=$model_id --quiet 2>/dev/null || true
                done
            fi
            echo "Deleting endpoint: $endpoint"
            gcloud ai endpoints delete $endpoint --region=$GOOGLE_REGION --quiet 2>/dev/null || true
        done
    fi
    
    # Delete Vertex AI models registered by the KFP pipeline
    MODELS=$(gcloud ai models list --region=$GOOGLE_REGION --format="value(name)" 2>/dev/null | grep -E "gnn-(dgat|hetgnn|stgnn)" || true)
    if [ -n "$MODELS" ]; then
        for model in $MODELS; do
            echo "Deleting Vertex AI model: $model"
            gcloud ai models delete $model --region=$GOOGLE_REGION --quiet 2>/dev/null || true
        done
    fi
    
    # Cancel any running Vertex AI Pipeline runs (GNN training pipeline)
    PIPELINE_RUNS=$(gcloud ai pipeline-jobs list --region=$GOOGLE_REGION \
        --filter="displayName:gnn-training-pipeline AND state:PIPELINE_STATE_RUNNING" \
        --format="value(name)" 2>/dev/null || true)
    if [ -n "$PIPELINE_RUNS" ]; then
        for run in $PIPELINE_RUNS; do
            echo "Cancelling running pipeline job: $run"
            gcloud ai pipeline-jobs cancel "$run" --region=$GOOGLE_REGION --quiet 2>/dev/null || true
        done
    fi

    # Delete Cloud Scheduler GNN inference trigger
    echo "Deleting Cloud Scheduler job gnn-inference-scheduler..."
    gcloud scheduler jobs delete gnn-inference-scheduler \
        --location=$GOOGLE_REGION --quiet 2>/dev/null || true

    # Delete GNN inference Cloud Run Job
    echo "Deleting Cloud Run Job gnn-infer..."
    gcloud run jobs delete gnn-infer --region=$GOOGLE_REGION --quiet 2>/dev/null || true

    gcloud run services delete networktools --region=$GOOGLE_REGION --quiet
    gcloud run services delete testagent --region=$GOOGLE_REGION --quiet
    gcloud run services delete logsagent --region=$GOOGLE_REGION --quiet
    gcloud run services delete network-agent-supervisor --region=$GOOGLE_REGION --quiet
    gcloud run services delete network-dashboard --region=$GOOGLE_REGION --quiet

    # delete pubsub subscription and topic
    gcloud beta run worker-pools delete metricscollector --region=$GOOGLE_REGION --quiet

    echo "#####################"
    echo "Deleting GKE Cluster"
    echo "#####################"
    gcloud container clusters delete networkautomation --region=$GOOGLE_ZONE --quiet
    kubectl config unset current-context

    echo "#####################"
    echo "Deleting mgmt network"
    echo "#####################"
    gcloud compute routers delete mgmt --region=$GOOGLE_REGION --quiet
    for r in $(gcloud compute firewall-rules list --filter="name~'^mgmt-'" --format="value(name)" --project=$GOOGLE_PROJECT); do
        gcloud compute firewall-rules delete $r --project=$GOOGLE_PROJECT --quiet
    done
    gcloud compute networks subnets delete mgmt-subnet --region=$GOOGLE_REGION --quiet
    gcloud compute networks delete mgmt --project=$GOOGLE_PROJECT --quiet

}

############################################################
# Build and deploy the operator                            #
############################################################
DeployOperator()
{

    jinja -E GOOGLE_VM_USER -E GOOGLE_PROJECT -E GOOGLE_PROJECT_NUMBER -E GOOGLE_REGION -E GOOGLE_ZONE -E GOOGLE_REPO -E WEBAPPS_LOGIN \
          -E WEBAPPS_PWD -E NETWORK_OPERATOR -E GIT_OPERATOR -E GOOGLE_ORG_NAME operator/deployment.j2 > operator/deployment.yaml

    echo "######################################"
    echo "Deploy the Operator, networks and CRDs"
    echo "######################################"

    if ! test -f operator/deployment.yaml; then
        echo "No deployment.yaml found - you can generate by running ./install.sh -c"
        exit 1
    fi

    cd operator
    IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networkoperator:latest"
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
        read -p "Operator image already exists. Rebuild? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
        fi
    elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
        echo "Skipping operator image rebuild (NO_FLAG set)"
    else
        gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    fi
    kubectl delete -f deployment.yaml
    kubectl apply -f deployment.yaml
    echo "Waiting for deployment to be ready..."
    kubectl rollout status deployment $GIT_OPERATOR -n $GOOGLE_NAMESPACE --timeout=120s
    kubectl rollout status deployment $NETWORK_OPERATOR -n $GOOGLE_NAMESPACE --timeout=120s

    # load the crds
    kubectl apply -f config/gitea.yaml
    kubectl apply -f config/vyosvm.yaml
    kubectl apply -f config/device.yaml
    kubectl apply -f config/traffic.yaml
    kubectl apply -f config/free5gc/
    kubectl apply -f config/transport/

    cd ..
}

############################################################
# Build and deploy the gitea server and git repo           #
############################################################
DeployGit()
{
    # Delete current instance
    echo "Deleting current spanner DB..."
    kubectl delete -f environment/git.yaml

    echo "#####################################"
    echo "Create The gitea server and git repo"
    echo "#####################################"
    kubectl apply -f environment/git.yaml
    # Wit fir the gitea server to be up and running
    while [[ $(kubectl get gitea gitea -o 'jsonpath={..status.create_gitea.status}' 2>/dev/null) != "Running" ]]; do
        sleep 60
        echo "waiting for Gitea to be ready, sleeping for 60 secs..."
    done

    # Say how to access the gitea server
    gitea_host=$(kubectl get gitea gitea -o 'jsonpath={..status.create_gitea.external_ip_address}')
    echo -e "\nGitea server is available at:\n\thttps://$gitea_host:3000/explore/repos\n"
    echo "You can clone the git repos as follows (username/password = ${WEBAPPS_LOGIN}/${WEBAPPS_PWD})"
    echo "  git clone https://$gitea_host:3000/${WEBAPPS_LOGIN}/network -c http.sslVerify=false"
}

############################################################
# Build and deploy the log capture                         #
############################################################
DeployMetricsCollector()
{
    echo "##############################################################"
    echo "Deploy VYOS metrics collector from Cloud Monitoring to Spanner"
    echo "##############################################################"
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`

    cd logservices/metricscollector
    IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/metricscollector:latest"
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
        read -p "Metrics collector image already exists. Rebuild? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
        fi
    elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
        echo "Metrics collector image already exists - not building the image (NO_FLAG set)"
    elif [[ $NO_FLAG != "y" ]]; then
        gcloud builds submit --region=$GOOGLE_REGION --config cloudbuild.yaml
    fi
    gcloud beta run worker-pools deploy metricscollector \
    --image $GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/metricscollector:latest \
    --region $GOOGLE_REGION \
    --instances 1 \
    --cpu 1 \
    --memory 1Gi \
    --service-account $GOOGLE_SERVICE_ACCOUNT \
    --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
    --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
    --update-env-vars GOOGLE_ZONE=$GOOGLE_ZONE \
    --update-env-vars GOOGLE_SPANNER_INSTANCE=$GOOGLE_SPANNER_INSTANCE \
    --update-env-vars GOOGLE_SPANNER_DATABASE=$GOOGLE_SPANNER_DATABASE \
    --update-env-vars POLL_INTERVAL=15 \
    --update-env-vars NETWORK_AGENT_FILE="/app/networkagent.json" \
    --update-env-vars GOOGLE_APPLICATION_CREDENTIALS="/app/networkagent.json"
    cd ../..
    sleep 5
}


############################################################
# Build and deploy the log capture                         #
############################################################
DeployLogCapture()
{
    echo "#####################################"
    echo "Create Operator Log Sink and capture"
    echo "#####################################"

    # Create a  network log sink to bigquery and collect
    # logs from the network operator
    #
    # ==> Sink to BQ dataset
    #bq mk --location=$GOOGLE_REGION --description="Network operator logs" --dataset nwoplogs
    #gcloud logging sinks create nwoplogs-sink bigquery.googleapis.com/projects/${GOOGLE_PROJECT}/datasets/nwoplogs \
    #  --log-filter='resource.labels.project_id="networkagent-434609" AND resource.type="k8s_container" \
    #      AND resource.labels.cluster_name="networkautomation" AND resource.labels.namespace_name="automation"  \
    #      AND labels.python_logger!="kopf._cogs.clients.watching"' \
    #  --description="Network operator logs"
    #gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
    #    --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    #    --role="roles/bigquery.dataEditor" --condition=None --no-user-output-enabled
    #

    # ==> Sink to PubSub topic
    # Create the pubsub topic if it doesn't exist yet
    gcloud pubsub topics describe $TOPIC_NAME > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating Pub/Sub topic '${TOPIC_NAME}'..."
        gcloud pubsub topics create $TOPIC_NAME --project=${GOOGLE_PROJECT}
    else
        echo "Pub/Sub topic '${TOPIC_NAME}' already exists..."
    fi

    # Create the logging sink if it doesn't exist yet
    gcloud logging sinks describe $SINK_NAME > /dev/null 2>&1
    # The log sink filter captures:
    # 1) all logs from the network operator except kopf logs
    # and also
    # 2) the error logs from the config manager in case something goes
    #    wrong when GCP resources are instantiated or deleted
    # 3) all logs from the vyos router containers (hosted on the networkvm VM)
    log_filter=$(cat <<EOF
        resource.labels.project_id=${GOOGLE_PROJECT} AND 
        (((resource.labels.container_name=${NETWORK_OPERATOR} AND labels.python_logger!=kopf._cogs.clients.watching) OR
            logName="projects/${GOOGLE_PROJECT}/logs/gcplogs-docker-driver" OR
            labels.python_logger=UERANSIMHEALTH OR labels.python_logger=CRITICALSERVICEERROR) OR 
            logName="projects/${GOOGLE_PROJECT}/logs/vyos_syslog")
EOF
    )
    if [[ $? -ne 0 ]]; then
        echo "Creating Logging sink '${SINK_NAME}'..."
        gcloud logging sinks create $SINK_NAME pubsub.googleapis.com/projects/${GOOGLE_PROJECT}/topics/${TOPIC_NAME} \
            --description="Network operator logs sink" \
            --log-filter="${log_filter}"
    else
        # Update the log filter in case we updated it in the meantime
        echo "Update existing sink '${SINK_NAME}' with filter..."
        gcloud logging sinks update $SINK_NAME --log-filter="${log_filter}"
    fi

    # Grant the Cloud Logging service account used by the Log sink the right to publish 
    # log entries to the PubSub topic
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-logging.iam.gserviceaccount.com" \
        --role="roles/pubsub.publisher" --condition=None --no-user-output-enabled

    # Give the eventarc service account (by default the compute service account of the
    # project) the permission to invoke the cloud run function
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/run.invoker" --condition=None --no-user-output-enabled
    # Give the Cloud Function service account (by default the compute service account of the
    # project) the permission to use (read/write) Spanner
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/spanner.databaseUser" --condition=None --no-user-output-enabled   
    # Give the Cloud Function service account (by default the compute service account of the
    # project) the permission to use Vertex AI (e.g. embedding generation)
    gcloud projects add-iam-policy-binding ${GOOGLE_PROJECT} \
        --member="serviceAccount:${GOOGLE_PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/aiplatform.user" --condition=None --no-user-output-enabled   

    # Create the Cloud Run function that receives the eventarc
    # events from pub/pub  and feed the Spanner DB
    echo "Deploying Log capture function..."
    gcloud functions deploy $CAPTURE_LOG_FUNCTION --source ./logservices/logcollector --runtime python312 \
      --trigger-topic $TOPIC_NAME  --entry-point=capture_log --memory=512MB \
      --project=$GOOGLE_PROJECT --region=$GOOGLE_REGION
}

############################################################
# Build and deploy the GNN services (Vertex AI Pipelines)  #
############################################################
DeployGNN()
{
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`
    TRAIN_VERTEX_IMAGE="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/traingnn-vertex:latest"
    INFER_IMAGE="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/infergnn-cloudrun:latest"

    # ── 1. Build all GNN Docker images via Cloud Build ─────────────────────────
    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && \
       $(gcloud artifacts docker images describe $TRAIN_VERTEX_IMAGE >/dev/null 2>&1); then
        read -p "GNN Vertex AI images already exist. Rebuild? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config=gnn/cloudbuild.yaml .
        fi
    elif [[ $NO_FLAG == "y" ]] && \
         $(gcloud artifacts docker images describe $TRAIN_VERTEX_IMAGE >/dev/null 2>&1); then
        echo "GNN images already exist - not rebuilding (NO_FLAG set)"
    else
        echo "Building all GNN Docker images (Vertex AI training + serving + Cloud Run inference)..."
        gcloud builds submit --region=$GOOGLE_REGION --config=gnn/cloudbuild.yaml .
    fi

    # ── 2. Create GNN artefacts GCS bucket ─────────────────────────────────────
    echo "Ensuring GNN artefacts bucket gs://$GOOGLE_GNN_BUCKET exists..."
    gcloud storage buckets describe gs://$GOOGLE_GNN_BUCKET > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        gcloud storage buckets create gs://$GOOGLE_GNN_BUCKET \
            --location=$GOOGLE_REGION \
            --project=$GOOGLE_PROJECT \
            --uniform-bucket-level-access
        echo "  Created gs://$GOOGLE_GNN_BUCKET"
    fi
    # Grant service account write access to the bucket
    gcloud storage buckets add-iam-policy-binding gs://$GOOGLE_GNN_BUCKET \
        --member="serviceAccount:$GOOGLE_SERVICE_ACCOUNT" \
        --role="roles/storage.objectAdmin" > /dev/null 2>&1

    # Grant the Vertex AI Service Agent read access to Artifact Registry so it
    # can pull the GNN training/serving container images when creating pipeline jobs.
    echo "Granting Vertex AI Service Agent read access to Artifact Registry..."
    gcloud artifacts repositories add-iam-policy-binding $GOOGLE_REPO \
        --location=$GOOGLE_REGION \
        --member="serviceAccount:service-${GOOGLE_PROJECT_NUMBER}@gcp-sa-aiplatform-cc.iam.gserviceaccount.com" \
        --role="roles/artifactregistry.reader" \
        --project=$GOOGLE_PROJECT > /dev/null 2>&1

    # ── 3. Generate submit_pipeline.py from Jinja template ─────────────────────
    echo "Regenerating gnn/src/pipeline/submit_pipeline.py..."
    jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_PIPELINE_ROOT -E GOOGLE_GNN_BUCKET -E GOOGLE_REPO \
        gnn/src/pipeline/submit_pipeline.j2 > gnn/src/pipeline/submit_pipeline.py

    # ── 3b. Ensure current user can act as the networkagent SA ─────────────────
    # Required for job.submit(service_account=...) in submit_pipeline.py
    echo "Ensuring $GOOGLE_USER can act as $GOOGLE_SERVICE_ACCOUNT..."
    gcloud iam service-accounts add-iam-policy-binding $GOOGLE_SERVICE_ACCOUNT \
        --member="user:$GOOGLE_USER" \
        --role="roles/iam.serviceAccountUser" \
        --project=$GOOGLE_PROJECT --no-user-output-enabled

    # ── 4. Install pipeline submission deps and submit the training pipeline ───
    # Use python3 -m pip so the packages land in the same interpreter that will
    # run submit_pipeline.py.  requirements.vertex.txt is the single source of
    # truth for KFP SDK + Vertex AI pipeline deps.
    echo "Installing pipeline submission dependencies (requirements.vertex.txt)..."
    python3 -m pip install --quiet -r gnn/requirements.vertex.txt

    echo "Submitting GNN training pipeline to Vertex AI Pipelines..."
    # Use the networkagent SA key so the Vertex AI SDK authenticates as the SA rather
    # than falling back to gcloud ADC (which may be a different Google account).
    # The SA has storage.objectAdmin on the bucket and can act as itself.
    GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/gnn/src/networkagent.json" \
    python3 gnn/src/pipeline/submit_pipeline.py \
        --project "$GOOGLE_PROJECT" \
        --region "$GOOGLE_REGION" \
        --pipeline-root "$GOOGLE_PIPELINE_ROOT" \
        --spanner-instance "$GOOGLE_SPANNER_INSTANCE" \
        --spanner-database "$GOOGLE_SPANNER_DATABASE" \
        --gcs-bucket "$GOOGLE_GNN_BUCKET" \
        --service-account "$GOOGLE_SERVICE_ACCOUNT"
    echo "  Pipeline submitted — monitor at: https://console.cloud.google.com/vertex-ai/pipelines?project=$GOOGLE_PROJECT"

    # ── 5. Deploy GNN inference Cloud Run Job ──────────────────────────────────
    echo "Deploying GNN inference Cloud Run Job (gnn-infer)..."
    gcloud run jobs deploy gnn-infer \
        --image "$INFER_IMAGE" \
        --region "$GOOGLE_REGION" \
        --service-account "$GOOGLE_SERVICE_ACCOUNT" \
        --set-env-vars "GCS_BUCKET_NAME=$GOOGLE_GNN_BUCKET" \
        --set-env-vars "SPANNER_INSTANCE=$GOOGLE_SPANNER_INSTANCE" \
        --set-env-vars "SPANNER_DATABASE=$GOOGLE_SPANNER_DATABASE" \
        --set-env-vars "GOOGLE_PROJECT=$GOOGLE_PROJECT" \
        --set-env-vars "GOOGLE_REGION=$GOOGLE_REGION" \
        --max-retries 2 \
        --task-timeout 300 \
        --memory 2Gi \
        --cpu 2

    # ── 6. Create Cloud Scheduler job to trigger inference every 60 seconds ───
    # Cloud Scheduler minimum granularity is 1 minute (cron "* * * * *")
    INFER_JOB_URI="https://run.googleapis.com/v2/projects/$GOOGLE_PROJECT/locations/$GOOGLE_REGION/jobs/gnn-infer:run"
    echo "Creating/updating Cloud Scheduler job gnn-inference-scheduler..."
    gcloud scheduler jobs describe gnn-inference-scheduler --location=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        gcloud scheduler jobs create http gnn-inference-scheduler \
            --location "$GOOGLE_REGION" \
            --schedule "* * * * *" \
            --uri "$INFER_JOB_URI" \
            --http-method POST \
            --oauth-service-account-email "$GOOGLE_SERVICE_ACCOUNT" \
            --message-body '{}' \
            --attempt-deadline 320s \
            --description "Trigger GNN inference Cloud Run Job every 60 seconds"
    else
        echo "  Scheduler job already exists — updating schedule..."
        gcloud scheduler jobs update http gnn-inference-scheduler \
            --location "$GOOGLE_REGION" \
            --schedule "* * * * *" \
            --uri "$INFER_JOB_URI"
    fi
    echo "  Cloud Scheduler job gnn-inference-scheduler configured."
    echo ""
    echo "GNN deployment complete!"
    echo "  Pipeline:   https://console.cloud.google.com/vertex-ai/pipelines?project=$GOOGLE_PROJECT"
    echo "  Infer job:  https://console.cloud.google.com/run/jobs?project=$GOOGLE_PROJECT"
    echo "  Scheduler:  https://console.cloud.google.com/cloudscheduler?project=$GOOGLE_PROJECT"
}

############################################################
# Build and deploy the networkagent                        #
############################################################
Networkagent()
{
    # check if flutter installed locally for now
    if ! command -v flutter &> /dev/null
    then
        echo "flutter could not be found in your path, you must install it"
        exit 1
    fi

    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`

    agent_processed=false

    # deploy the mcp tools
    if [[ "$AGENT_NAMES" == "all" ]] || [[ "$AGENT_NAMES" == *"networktools"* ]]; then
        agent_processed=true
        IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networktools:latest"
        if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            read -p "Networktools image already exists. Rebuild? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                gcloud builds submit --region=$GOOGLE_REGION --config tools/cloudbuild.yaml .
            fi
        elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            echo "Networktools image already exists - not building the image (NO_FLAG set)"
        elif [[ $NO_FLAG != "y" ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config tools/cloudbuild.yaml .
        fi
        gcloud run deploy networktools \
        --image $IMAGE_URI \
        --region $GOOGLE_REGION \
        --min 1 \
        --memory 1Gi \
        --service-account $GOOGLE_SERVICE_ACCOUNT \
        --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
        --update-env-vars GOOGLE_ZONE=$GOOGLE_ZONE \
        --update-env-vars WEBAPPS_PWD=${WEBAPPS_PWD} \
        --update-env-vars WEBAPPS_LOGIN=${WEBAPPS_LOGIN} \
        --update-env-vars GOOGLE_APPLICATION_CREDENTIALS="/agent/networkagent.json" \
        --allow-unauthenticated 
        sleep 5
    fi

    TOOLS_URL=$(gcloud run services describe networktools --region=$GOOGLE_REGION --format="value(status.url)")
    if [[ $? -ne 0 ]]; then
        echo
        echo "**ERROR** cannot determine the networktools URL required by other agents. Exiting"
        exit 1
    fi

    # deploy supervisor
    if [[ "$AGENT_NAMES" == "all" ]] || [[ "$AGENT_NAMES" == *"supervisor"* ]]; then
        agent_processed=true
        IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/networksupervisor:latest"
        if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            read -p "Supervisor agent image already exists. Rebuild? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/supervisor/cloudbuild.yaml .
            fi
        elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            echo "Supervisor agent image already exists - not building the image (NO_FLAG set)"
        elif [[ $NO_FLAG != "y" ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/supervisor/cloudbuild.yaml .
        fi
        gcloud run deploy network-agent-supervisor \
        --image $IMAGE_URI \
        --region $GOOGLE_REGION \
        --service-account $GOOGLE_SERVICE_ACCOUNT \
        --timeout=3600 \
        --min 1 \
        --memory 1Gi \
        --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars GOOGLE_REGION=$GOOGLE_REGION \
        --update-env-vars GOOGLE_CLOUD_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars GOOGLE_CLOUD_LOCATION=$GOOGLE_REGION \
        --update-env-vars GOOGLE_GENAI_USE_VERTEXAI=1 \
        --update-env-vars GOOGLE_APPLICATION_CREDENTIALS="/agent/networkagent.json" \
        --update-env-vars AGENT_MCP_TOOLS_ADDRESS=$TOOLS_URL/sse \
        --allow-unauthenticated 

        # Check if allUsers access is already granted. 
        # If not Allow allUsers to invoke the Cloud Run service
        gcloud run services get-iam-policy network-agent-supervisor --region=$GOOGLE_REGION --project=$GOOGLE_PROJECT \
            --format="value(bindings.members)" 2>&1 | fgrep -q allUsers
        if [ $? -ne 0 ]; then
            gcloud run services add-iam-policy-binding network-agent-supervisor --member='allUsers' --role='roles/run.invoker' \
                --region=$GOOGLE_REGION --project=$GOOGLE_PROJECT >/dev/null 2>&1
            if [ $? -eq 1 ]; then
                echo "ERROR : could not setup access for all Users on the Cloud Run service network-agent-supervisor"
                echo "You must probably disable the Domain Restricted Sharing policy of your domain."
                echo "Then run this command again and re-enable the DRS policy"
                exit 1
            fi
        fi
    fi

    # deploy the tester agent
    if [[ "$AGENT_NAMES" == "all" ]] || [[ "$AGENT_NAMES" == *"test"* ]]; then 
        agent_processed=true
        IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/testagent:latest"
        if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            read -p "Tester agent image already exists. Rebuild? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/tester/cloudbuild.yaml .
            fi
        elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            echo "Tester agent image already exists - not building the image (NO_FLAG set)"
        elif [[ $NO_FLAG != "y" ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/tester/cloudbuild.yaml .
        fi
        gcloud run deploy testagent \
        --image $IMAGE_URI \
        --region $GOOGLE_REGION \
        --service-account $GOOGLE_SERVICE_ACCOUNT \
        --min 1 \
        --update-env-vars GOOGLE_CLOUD_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars GOOGLE_CLOUD_LOCATION=$GOOGLE_REGION \
        --update-env-vars GOOGLE_GENAI_USE_VERTEXAI=1 \
        --update-env-vars AGENT_MCP_TOOLS_ADDRESS=$TOOLS_URL/sse \
        --update-env-vars GOOGLE_APPLICATION_CREDENTIALS="/agent/networkagent.json" \
        --allow-unauthenticated 
    fi

    # deploy the logs agent
    if [[ "$AGENT_NAMES" == "all" ]] || [[ "$AGENT_NAMES" == *"logs"* ]]; then
        agent_processed=true
        IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/logsagent:latest"
        if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            read -p "Logs agent image already exists. Rebuild? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/logs/cloudbuild.yaml .
            fi
        elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            echo "Logs agent image already exists - not building the image (NO_FLAG set)"
        elif [[ $NO_FLAG != "y" ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config=networkagents/logs/cloudbuild.yaml .
        fi
        gcloud run deploy logsagent \
        --image $IMAGE_URI \
        --region $GOOGLE_REGION \
        --service-account $GOOGLE_SERVICE_ACCOUNT \
        --min 1 \
        --update-env-vars GOOGLE_CLOUD_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars GOOGLE_CLOUD_LOCATION=$GOOGLE_REGION \
        --update-env-vars GOOGLE_GENAI_USE_VERTEXAI=1 \
        --update-env-vars AGENT_MCP_TOOLS_ADDRESS=$TOOLS_URL/sse \
        --update-env-vars GOOGLE_APPLICATION_CREDENTIALS="/agent/networkagent.json" \
        --allow-unauthenticated 
    fi

    # build and deploy the network dashboard
    if [[ "$AGENT_NAMES" == "all" ]] || [[ "$AGENT_NAMES" == *"dashboard"* ]]; then
        agent_processed=true
        GITEA_HOST=$(kubectl get gitea gitea -o 'jsonpath={..status.create_gitea.external_ip_address}')
        SUPERVISOR_URL=$(gcloud run services describe network-agent-supervisor --region=$GOOGLE_REGION --format="value(status.url)")
        echo "Supervisor Agent URL is ${SUPERVISOR_URL}"

        IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/dashboard:latest"
        TRAIN_GNN_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/train-gnn:latest"
        SERVE_GNN_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/serve-gnn:latest"

        if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && $(gcloud artifacts docker images describe $IMAGE_URI >/dev/null 2>&1); then
            read -p "Dashboard image already exists. Rebuild? (y/n) " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                cd ui/dashboard
                echo "Cleaning flutter environment"
                flutter clean
                echo "Building flutter web app"
                flutter build web \
                        --dart-define=WEBAPPS_LOGIN=${WEBAPPS_LOGIN} \
                        --dart-define=WEBAPPS_PWD=${WEBAPPS_PWD} \
                        --dart-define=GCP_PROJECT=${GOOGLE_PROJECT}\
                        --dart-define=GITEA_URL=https://${GITEA_HOST}:3000 \
                        --dart-define=NETWORKAGENT_URL=${SUPERVISOR_URL}\
                        --dart-define=TRAIN_GNN_URI=${TRAIN_GNN_URI}\
                        --dart-define=SERVE_GNN_URI=${SERVE_GNN_URI}
                cd ../../
                gcloud builds submit --region=$GOOGLE_REGION --config ui/dashboard/cloudbuild.yaml .
            fi
        else
            cd ui/dashboard
            echo "Cleaning flutter environment"
            flutter clean
            echo "Building flutter web app"
            flutter build web \
                    --dart-define=WEBAPPS_LOGIN=${WEBAPPS_LOGIN} \
                    --dart-define=WEBAPPS_PWD=${WEBAPPS_PWD} \
                    --dart-define=GCP_PROJECT=${GOOGLE_PROJECT}\
                    --dart-define=GITEA_URL=https://${GITEA_HOST}:3000 \
                    --dart-define=NETWORKAGENT_URL=${SUPERVISOR_URL}\
                    --dart-define=TRAIN_GNN_URI=${TRAIN_GNN_URI}\
                    --dart-define=SERVE_GNN_URI=${SERVE_GNN_URI}
            cd ../../
            gcloud builds submit --region=$GOOGLE_REGION --config ui/dashboard/cloudbuild.yaml .
        fi

        gcloud run deploy network-dashboard \
        --image $IMAGE_URI \
        --region $GOOGLE_REGION \
        --service-account $GOOGLE_SERVICE_ACCOUNT \
        --update-env-vars GOOGLE_PROJECT=$GOOGLE_PROJECT \
        --update-env-vars NETWORKAGENT_URL=${SUPERVISOR_URL} \
        --update-env-vars GITEA_URL=https://${GITEA_HOST}:3000 \
        --update-env-vars WEBAPPS_PWD=${WEBAPPS_PWD} \
        --update-env-vars WEBAPPS_LOGIN=${WEBAPPS_LOGIN} \
        --update-env-vars TRAIN_GNN_URI=${TRAIN_GNN_URI} \
        --update-env-vars SERVE_GNN_URI=${SERVE_GNN_URI} \
        --allow-unauthenticated 

        DASHBOARD_URL=$(gcloud run services describe network-dashboard --region=$GOOGLE_REGION --format="value(status.url)")
        echo "Dashboard URL is ${DASHBOARD_URL}"
    fi

    if [ "$agent_processed" = false ]; then
        echo
        echo "**ERROR**: the agent names(s) \"$AGENT_NAMES\" you specified for -n are incorrect"
        exit 1
    fi
}

############################################################
# Demo information                                         #
############################################################
DisplayDemoInfo()
{
    echo "Gathering demo information..."
    DASHBOARD_URL=$(gcloud run services describe network-dashboard --region=$GOOGLE_REGION --format="value(status.url)")
    TESTER_URL=$(gcloud run services describe testagent --region=$GOOGLE_REGION --format="value(status.url)")
    LOGS_URL=$(gcloud run services describe logsagent --region=$GOOGLE_REGION --format="value(status.url)")
    TOOLS_URL=$(gcloud run services describe networktools --region=$GOOGLE_REGION --format="value(status.url)")

    echo "============================================================"
    echo "=                Demo information Summary                  ="
    echo "============================================================"
    echo ""
    echo "Network Agent Dashboard: ${DASHBOARD_URL}"
    echo "Username/password: ${WEBAPPS_LOGIN}/${WEBAPPS_PWD}"
    echo ""
}

############################################################
# Install everything - comprehensive setup                 #
############################################################
InstallAll()
{
    echo "########################################"
    echo "Starting comprehensive installation..."
    echo "########################################"
    
    # Check if networkagent.json exists, if not run Create function
    if [[ ! -f "networkagent.json" ]] || [[ ! -s "networkagent.json" ]]; then
        echo "networkagent.json not found or empty, running Create function..."
        Create
    else
        echo "networkagent.json found, skipping Create function"
    fi
    
    # Run Start function
    echo "Running Start function..."
    Start

    # Run Networkagent function with all agents
    echo "Running Networkagent function with all agents..."
    AGENT_NAMES="all"
    Networkagent

    echo "Deploying the metrics collector service"
    DeployMetricsCollector

    # echo "Deploying GNN"
    # DeployGNN

    # Display demo information summary
    DisplayDemoInfo

    echo "########################################"
    echo "Comprehensive installation completed!"
    echo "########################################"
}

############################################################
# Help                                                     #
############################################################
Help()
{
   # Display Help
   echo "Network Agent environment manager."
   echo
   echo "Syntax: install.sh [-c|-s|-o|-l|-m|-n|-k|-d|-g|-i|--all|--deploy] [-y|-N]"
   echo 
   echo "long options:"
   echo "-------------"
   echo "  --all  install everything (comprehensive setup: create env if needed, build image if needed, start runtime, deploy all agents)"
   echo "         can be combined with -y or -N flags (e.g., ./install.sh -all -y)"
   echo "  --deploy component1 component2"
   echo "         (re)deploy specific components (valid components : spanner, operator, logcapture, git, gnn, metricscollector)"
   echo 
   echo "short options:"
   echo "--------------"
   echo "  -c     create network agent environment (keys, manifests,..)"
   echo "  -s     build and start network agent runtime (incl. the operator)"
   echo "  -o     build and deploy the network operator (same as --deploy operator)"
   echo "  -l     build and deploy the logs capture function (same as --deploy logcapture)"
   echo "  -m     build and deploy the metrics collector service (same as --deploy metricscollector)"
   echo "  -n     build and deploy the network dashboard and network agents"
   echo "         can be followed by a comma-separated list of agent names to (re)deploy selectively"
   echo "         valid agent names: all, networktools, supervisor, dashboard, test"
   echo "         example: -n dashboard,operations or -n all (to deploy all agents)"
   echo "  -k     stop and delete the network agent runtime (GKE cluster, VMS, DB, etc..)"
   echo "  -d     delete the network agent environment (keys, manifests...)."
   echo "  -g     display active GCP environment (user, project, GKE cluster,...)"
   echo "  -i     display demo information"
   echo "  -y     answer 'yes' to all questions (no ask for confirmation)"
   echo "  -N     answer 'no' to all questions (no ask for confirmation)"
   echo 
   echo "Some typical use cases:"
   echo " - To install everything from scratch: ./install.sh --all"
   echo " - To install everything from scratch without prompts: ./install.sh --all -y"
   echo " - To install everything from scratch, skipping rebuilds: ./install.sh --all -N"
   echo " - To create and run a network agent environment including the operator: ./install.sh -c; ./install.sh -s"
   echo " - To redeploy the operator alone : ./install.sh -o (or --deploy operator)"
   echo " - To (re)deploy the network agent Web UI alone : ./install.sh -n"
   echo " - To regenerate the network agent runtime with the same environment setup: ./install.sh -k; ./install.sh -s"
   echo " - To recreate a complete environment and runtime from scratch: ./install.sh -k; ./install.sh -d; ./install.sh -c; ./install.sh -s"
}

############################################################
# Process the input options. Add options as needed.        #
############################################################
# Get the options
# Global variable to store agent names
AGENT_NAMES=""
YES_FLAG="n"
NO_FLAG="n"
func_calls=""

# Handle long options first
if [[ "$1" == "--all" ]]; then
    func_calls="CheckGCPEnv SetDemoEnv InstallAll"
    shift # Remove -all from arguments
    
    # Process remaining arguments for -y or -N flags
    while [[ $# -gt 0 ]]; do
        case $1 in
            -y)
                YES_FLAG="y"
                shift
                ;;
            -N)
                NO_FLAG="y"
                shift
                ;;
            *)
                echo "Error: Invalid option '$1' with --all"
                Help
                exit 1
                ;;
        esac
    done
fi

if [[ "$1" == "--deploy" ]]; then
    func_calls="CheckGCPEnv SetDemoEnv"
    shift
    # Process remaining arguments for -y or -N flags
    while [[ $# -gt 0 ]]; do
        case $1 in
            spanner)
                func_calls="${func_calls} DeploySpanner"
                shift
                ;;
            gnn)
                func_calls="${func_calls} DeployGNN"
                shift
                ;;
            operator)
                func_calls="${func_calls} DeployOperator"
                shift
                ;;
            logcapture)
                func_calls="${func_calls} DeployLogCapture"
                shift
                ;;
            metricscollector)
                func_calls="${func_calls} DeployMetricsCollector"
                shift
                ;;
            git)
                func_calls="${func_calls} DeployGit"
                shift
                ;;
            *)
                echo "Error: Invalid component '$1' with --deploy"
                Help
                exit 1
                ;;
        esac
    done
fi

# If func_calls is already set (from -all), skip getopts
if [[ -z $func_calls ]]; then
    while getopts "hcsolmn:kdgiyN" option; do
       case $option in
          h) 
            func_calls="Help"
            ;;
          c) 
            func_calls="CheckGCPEnv SetDemoEnv Create"
            ;;
          s) 
            func_calls="CheckGCPEnv SetDemoEnv Start"
            ;;
          o) 
            func_calls="CheckGCPEnv SetDemoEnv DeployOperator"
            ;;
          l) 
            func_calls="CheckGCPEnv SetDemoEnv DeployLogCapture"
            ;;
          m) 
            func_calls="CheckGCPEnv SetDemoEnv DeployMetricsCollector"
            ;;
          n) 
            AGENT_NAMES=$OPTARG
            func_calls="CheckGCPEnv SetDemoEnv Networkagent"
            ;;
          k) 
            func_calls="CheckGCPEnv SetDemoEnv Kill"
            ;;
          d)
            func_calls="CheckGCPEnv SetDemoEnv Delete"
            ;;
          g)
            func_calls="CheckGCPEnv SetDemoEnv DisplayGCPEnv"
            ;;
          i)
            func_calls="CheckGCPEnv SetDemoEnv DisplayDemoInfo"
            ;;
          y)
            # Say yes to all questions (no ask for confirmation)
            YES_FLAG="y"
            ;;
          N)
            # Say no to all questions (no ask for confirmation)
            NO_FLAG="y"
            ;;
          \?) # Invalid option
            echo "Error: Invalid option"
            func_calls="Help"
            ;;
          :)
            echo "Option -$OPTARG requires an argument."
            ;;
       esac
    done
fi

if [[ -z $func_calls ]]; then
    Help
    exit 1
else
   # Execute the chosen functions
   for f in $func_calls; do
     $f
   done
   exit 0
fi
