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

import logging
import kubernetes
import kopf
import googleapiclient.discovery
from tempfile import NamedTemporaryFile
import base64
from ruamel.yaml import YAML
import google.auth
from google.cloud.container_v1 import ClusterManagerClient
import os
from utils.compute import get_resource_api

logger = logging.getLogger(__name__)

##########################################################
# get GCP auth token
##########################################################
def token(*scopes):
    credentials, _ = google.auth.load_credentials_from_file("/operator/networkagent.json")
    scopes = [f'https://www.googleapis.com/auth/{s}' for s in scopes]
    scoped = googleapiclient._auth.with_scopes(credentials, scopes)
    googleapiclient._auth.refresh_credentials(scoped)
    return scoped.token

##########################################################
# Given a public cluster endopoint and certificate return 
# kubernetes api client
##########################################################
def get_api_client(endpoint, certificate):
    config = kubernetes.client.Configuration()
    config.host = f'https://{endpoint}'

    config.api_key_prefix['authorization'] = 'Bearer'
    mytoken = token('cloud-platform')

    logger.debug(mytoken)
    config.api_key['authorization'] = mytoken

    with NamedTemporaryFile(delete=False) as cert:
        cert.write(base64.decodebytes(certificate.encode()))
        config.ssl_ca_cert = cert.name

    client = kubernetes.client.ApiClient(configuration=config)

    return client

##########################################################
# get the external ip address of the named cluster
##########################################################
async def getClusterIP(name):
    logger.debug("get cluster external ip for %s", name)
    credentials, _ = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))
    cluster_manager_client = ClusterManagerClient(credentials=credentials)
    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    GOOGLE_REGION = os.getenv("GOOGLE_REGION")
    clustername=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_REGION}/clusters/{name}"
    cluster = cluster_manager_client.get_cluster(name=clustername)
    return cluster.endpoint

################################################################
# get the external ip address of the networkautomation cluster
################################################################
async def getAutomationClusterIP():
    logger.debug("get cluster external ip for networkautomation")
    credentials, _ = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))
    cluster_manager_client = ClusterManagerClient(credentials=credentials)
    GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
    GOOGLE_ZONE = os.getenv("GOOGLE_ZONE")
    clustername=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_ZONE}/clusters/networkautomation"
    cluster = cluster_manager_client.get_cluster(name=clustername)
    return cluster.endpoint

##########################################################
# Get all cluster details for CNRM created cluster
##########################################################
async def getClusterDetails(clustername):
  logger.debug("get cluster details for %s", clustername)
  credentials, _ = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))
  cluster_manager_client = ClusterManagerClient(credentials=credentials)
  GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
  GOOGLE_REGION = os.getenv("GOOGLE_REGION")
  name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_REGION}/clusters/{clustername}"
  cluster = cluster_manager_client.get_cluster(name=name)
  return cluster

##########################################################
# Get all cluster details for networkautomation
##########################################################
async def getAutomationClusterDetails():
  logger.debug("get cluster details for networkautomation")
  credentials, _ = google.auth.load_credentials_from_file(os.getenv("NETWORK_AGENT_FILE","/operator/networkagent.json"))
  cluster_manager_client = ClusterManagerClient(credentials=credentials)
  GOOGLE_PROJECT = os.getenv("GOOGLE_PROJECT")
  GOOGLE_ZONE = os.getenv("GOOGLE_ZONE")
  name=f"projects/{GOOGLE_PROJECT}/locations/{GOOGLE_ZONE}/clusters/networkautomation"
  cluster = cluster_manager_client.get_cluster(name=name)
  return cluster

##########################################################
# Get node ip addresses
##########################################################
async def getNodeAddresses():
    logger.debug("get node address")
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version="v1", kind="Node")
    try:

        result = api.get()
        logging.debug(result)
        addresses = []
        for node in result.items:
            addresses.append(node.get('status')['addresses'][0]['address'])

        return addresses

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug("%s Not found")
        else:
            logger.error(e)

##########################################################
# Get pod multi network addresses
##########################################################
async def getPodAddress(labels):
    logger.debug("getting pod address with labels %s", labels)

    yaml = YAML(typ='safe', pure=True)

    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version="v1", kind="Pod")

    try:
        result = api.get(label_selector=labels)
        logger.debug(result)

        if result is not None:     
            for pod in result.items:
                logger.debug(pod)
                if pod.get('metadata')['annotations']['networking.gke.io/pod-ips'] is not None:
                    logger.debug("getting ip address")
                    addressString = pod.get('metadata')['annotations']['networking.gke.io/pod-ips']
                    logger.debug(addressString)
                    address = yaml.load(addressString)
                    logger.debug(address)
                    return address[0]['ip']
                else:
                    raise kopf.TemporaryError(f"Waiting for pod address with labels {labels}", 10)
        else:
            raise kopf.TemporaryError(f"Pod with labels {labels} not found, waiting", 20)

        return None

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug("Pod with labels %s not found", labels)
            raise kopf.TemporaryError(f"Pod with labels {labels} not found", 10)
        else:
            logger.error(e)


##########################################################
# Return the ip address of the DNN named
##########################################################
async def getDNNAddress(namespace, name):
    logger.debug("get DNN address for %s %s", name, namespace)

    network_api = get_resource_api("google.dev/v1", "DataNetwork")

    address=None
    try:
        result = network_api.get(name=name, namespace=namespace)
        logger.debug(result)
        if result.get('status').get('datanetwork') is None:
            raise kopf.TemporaryError("Waiting for dnn to come up")
        address=result.get('status').get('datanetwork').get('address')
        logger.debug("DNN ADDRESS = %s", address)

    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No DNN {name} found yet. Waiting...")

    return address

########################################################
# Get the user running at the UERANSIM instance
########################################################
async def getIMSI(namespace, name):
    logger.debug(f"Getting userid at UERANSIM {name}")
    
    network_api = get_resource_api("google.dev/v1", "UERanSIM")
    userid=None
    try:
        result = network_api.get(name=name, namespace=namespace)
        logger.debug(result)
        userid=result.get('spec').get('ue').get('imsi')
        logger.debug(f"found IMSI {userid}")
    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No UERANSIM {name} found yet. Waiting...")
    return userid

##########################################################
# Return the ip address of the UPF named
##########################################################
async def getUPFAddress(upfnamespace, upfname):
    logger.debug("get upf address for %s %s", upfname, upfnamespace)

    network_api = get_resource_api("google.dev/v1", "UserPlaneFunction")
    logger.debug("looking for upf %s %s", upfname, upfnamespace)

    upfaddress=None
    try:
        result = network_api.get(name=upfname, namespace=upfnamespace)
        logger.debug(result)
        if result.get('status') is not None:
            if result.get('status').get('userplanefunction') is None:
                raise kopf.TemporaryError("Waiting for upf to come up")
            upfaddress=result.get('status').get('userplanefunction').get('ingressAddress')
            logger.debug("UPF ADDRESS = %s", upfaddress)
        else:
            raise kopf.TemporaryError("No status yet")

    except kubernetes.client.rest.ApiException as e: 
        logger.debug(e.status)
        if e.status == 404:
            raise kopf.TemporaryError(f"No UPF {upfname} found yet. Waiting...")

    return upfaddress
