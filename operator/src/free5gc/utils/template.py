import logging
from jinja2 import Environment, FileSystemLoader
import os
import utils.constants as constants
import kubernetes
from ruamel.yaml import YAML
import kopf
from utils.compute import *

logger = logging.getLogger(__name__)

##########################################################
# template the amf manifests
##########################################################
async def template_manifest(folder, filename):
    environment = Environment(loader=FileSystemLoader(folder))
    template = environment.get_template(filename)
    output=template.render(
        GOOGLE_REGION=os.getenv("GOOGLE_REGION"),
        GOOGLE_ZONE=os.getenv("GOOGLE_ZONE"),
        GOOGLE_PROJECT=os.getenv("GOOGLE_PROJECT")
        )
    return output

##########################################
# Create a new cnf config map
##########################################
async def createConfigMap(name, namespace):
  logger.debug("Creating config map for %s", name)
  yaml = YAML(typ='safe', pure=True)

  configmap_manifest = await template_manifest(constants.basedir+f"/free5gc/{name}/templates/", f"{name}-configmap.yaml" )
  configmap_manifest_yaml = yaml.load(configmap_manifest)
  kopf.adopt(configmap_manifest_yaml)

  try:
    network_api = get_resource_api("v1", configmap_manifest_yaml.get('kind'))
    network_api.create(body=configmap_manifest_yaml, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("configmap exists already - skipping")
    else:
      logger.error(e)

  return configmap_manifest_yaml

##########################################
# Create a new cnf deployment
##########################################
async def createDeployment(name,namespace):
  logger.debug("Create deployment for %s", name)
  yaml = YAML(typ='safe', pure=True)

  deployment_manifest = await template_manifest(constants.basedir+f"/free5gc/{name}/templates/", f"{name}-deployment.yaml" )
  deployment_manifest_yaml = yaml.load(deployment_manifest)
  kopf.adopt(deployment_manifest_yaml)

  try:
    network_api = get_resource_api("v1", deployment_manifest_yaml.get('kind'))
    network_api.create(body=deployment_manifest_yaml, namespace=namespace)
  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("deployment exists already - skipping")
    else:
      logger.error(e)


##########################################
# Create a new service for cnf
##########################################
async def createService(name,namespace):
  logger.debug("Create service for %s", name)
  yaml = YAML(typ='safe', pure=True)

  service_manifest = await template_manifest(constants.basedir+f"/free5gc/{name}/templates/", f"{name}-service.yaml" )
  service_manifest_yaml = yaml.load(service_manifest)
  kopf.adopt(service_manifest_yaml)

  try:

    network_api = get_resource_api("v1", service_manifest_yaml.get('kind'))
    network_api.create(body=service_manifest_yaml, namespace=namespace)

  except kubernetes.client.rest.ApiException as e: 
    if e.status == 409:
      logger.debug("service exists already - skipping")
    elif e.status == 422:
      logger.debug("unprocessable, service already deployed, skipping")
    else:
      logger.error(e)
