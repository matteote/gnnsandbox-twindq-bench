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
from utils.compute import *
# from utils.request_throttler import throttled, throttled_call
import json
# Imports the Google Cloud Spanner Client Library.
from google.cloud import spanner
import threading
import time
import os

SQL_TEMPLATES = {
  # --- Knowledge Graph tables ---
  'create_kg_res_node': "INSERT KgResourceDescriptionNode (id, content)"
                        " VALUES (@id, @content)",
  'update_kg_res_node': "UPDATE KgResourceDescriptionNode SET content = @content WHERE id = @id",
  'delete_kg_res_node': "DELETE FROM KgResourceDescriptionNode WHERE id = @id",
  'exist_kg_res_node' : "SELECT id FROM KgResourceDescriptionNode WHERE id = '{id}'",

  # --- Network topology tables ---
  # PhysicalRouter SCD
  'get_active_phy_router': "SELECT config, status FROM PhysicalRouter WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_router': "UPDATE PhysicalRouter SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_router': "INSERT PhysicalRouter (id, name, vendor, model, location_city, location_lat, location_lon, role, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @name, @vendor, @model, @location_city, @location_lat, @location_lon, @role, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_router': "UPDATE PhysicalRouter SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'get_router_id_by_name': "SELECT id FROM PhysicalRouter WHERE name = @name AND valid_end_ts IS NULL",

  # PhysicalInterface SCD
  'get_active_phy_interface': "SELECT speed, media_type, ip_address, mac_address, status FROM PhysicalInterface WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_interface': "UPDATE PhysicalInterface SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_interface': "INSERT PhysicalInterface (id, router_id, name, speed, media_type, ip_address, mac_address, status, valid_start_ts, valid_end_ts) VALUES (@id, @router_id, @name, @speed, @media_type, @ip_address, @mac_address, @status, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_interface_by_router': "UPDATE PhysicalInterface SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE router_id = @router_id AND valid_end_ts IS NULL",

  'upsert_customer': "INSERT OR UPDATE Customer (id, name, type, properties, last_updated) VALUES (@id, @name, @type, @properties, PENDING_COMMIT_TIMESTAMP())",
  
  # L3VPNService SCD
  'get_active_l3vpn': "SELECT config, status FROM L3VPNService WHERE id = @id AND valid_end_ts IS NULL",
  'close_l3vpn': "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_l3vpn': "INSERT L3VPNService (id, customer_id, name, service_type, topology, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @customer_id, @name, @service_type, @topology, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_l3vpn': "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  # VRF SCD
  'get_active_vrf': "SELECT config, status FROM VRF WHERE id = @id AND valid_end_ts IS NULL",
  'close_vrf': "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_vrf': "INSERT VRF (id, router_id, vpn_id, name, rd, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @router_id, @vpn_id, @name, @rd, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_vrf_by_vpn': "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vpn_id = @vpn_id AND valid_end_ts IS NULL",
  
  # BGPSession SCD
  'get_active_bgp': "SELECT config, status FROM BGPSession WHERE id = @id AND valid_end_ts IS NULL",
  'close_bgp': "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_bgp': "INSERT BGPSession (id, vrf_id, local_as, remote_as, peer_ip, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @vrf_id, @local_as, @remote_as, @peer_ip, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_bgp_by_vpn': "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id = @vpn_id AND valid_end_ts IS NULL) AND valid_end_ts IS NULL",

  # LogicalSubnet SCD
  'get_active_subnet': "SELECT properties, network_type FROM LogicalSubnet WHERE id = @id AND valid_end_ts IS NULL",
  'close_subnet': "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_subnet': "INSERT LogicalSubnet (id, cidr, network_type, description, properties, valid_start_ts, valid_end_ts) VALUES (@id, @cidr, @network_type, @description, @properties, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_subnet': "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  # PhysicalLink SCD
  'get_active_phy_link': "SELECT properties, status FROM PhysicalLink WHERE id = @id AND valid_end_ts IS NULL",
  'close_phy_link': "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_phy_link': "INSERT PhysicalLink (id, name, bandwidth, status, properties, valid_start_ts, valid_end_ts) VALUES (@id, @name, @bandwidth, @status, @properties, PENDING_COMMIT_TIMESTAMP(), NULL)",

  'delete_phy_link': "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  'upsert_interface_link': "INSERT OR IGNORE Interface_Link (interface_id, link_id) VALUES (@interface_id, @link_id)",
  'delete_interface_link': "DELETE FROM Interface_Link WHERE interface_id = @interface_id OR link_id = @link_id",

  # Interface_Link SCD
  'get_active_interface_link': "SELECT interface_id FROM Interface_Link WHERE interface_id = @interface_id AND link_id = @link_id AND valid_end_ts IS NULL",
  'close_interface_link': "UPDATE Interface_Link SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE interface_id = @interface_id AND link_id = @link_id AND valid_end_ts IS NULL",
  'insert_interface_link': "INSERT Interface_Link (interface_id, link_id, valid_start_ts, valid_end_ts) VALUES (@interface_id, @link_id, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_interface_link_by_id': "UPDATE Interface_Link SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE (interface_id = @interface_id OR link_id = @link_id) AND valid_end_ts IS NULL",

  # Subnet_Association SCD
  'get_active_subnet_assoc': "SELECT entity_id FROM Subnet_Association WHERE entity_id = @entity_id AND subnet_id = @subnet_id AND valid_end_ts IS NULL",
  'close_subnet_assoc': "UPDATE Subnet_Association SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE entity_id = @entity_id AND subnet_id = @subnet_id AND valid_end_ts IS NULL",
  'insert_subnet_assoc': "INSERT Subnet_Association (entity_id, subnet_id, entity_type, valid_start_ts, valid_end_ts) VALUES (@entity_id, @subnet_id, @entity_type, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_subnet_assoc_by_id': "UPDATE Subnet_Association SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE (entity_id = @entity_id OR subnet_id = @subnet_id) AND valid_end_ts IS NULL",

  'upsert_bgp_peering': "INSERT OR IGNORE BGP_Peering (session_id_a, session_id_b) VALUES (@id_a, @id_b)",
  'delete_bgp_peering': "DELETE FROM BGP_Peering WHERE session_id_a = @id OR session_id_b = @id",

  # BGP_Peering SCD
  'get_active_bgp_peering': "SELECT session_id_a FROM BGP_Peering WHERE session_id_a = @id_a AND session_id_b = @id_b AND valid_end_ts IS NULL",
  'close_bgp_peering': "UPDATE BGP_Peering SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE session_id_a = @id_a AND session_id_b = @id_b AND valid_end_ts IS NULL",
  'insert_bgp_peering': "INSERT BGP_Peering (session_id_a, session_id_b, valid_start_ts, valid_end_ts) VALUES (@id_a, @id_b, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_bgp_peering_by_id': "UPDATE BGP_Peering SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE (session_id_a = @id OR session_id_b = @id) AND valid_end_ts IS NULL",

  # Device SCD
  'get_active_device': "SELECT router_id, network_name, ip_address, gateway, vlan, status, config FROM Device WHERE id = @id AND valid_end_ts IS NULL",
  'close_device': "UPDATE Device SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",
  'insert_device': "INSERT Device (id, name, router_id, network_name, ip_address, gateway, vlan, status, config, valid_start_ts, valid_end_ts) VALUES (@id, @name, @router_id, @network_name, @ip_address, @gateway, @vlan, @status, @config, PENDING_COMMIT_TIMESTAMP(), NULL)",
  'delete_device': "UPDATE Device SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id = @id AND valid_end_ts IS NULL",

  'upsert_service_perf': "INSERT OR UPDATE ServicePerformance (id, service_type, response_time_ms, timestamp, userid, error, node, vpn_id) VALUES (@id, @service_type, @response_time_ms, @timestamp, @userid, @error, @node, @vpn_id)",
  'upsert_incident': "INSERT OR UPDATE Incident (id, recordedTimestamp, agentTaskId, issue, strategy, root_cause, resolution, resolvedTimestamp) VALUES (@id, @recordedTimestamp, @agentTaskId, @issue, @strategy, @root_cause, @resolution, @resolvedTimestamp)",
}

# Spanner DB connection check interval
DB_CHECK_SECONDS=int(os.environ.get("DB_CHECK_SECONDS", 60))

# Connect to Spanner database
def spanner_connect():
  spanner_client = spanner.Client()
  instance = spanner_client.instance('networktopology-instance')
  database = instance.database('networktopology-db')
  return database

# Global lock for thread-safe database updates
lock = threading.Lock()

def check_spanner_connection(db_container, lock):
    """Checks if the Spanner database exists and reconnects if necessary."""
    try:
        # Check outside lock first
        if not db_container['db'].exists():
            with lock:
                # Double check inside the lock to avoid multiple reconnections
                if not db_container['db'].exists():
                    logger.warning("Spanner database not found. Attempting to reconnect...")
                    db_container['db'] = spanner_connect()
                    logger.warning("Reconnected to Spanner.")
        else:
            logger.info("Spanner DB connection alive!")
    except Exception as e:
        with lock:
            logger.error(f"Error checking Spanner connection: {e}. Attempting to reconnect...")
            try:
                db_container['db'] = spanner_connect()
                logger.warning("Reconnected to Spanner after error.")
            except Exception as re:
                logger.error(f"Failed to reconnect to Spanner: {re}")

def spanner_connection_worker(db_container, lock):
    """
    Background worker that regularly checks the Spanner connection.
    """
    logger.info(f"Spanner connection worker started. Checking every {DB_CHECK_SECONDS} seconds.")
    while True:
        check_spanner_connection(db_container, lock)
        time.sleep(DB_CHECK_SECONDS)

# Initialize database connection and start connection monitor thread
database_initial = spanner_connect()
db_container = {'db': database_initial}

connection_thread = threading.Thread(
    target=spanner_connection_worker, 
    args=(db_container, lock), 
    daemon=True
)
connection_thread.start()

logger = logging.getLogger(__name__)

# ------------------------------------------
# Build a serialized JSON representation of the 
# body that fit into a INSERT/UPDATE SQL statement
#
# **WARNING** Please think twice before making modifications
# here as it took me a lot of trial and errors to come up
# with this solution
# ------------------------------------------
def body_sql_json_dump(string_dump):
  # Double escape the \" sequences created by the santitize call so as to build
  # a syntactically correct SQL INSERT statement for Spanner to execute.
  # Also escape single quotes as single quotes are used to enclose the
  # JSON string in the SQL statement.
  return string_dump.replace('\\n','\\\\n').replace('\\"', '\\\\"').replace("'", "\\'")
 
def body_string_dump(body, kind, namespace, name):
  # Do not rely on the body object from kopf. Get it from
  # K8s directly
  api = kubernetes.client.ApiClient()
  client = kubernetes.dynamic.DynamicClient(api)
  resource_api = get_resource_api(body.get('apiVersion'), kind, client)
  resource = resource_api.get(namespace=namespace, name=name)
  #sanitized_resource = api.sanitize_for_serialization(resource.to_dict())
  #logger.debug("resource: %s",sanitized_resource)

  # Remove some JSON keys that Spanner JSON doesn't like although it is perfectly
  # valid and sanitized (invalid JSON litteral error on SQL INSERT)
  resource_dict = api.sanitize_for_serialization(resource.to_dict())

  resource_dict['metadata'].pop('managedFields', None)
  if 'annotations' in resource_dict['metadata']:
    # CAUTION !! We are iterating through keys that we can possibly delete 
    # so keep the for loop below exactly as is (the call to list() does
    # a copy of the keys)
    for key in list(resource_dict['metadata']['annotations'].keys()):
      if key.startswith('kopf'):
        resource_dict['metadata']['annotations'].pop(key, None)
 
  return json.dumps(resource_dict, ensure_ascii = True)

# ------------------------------------------
# Helper to sanitize K8s body for storage
# ------------------------------------------
def sanitize_k8s_body(body):
  import copy
  # Deep copy to avoid modifying the original body used by kopf
  clean_body = copy.deepcopy(body)
  
  if 'metadata' in clean_body:
    # Remove managedFields as they are verbose and not needed for config
    clean_body['metadata'].pop('managedFields', None)
    
    # Remove internal annotations
    if 'annotations' in clean_body['metadata']:
      for key in list(clean_body['metadata']['annotations'].keys()):
        if key.startswith('kopf') or key.startswith('kubectl.kubernetes.io'):
          clean_body['metadata']['annotations'].pop(key, None)
          
  return clean_body

# ------------------------------------------
# Extract a human readbale status and return a well 
# formatted string to use in SQL INSERT (either NULL or
# "'status_string'")
# ------------------------------------------
def get_status(body):
  status_value = "NULL"
  status = body.get('status')
  if status is not None:
    conditions = status.get('conditions')
    # NOTE: conditions is a list object
    if conditions is not None:
      reason = conditions[0].get('reason')
      if reason is not None:
        status_value = reason
    else:
      if body['kind'].lower() in ['wireguardappliance', 'pointtopointservice', 'meshservice', 'userplanefunction', 'controlplane', 'datanetwork','ueransim']:
        if 'currentStatus' in body['status']:
          status_value = body['status']['currentStatus']
        else:
          svc = body['kind'].lower()
          if (svc in body['status']):
            if ('status' in body['status'][svc]):
              status_value = body['status'][svc]['status']
          elif ('kopf' in body['status']) and ('progress' in body['status']['kopf']) and (svc in body['status']['kopf']['progress']):
            if ('failure' in body['status']['kopf']['progress'][svc]) and (body['status']['kopf']['progress'][svc]['failure'] == True):
              status_value = 'Failed'

  return status_value

# ------------------------------------------
# Idempotent function to create or update a
# KG resource node
# ------------------------------------------
# @throttled
async def create_or_update_kg_resource_description_node(id, body_string):
  success = True
  if await exist_kg_resource_description_node(id):
    success = success & await update_kg_resource_description_node(id, body_string)
  else:
    success = success & await create_kg_resource_description_node(id, body_string)
  return success

# ------------------------------------------
# Does a KG resource node exists
# ------------------------------------------
# @throttled
async def exist_kg_resource_description_node(id):

  tmpl = SQL_TEMPLATES['exist_kg_res_node']
  sql = tmpl.format(id=id)
  logger.debug("SQL: {}".format(sql))

  try:
    with db_container['db'].snapshot() as snapshot:
      results = snapshot.execute_sql(sql)
    success = (results.one_or_none() is not None)
  except Exception as e:
    success = False
    logger.error("SQL error: {}".format(e))

  if success:
    logger.debug("{} KG resource node exists)".format(id))
  else:
    logger.debug("{} KG resource node doesn't exist)".format(id))
  return success

# ------------------------------------------
# Create K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def create_kg_resource_description_node(id, body_string):

  def sql_create_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['create_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string
  
  row_ct = 0
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_create_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"KG Resource node created id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node creation failed id: {id}")
  return success


# ------------------------------------------
# Update K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def update_kg_resource_description_node(id, body_string):

  def sql_update_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['update_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"content": content, "id": id},
      param_types={
        "content": spanner.param_types.STRING,
        "id": spanner.param_types.STRING})
  
  # For now we only update the status field and node property
  content = body_string

  row_ct = None
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_update_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")
  
  if success:
    logger.debug(f"KG Resource node updated id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node update failed id: {id} ")
  return success

# ------------------------------------------
# Delete K8s resource descriptions in Knowledge Graph
# ------------------------------------------
# @throttled
async def delete_kg_resource_description_node(id):

  def sql_delete_kg_resource_description_node(transaction):
    sql = SQL_TEMPLATES['delete_kg_res_node']
    logger.debug(f"SQL: {sql}")
    return transaction.execute_update(
      sql,
      params={"id": id},
      param_types={"id": spanner.param_types.STRING})
   
  row_ct = None
  success = True
  try:
    row_ct = db_container['db'].run_in_transaction(sql_delete_kg_resource_description_node)
  except Exception as e:
    success = False
    logger.error(f"SQL error: {e}")

  if success:
    logger.debug(f"{id} KG Resource node deleted id: {id} (row count: {row_ct})")
  else:
    logger.error(f"KG Resource Node deletion failed id: {id}")
  return success

# ------------------------------------------
# Helper to find PhysicalRouter ID by name
# ------------------------------------------
async def _get_router_id_by_name(name):
  sql = SQL_TEMPLATES['get_router_id_by_name']
  try:
    with db_container['db'].snapshot() as snapshot:
      results = snapshot.execute_sql(sql, params={'name': name}, param_types={'name': spanner.param_types.STRING})
      # Get first result in case of duplicates (could happen if routers were recreated)
      for row in results:
        return row[0]  # Return the first match
  except Exception as e:
    logger.error(f"Error finding router id for {name}: {e}")
  return None

# ------------------------------------------
# Sync VyOSInfrastructure
# ------------------------------------------
async def sync_vyos_infrastructure(body, spec, name, uid, logger):
    logger.debug(f"Syncing VyOSInfrastructure {name}")
    # Sync networks as LogicalSubnets (SCD)
    networks = spec.get('networks', [])
    for net in networks:
        subnet_id = f"subnet:{net['name']}"
        # Convert to dict if it's a Kubernetes object
        net_dict = dict(net) if hasattr(net, '__iter__') and not isinstance(net, (str, bytes)) else net
        subnet_props = json.dumps(net_dict)
        cidr = net.get('subnet', '')
        network_type = net.get('network_type', 'unknown')
        description = net.get('description', '')
        
        def sql_upsert_subnet(transaction):
            # 1. Get active subnet
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_subnet'],
                params={'id': subnet_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                existing_props = row[0] # properties is first col in get_active_subnet
                # Compare content
                if existing_props == net_dict:
                     need_insert = False
                else:
                    # Close existing
                    transaction.execute_update(
                        SQL_TEMPLATES['close_subnet'],
                        params={'id': subnet_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
            
            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_subnet'],
                    params={
                        'id': subnet_id,
                        'cidr': cidr,
                        'network_type': network_type,
                        'description': description,
                        'properties': subnet_props
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'cidr': spanner.param_types.STRING,
                        'network_type': spanner.param_types.STRING,
                        'description': spanner.param_types.STRING,
                        'properties': spanner.param_types.JSON
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_subnet)
        except Exception as e:
            logger.error(f"Failed to upsert subnet {subnet_id}: {e}")
        
        # Create PhysicalLink if this network has connected_routers (indicates a physical link)
        connected_routers = net.get('connected_routers', [])
        if len(connected_routers) >= 2:
            # This is a physical link connecting routers
            link_id = f"link:{net['name']}"
            link_name = net.get('name', '')
            bandwidth = net.get('bandwidth', 'unknown')
            link_status = 'UP'  # Could be derived from network/router status
            
            # Build link properties including all network details
            link_props = {
                'subnet': net.get('subnet', ''),
                'network_type': net.get('network_type', 'unknown'),
                'vlan': net.get('vlan', None),
                'mtu': net.get('mtu', 1500),
                'description': net.get('description', ''),
                'connected_routers': connected_routers
            }
            
            def sql_upsert_link(transaction):
                # 1. Get active link
                results = transaction.execute_sql(
                    SQL_TEMPLATES['get_active_phy_link'],
                    params={'id': link_id},
                    param_types={'id': spanner.param_types.STRING}
                )
                row = results.one_or_none()

                need_insert = True
                if row:
                    # row[0] is properties (JSON/Dict), row[1] is status
                    existing_props = row[0]
                    existing_status = row[1]
                    
                    # Compare content (Dict comparison)
                    if existing_props == link_props and existing_status == link_status.lower():
                        need_insert = False
                    else:
                        # Close existing row
                        transaction.execute_update(
                            SQL_TEMPLATES['close_phy_link'],
                            params={'id': link_id},
                            param_types={'id': spanner.param_types.STRING}
                        )
                
                if need_insert:
                    transaction.execute_update(
                        SQL_TEMPLATES['insert_phy_link'],
                        params={
                            'id': link_id,
                            'name': link_name,
                            'bandwidth': bandwidth,
                            'status': link_status.lower(),
                            'properties': json.dumps(link_props)
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'name': spanner.param_types.STRING,
                            'bandwidth': spanner.param_types.STRING,
                            'status': spanner.param_types.STRING,
                            'properties': spanner.param_types.JSON
                        }
                    )
            
            try:
                db_container['db'].run_in_transaction(sql_upsert_link)
                logger.debug(f"Created PhysicalLink {link_id} connecting {len(connected_routers)} routers")
            except Exception as e:
                logger.error(f"Failed to create PhysicalLink {link_id}: {e}")
            
            # Create Interface_Link associations for each connected router
            for router_conn in connected_routers:
                router_name = router_conn.get('router_name')
                interface_name = router_conn.get('interface')
                
                if not router_name or not interface_name:
                    logger.warning(f"Skipping connection with missing router_name or interface in link {link_id}")
                    continue
                
                # Look up router ID by name
                router_id = await _get_router_id_by_name(router_name)
                
                if not router_id:
                    logger.debug(f"Router {router_name} not found yet for link {link_id}, will sync when router is created")
                    continue
                
                interface_id = f"{router_id}:interface:{interface_name}"
                
                def sql_upsert_iface_link(transaction):
                    # Check active link
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_interface_link'],
                        params={'interface_id': interface_id, 'link_id': link_id},
                        param_types={'interface_id': spanner.param_types.STRING, 'link_id': spanner.param_types.STRING}
                    )
                    if not results.one_or_none():
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_interface_link'],
                            params={
                                'interface_id': interface_id,
                                'link_id': link_id
                            },
                            param_types={
                                'interface_id': spanner.param_types.STRING,
                                'link_id': spanner.param_types.STRING
                            }
                        )
                
                try:
                    db_container['db'].run_in_transaction(sql_upsert_iface_link)
                    logger.debug(f"Linked interface {interface_id} to {link_id}")
                except Exception as e:
                    logger.error(f"Failed to link interface {interface_id} to {link_id}: {e}")


async def delete_vyos_infrastructure(uid, spec, logger):
    """Delete VyOSInfrastructure and associated subnets and physical links"""
    logger.debug(f"Deleting VyOSInfrastructure {uid}")
    
    def sql_delete(transaction):
        # Delete interface-link associations
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE link_id LIKE 'link:%'",
            params={},
            param_types={}
        )
        
        # Close physical links (SCD)
        transaction.execute_update(
            "UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'link:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # Delete subnet associations
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE subnet_id LIKE 'subnet:%'",
            params={},
            param_types={}
        )
        
        # Close logical subnets created by this infrastructure (SCD)
        transaction.execute_update(
            "UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'subnet:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
    
    try:
        db_container['db'].run_in_transaction(sql_delete)
        logger.debug(f"Successfully deleted VyOSInfrastructure topology for {uid}")
    except Exception as e:
        logger.error(f"Failed to delete VyOSInfrastructure {uid}: {e}")

# ------------------------------------------
# Sync PhysicalRouter
# ------------------------------------------
async def sync_physical_router(body, spec, name, uid, logger):
    logger.debug(f"Syncing PhysicalRouter {name}")
    
    # 1. Upsert Router
    # Use router name as ID to prevent duplicates when router is recreated
    router_id = f"router:{name}"
    
    router_role = spec.get('role', 'Router')
    logger.debug(f"Syncing PhysicalRouter {name} with role: {router_role}")
    
    # Extract status from CRD status field
    router_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        router_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            router_status = status_str
    
    def sql_upsert_router(transaction):
        # Prepare the full config object (sanitized body)
        # We use the full body so we have metadata, status, etc.
        sanitized_body = sanitize_k8s_body(body)
        # Ensure it is a dict (it should be coming from kopf/lifecycle)
        if not isinstance(sanitized_body, dict):
             # Fallback if somehow it's not a dict, though unexpected
             sanitized_body = dict(sanitized_body)
             
        config_json = json.dumps(sanitized_body)

        
        # Get location from spec.location (VyOS Infrastructure uses latitude/longitude, not lat/lon)
        location = spec.get('location', {})
        metadata_labels = body.get('metadata', {}).get('labels', {})
        
        # 1. Get active router
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_phy_router'],
            params={'id': router_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()

        need_insert = True
        if row:
            existing_config = row[0]
            existing_status = row[1]
            # Compare content. existing_config is the config JSON string from DB (or dict if client decodes it??)
            # Spanner client usually returns JSON types as native Python objects (dicts/lists)
            # But let's be careful. If row[0] is a string, load it.
            # update: param_types.JSON returns native object (dict)
            
            # We compare the dictionary representations
            if existing_config == sanitized_body and existing_status == router_status.lower():
                need_insert = False
            else:
                # Close existing row
                transaction.execute_update(
                    SQL_TEMPLATES['close_phy_router'],
                    params={'id': router_id},
                    param_types={'id': spanner.param_types.STRING}
                )

        
        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_phy_router'],
                params={
                    'id': router_id,
                    'name': name,
                    'vendor': spec.get('vendor', 'VyOS'),
                    'model': spec.get('model', 'Virtual'),
                    'location_city': location.get('city') or metadata_labels.get('city', 'Unknown'),
                    'location_lat': float(location.get('latitude') or location.get('lat') or metadata_labels.get('latitude') or metadata_labels.get('lat') or 0.0),
                    'location_lon': float(location.get('longitude') or location.get('lon') or metadata_labels.get('longitude') or metadata_labels.get('lon') or 0.0),
                    'role': spec.get('role', 'Router'),
                    'status': router_status.lower(),
                    'config': config_json
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'vendor': spanner.param_types.STRING,
                    'model': spanner.param_types.STRING,
                    'location_city': spanner.param_types.STRING,
                    'location_lat': spanner.param_types.FLOAT64,
                    'location_lon': spanner.param_types.FLOAT64,
                    'role': spanner.param_types.STRING,
                    'status': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON
                }
            )
    
    try:
        db_container['db'].run_in_transaction(sql_upsert_router)
    except Exception as e:
        logger.error(f"Failed to upsert router {name}: {e}")
        return

    # 2. Upsert Interfaces
    interfaces = spec.get('interfaces', [])
    for iface in interfaces:
        # Handle both string and object interface definitions
        if isinstance(iface, str):
            iface_name = iface
            iface_data = {}
        else:
            iface_name = iface.get('name', 'unknown')
            iface_data = iface
        
        iface_id = f"{router_id}:interface:{iface_name}"
        
        # Extract IP address (remove CIDR if present)
        ip_address = iface_data.get('address', '0.0.0.0')
        if '/' in ip_address:
            ip_address = ip_address.split('/')[0]
        
        # Determine interface status from router status or interface specific status
        iface_status = 'Unknown'
        if status_obj and 'interfaces' in status_obj:
            for iface_status_obj in status_obj['interfaces']:
                if not isinstance(iface_status_obj, dict):
                    logger.warning(f"Skipping non-dict entry in interfaces status (got {type(iface_status_obj).__name__}): {iface_status_obj!r}")
                    continue
                if iface_status_obj.get('name') == iface_name:
                    iface_status = iface_status_obj.get('status', 'Unknown')
                    break
        
        # If no specific status, use enabled flag or default to UP if router is running
        # Normalise to uppercase to match Ansible operstate values (UP / DOWN / UNKNOWN)
        if iface_status == 'Unknown':
            if iface_data.get('enabled', True):
                iface_status = 'UP' if router_status in ['Running', 'Ready'] else 'DOWN'
            else:
                iface_status = 'ADMIN_DOWN'
        
        # Extract speed and media type with better defaults
        speed = iface_data.get('speed', '1G')  # More realistic default
        media_type = iface_data.get('media_type', 'ethernet')
        if iface_name == 'lo':
            media_type = 'loopback'
            speed = 'N/A'
        
        def sql_upsert_iface(transaction):
            # 1. Get active interface
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_phy_interface'],
                params={'id': iface_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                # SELECT speed, media_type, ip_address, mac_address, status
                existing_speed = row[0]
                existing_media = row[1]
                existing_ip = row[2]
                existing_mac = row[3]
                existing_status = row[4]
                
                # Compare content
                current_mac = iface_data.get('mac', '')
                if (existing_speed == speed and 
                    existing_media == media_type and 
                    existing_ip == ip_address and 
                    existing_mac == current_mac and 
                    existing_status == iface_status):
                    need_insert = False
                else:
                    # Close existing row
                    transaction.execute_update(
                        SQL_TEMPLATES['close_phy_interface'],
                        params={'id': iface_id},
                        param_types={'id': spanner.param_types.STRING}
                    )

            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_phy_interface'],
                    params={
                        'id': iface_id,
                        'router_id': router_id,
                        'name': iface_name,
                        'speed': speed,
                        'media_type': media_type,
                        'ip_address': ip_address,
                        'mac_address': iface_data.get('mac', ''),
                        'status': iface_status.lower()
                    },
                    param_types={
                        'id': spanner.param_types.STRING,
                        'router_id': spanner.param_types.STRING,
                        'name': spanner.param_types.STRING,
                        'speed': spanner.param_types.STRING,
                        'media_type': spanner.param_types.STRING,
                        'ip_address': spanner.param_types.STRING,
                        'mac_address': spanner.param_types.STRING,
                        'status': spanner.param_types.STRING
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_iface)
        except Exception as e:
            logger.error(f"Failed to upsert interface {iface_id}: {e}")
        
        # 3. Create subnet associations for interfaces with IP addresses
        if iface_data.get('address'):
            # Extract CIDR network from interface address
            addr_cidr = iface_data.get('address')
            if '/' in addr_cidr:
                # Create the LogicalSubnet first (required by foreign key constraint)
                subnet_id = f"subnet:{addr_cidr}"
                
                def sql_upsert_subnet(transaction):
                    # 1. Get active subnet
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_subnet'],
                        params={'id': subnet_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
                    row = results.one_or_none()

                    need_insert = True
                    if row:
                        existing_props = row[0]
                        if existing_props == {}:
                             need_insert = False
                        else:
                            # Close existing
                            transaction.execute_update(
                                SQL_TEMPLATES['close_subnet'],
                                params={'id': subnet_id},
                                param_types={'id': spanner.param_types.STRING}
                            )
                    
                    if need_insert:
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_subnet'],
                            params={
                                'id': subnet_id,
                                'cidr': addr_cidr,
                                'network_type': 'interface',
                                'description': f'Subnet for interface {iface_name}',
                                'properties': json.dumps({})
                            },
                            param_types={
                                'id': spanner.param_types.STRING,
                                'cidr': spanner.param_types.STRING,
                                'network_type': spanner.param_types.STRING,
                                'description': spanner.param_types.STRING,
                                'properties': spanner.param_types.JSON
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_subnet)
                except Exception as e:
                    logger.error(f"Failed to create subnet {subnet_id}: {e}")
                    continue  # Skip association if subnet creation fails
                
                # Now create the association (subnet must exist due to foreign key)
                def sql_upsert_subnet_assoc(transaction):
                    # Check active association
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_subnet_assoc'],
                        params={'entity_id': iface_id, 'subnet_id': subnet_id},
                        param_types={'entity_id': spanner.param_types.STRING, 'subnet_id': spanner.param_types.STRING}
                    )
                    if not results.one_or_none():
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_subnet_assoc'],
                            params={
                                'entity_id': iface_id,
                                'subnet_id': subnet_id,
                                'entity_type': 'Interface'
                            },
                            param_types={
                                'entity_id': spanner.param_types.STRING,
                                'subnet_id': spanner.param_types.STRING,
                                'entity_type': spanner.param_types.STRING
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_subnet_assoc)
                except Exception as e:
                    logger.error(f"Failed to create subnet association for {iface_id}: {e}")


async def delete_physical_router(uid, name=None):
    """Delete physical router and cascade delete related entities"""
    # Use name-based ID if available, otherwise fall back to UID for backwards compatibility
    router_id = f"router:{name}" if name else uid
    logger.debug(f"Deleting PhysicalRouter {router_id}")
    

        
    def sql_delete_router(transaction):
        # 1. Delete subnet associations (Cleanup Edge Table)
        transaction.execute_update(
            "DELETE FROM Subnet_Association WHERE entity_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # 2. Delete interface-link associations (Cleanup Edge Table)
        transaction.execute_update(
            "DELETE FROM Interface_Link WHERE interface_id IN (SELECT id FROM PhysicalInterface WHERE router_id = @router_id)",
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )
        
        # 3. Close the router (SCD)
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_router'],
            params={'id': router_id},
            param_types={'id': spanner.param_types.STRING}
        )
        
        # 4. Close associated interfaces (SCD) - Cascading Close
        transaction.execute_update(
            SQL_TEMPLATES['delete_phy_interface_by_router'],
            params={'router_id': router_id},
            param_types={'router_id': spanner.param_types.STRING}
        )

    try:
        db_container['db'].run_in_transaction(sql_delete_router)
        logger.debug(f"Successfully closed PhysicalRouter {name} and its interfaces")
    except Exception as e:
        logger.error(f"Failed to close PhysicalRouter {name}: {e}")

# ------------------------------------------
# Sync L3VPNService
# ------------------------------------------
# ------------------------------------------
# Helper to create BGP Peering edge (SCD)
# ------------------------------------------
async def _create_bgp_peering(bgp_id, peer_ip, vpn_name, logger):
    # Parse router name from bgp_id: bgp:router_name:vpn_name:peer_ip
    try:
        parts = bgp_id.split(':')
        router_name = parts[1]
        router_id = f"router:{router_name}"
    except Exception:
        logger.error(f"Failed to parse router name from bgp_id {bgp_id}")
        return

    # 1. Get my interface IPs
    my_ips = []
    sql_get_ips = "SELECT ip_address FROM PhysicalInterface WHERE router_id = @router_id AND valid_end_ts IS NULL"
    try:
        with db_container['db'].snapshot() as snapshot:
            results = snapshot.execute_sql(sql_get_ips, params={'router_id': router_id}, param_types={'router_id': spanner.param_types.STRING})
            my_ips = [row[0] for row in results if row[0]]
            # Also handle CIDR strip if needed, but DB usually has IP
            my_ips = [ip.split('/')[0] for ip in my_ips]
    except Exception as e:
        logger.error(f"Failed to get IPs for router {router_name}: {e}")
        return

    if not my_ips:
        return

    # 2. Find peer session
    # Peer session must be in same VPN and have peer_ip IN my_ips
    # AND ideally belong to a router that has `peer_ip` (my_peer_ip)
    
    # We query for sessions that point TO me
    sql_find_peer = "SELECT id FROM BGPSession WHERE id LIKE @pattern AND peer_ip IN UNNEST(@my_ips) AND valid_end_ts IS NULL"
    pattern = f"bgp:%:{vpn_name}:%"
    
    peer_session_id = None
    try:
        with db_container['db'].snapshot() as snapshot:
            results = snapshot.execute_sql(
                sql_find_peer, 
                params={'pattern': pattern, 'my_ips': my_ips}, 
                param_types={'pattern': spanner.param_types.STRING, 'my_ips': spanner.param_types.ARRAY(spanner.param_types.STRING)}
            )
            # There might be multiple matches if full mesh? 
            # But typically point-to-point uses specific /30 or /31. 
            # If multiple sessions point to my IP, it's ambiguous.
            # We filter by checking if the session ID (which contains peer IP) implies the peer router?
            # Actually, `bgp_id` contains `peer_ip`.
            # The session we are looking for is `bgp:PEER_ROUTER:vpn:MY_IP`.
            # We verify if `PEER_ROUTER` owns `peer_ip` (from my bgp_id argument).
            
            candidates = list(results)
            for row in candidates:
                cand_id = row[0]
                # Check if cand_id's router owns `peer_ip`
                try:
                    cand_parts = cand_id.split(':')
                    cand_router_name = cand_parts[1]
                    cand_router_id = f"router:{cand_router_name}"
                    
                    # Check if cand_router owns `peer_ip`
                    sql_check_ip = "SELECT 1 FROM PhysicalInterface WHERE router_id = @rid AND ip_address LIKE @ip_pattern AND valid_end_ts IS NULL"
                    # ip_address in DB usually includes CIDR? Or striped? 
                    # Code: ip_address = ip_address.split('/')[0] (Step 643 upsert).
                    # So exact match or check.
                    
                    with db_container['db'].snapshot() as sub_snap:
                        res = sub_snap.execute_sql(
                            sql_check_ip, 
                            params={'rid': cand_router_id, 'ip_pattern': f"{peer_ip}%"}, # Fuzzy match for CIDR if inconsistent
                            param_types={'rid': spanner.param_types.STRING, 'ip_pattern': spanner.param_types.STRING}
                        )
                        if res.one_or_none():
                            peer_session_id = cand_id
                            break
                except:
                    continue
    except Exception as e:
        logger.error(f"Failed to find peer BGP session: {e}")
        return

    if peer_session_id:
        # 3. Create Peering Link (SCD)
        def sql_link_bgp(transaction):
            # Sort to avoid duplicates? BGP Peering is directional or non-directional?
            # Table is (session_a, session_b). Usually implies distinct rows for direction?
            # Or one row per pair?
            # If (a,b), then (b,a) is different.
            # Logic: We insert ONE row per peering relationship?
            # Or insert (me, peer)?
            # Code uses `id_a, id_b`.
            # If specific constraint exists, we sort. 
            # If we want directional graph, we assume (source, target).
            # BGP Peering is symmetric. 
            # Previous code used `unique_sessions = sorted([...])` (Step 544).
            # So let's sort to keep one canonical row per pair.
            
            ids = sorted([bgp_id, peer_session_id])
            session_a = ids[0]
            session_b = ids[1]

            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_bgp_peering'],
                params={'id_a': session_a, 'id_b': session_b},
                param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
            )
            if not results.one_or_none():
                transaction.execute_update(
                    SQL_TEMPLATES['insert_bgp_peering'],
                    params={'id_a': session_a, 'id_b': session_b},
                    param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
                )

        try:
            db_container['db'].run_in_transaction(sql_link_bgp)
            logger.debug(f"Linked BGP Session {bgp_id} <-> {peer_session_id}")
        except Exception as e:
            logger.error(f"Failed to link BGP sessions: {e}")

async def sync_l3vpn_service(body, spec, name, uid, logger):
    logger.debug(f"Syncing L3VPNService {name}")
    
    # Extract status from CRD
    l3vpn_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        l3vpn_status = status_obj['phase']
    else:
        status_str = get_status(body)
        if status_str != 'NULL':
            l3vpn_status = status_str
    
    # Track VPN IDs created from this CRD for delete tracking
    vpn_ids_in_crd = []
    
    # 1. Upsert VPN Services (SCD)
    services = spec.get('services', [])
    for svc in services:
        vpn_id = f"vpn:{svc['name']}"
        vpn_ids_in_crd.append(vpn_id)
        customer_id = "cust:default" # Placeholder for customer
        
        # Ensure customer exists (Metadata - not Temporal usually, but good to keep)
        def sql_upsert_cust(transaction):
             transaction.execute_update(
                SQL_TEMPLATES['upsert_customer'],
                params={'id': customer_id, 'name': 'Default Customer', 'type': 'Internal', 'properties': '{}'},
                param_types={'id': spanner.param_types.STRING, 'name': spanner.param_types.STRING, 'type': spanner.param_types.STRING, 'properties': spanner.param_types.JSON}
             )
        try:
             db_container['db'].run_in_transaction(sql_upsert_cust)
        except:
             pass

        # Prepare L3VPN data
        vpn_name = svc['name']
        service_type = svc.get('type', 'L3VPN')
        topology = svc.get('topology', 'Mesh')
        vpn_config = json.dumps(dict(svc) if hasattr(svc, '__iter__') and not isinstance(svc, (str, bytes)) else svc)

        def sql_upsert_l3vpn(transaction):
            # 1. Get active VPN
            results = transaction.execute_sql(
                SQL_TEMPLATES['get_active_l3vpn'],
                params={'id': vpn_id},
                param_types={'id': spanner.param_types.STRING}
            )
            row = results.one_or_none()

            need_insert = True
            if row:
                existing_config = row[0]
                existing_status = row[1]
                if existing_config == vpn_config and existing_status == l3vpn_status.lower():
                    need_insert = False
                else:
                    # Close existing
                    transaction.execute_update(
                        SQL_TEMPLATES['close_l3vpn'],
                        params={'id': vpn_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
            
            if need_insert:
                transaction.execute_update(
                    SQL_TEMPLATES['insert_l3vpn'],
                    params={
                        'id': vpn_id, 
                        'customer_id': customer_id, 
                        'name': vpn_name, 
                        'service_type': service_type, 
                        'topology': topology, 
                        'status': l3vpn_status.lower(), 
                        'config': vpn_config
                    },
                    param_types={
                        'id': spanner.param_types.STRING, 
                        'customer_id': spanner.param_types.STRING, 
                        'name': spanner.param_types.STRING, 
                        'service_type': spanner.param_types.STRING, 
                        'topology': spanner.param_types.STRING, 
                        'status': spanner.param_types.STRING, 
                        'config': spanner.param_types.JSON
                    }
                )
        try:
            db_container['db'].run_in_transaction(sql_upsert_l3vpn)
            logger.debug(f"Upserted L3VPNService {vpn_id}")
        except Exception as e:
            logger.error(f"Failed to upsert L3VPNService {vpn_id}: {e}")
            continue

        # 2. Sync VRFs (SCD)
        routers = svc.get('routers', [])
        for r in routers:
            router_name = r.get('router_name')
            if not router_name: continue
            
            router_id = await _get_router_id_by_name(router_name)
            if not router_id:
                logger.warning(f"Router {router_name} not found for VRF in {vpn_id}")
                continue
                
            vrf_id = f"vrf:{router_name}:{svc['name']}"
            rd = r.get('rd', 'unknown')
            vrf_status = 'Active' if l3vpn_status == 'Ready' else 'Pending'
            vrf_config = json.dumps(dict(r) if hasattr(r, '__iter__') and not isinstance(r, (str, bytes)) else r)

            def sql_upsert_vrf(transaction):
                # 1. Get active VRF
                results = transaction.execute_sql(
                    SQL_TEMPLATES['get_active_vrf'],
                    params={'id': vrf_id},
                    param_types={'id': spanner.param_types.STRING}
                )
                row = results.one_or_none()

                need_insert = True
                if row:
                    existing_config = row[0]
                    existing_status = row[1]
                    if existing_config == vrf_config and existing_status == vrf_status.lower():
                        need_insert = False
                    else:
                        # Close existing
                        transaction.execute_update(
                            SQL_TEMPLATES['close_vrf'],
                            params={'id': vrf_id},
                            param_types={'id': spanner.param_types.STRING}
                        )
                
                if need_insert:
                    transaction.execute_update(
                        SQL_TEMPLATES['insert_vrf'],
                        params={
                            'id': vrf_id,
                            'router_id': router_id,
                            'vpn_id': vpn_id,
                            'name': f"VRF-{svc['name']}",
                            'rd': rd,
                            'status': vrf_status.lower(),
                            'config': vrf_config
                        },
                        param_types={
                            'id': spanner.param_types.STRING,
                            'router_id': spanner.param_types.STRING,
                            'vpn_id': spanner.param_types.STRING,
                            'name': spanner.param_types.STRING,
                            'rd': spanner.param_types.STRING,
                            'status': spanner.param_types.STRING,
                            'config': spanner.param_types.JSON
                        }
                    )
            try:
                db_container['db'].run_in_transaction(sql_upsert_vrf)
                logger.debug(f"Upserted VRF {vrf_id}")
            except Exception as e:
                logger.error(f"Failed to upsert VRF {vrf_id}: {e}")
                continue

            # 3. Sync BGP Sessions (SCD)
            neighbors = r.get('neighbors', [])
            router_local_as = r.get('local_as', 65000)
            
            for n in neighbors:
                peer_ip = n.get('peer_ip')
                if not peer_ip: continue
                
                bgp_id = f"bgp:{router_name}:{svc['name']}:{peer_ip}"
                remote_as = n.get('remote_as', 0)
                bgp_status = 'Established' if l3vpn_status == 'Ready' else 'Idle'
                bgp_config = json.dumps(dict(n) if hasattr(n, '__iter__') and not isinstance(n, (str, bytes)) else n)

                def sql_upsert_bgp(transaction):
                    # 1. Get active BGP
                    results = transaction.execute_sql(
                        SQL_TEMPLATES['get_active_bgp'],
                        params={'id': bgp_id},
                        param_types={'id': spanner.param_types.STRING}
                    )
                    row = results.one_or_none()

                    need_insert = True
                    if row:
                        existing_config = row[0]
                        existing_status = row[1]
                        if existing_config == bgp_config and existing_status == bgp_status.lower():
                            need_insert = False
                        else:
                            # Close existing
                            transaction.execute_update(
                                SQL_TEMPLATES['close_bgp'],
                                params={'id': bgp_id},
                                param_types={'id': spanner.param_types.STRING}
                            )
                    
                    if need_insert:
                        transaction.execute_update(
                            SQL_TEMPLATES['insert_bgp'],
                            params={
                                'id': bgp_id,
                                'vrf_id': vrf_id,
                                'local_as': router_local_as,
                                'remote_as': remote_as,
                                'peer_ip': peer_ip,
                                'status': bgp_status.lower(),
                                'config': bgp_config
                            },
                            param_types={
                                'id': spanner.param_types.STRING,
                                'vrf_id': spanner.param_types.STRING,
                                'local_as': spanner.param_types.INT64,
                                'remote_as': spanner.param_types.INT64,
                                'peer_ip': spanner.param_types.STRING,
                                'status': spanner.param_types.STRING,
                                'config': spanner.param_types.JSON
                            }
                        )
                try:
                    db_container['db'].run_in_transaction(sql_upsert_bgp)
                except Exception as e:
                    logger.error(f"Failed to upsert BGP {bgp_id}: {e}")
                    continue
                
                # Create BGP peering relationships (bidirectional) - Edge Table (stateless for now)
                await _create_bgp_peering(bgp_id, peer_ip, svc['name'], logger)
    
    return vpn_ids_in_crd


async def delete_l3vpn_service(uid):
    """Delete L3VPN service and cascade delete VRFs and BGP sessions (SCD)"""
    logger.debug(f"Deleting L3VPN Service CRD {uid}")
    
    # We need to find all VPNs that match this CRD's UID pattern
    # VPN IDs are created as f"vpn:{svc['name']}" where svc comes from this CRD
    # Since we can't easily know the names without the spec, we might rely on the vpn_ids returned by sync
    # But K8s finalizer logic usually just gives us the UID.
    # For now, assuming we can partial match or we rely on the implementation that passes names?
    # Actually checking the caller in `lifecycle.py`, it passes the UID.
    # The current implementation used `LIKE 'vpn:%'` which is dangerous if there are multiple services.
    # But wait, looking at `sync_l3vpn_service`, it returns `vpn_ids_in_crd`.
    # Code below assumes we can delete by pattern matching or we need to query first.
    # The original code did `DELETE ... WHERE vpn_id LIKE 'vpn:%'` which deletes EVERYTHING?
    # No, it was likely deleting based on some other logic or just broken in the example I saw.
    # The original snippet showed: `DELETE FROM L3VPNService WHERE id LIKE 'vpn:%'`
    # This looks like it deletes ALL VPNs? That seems wrong for a single CRD delete if multiple CRDs exist.
    # However, if the user only has one L3VPNService CRD managing everything, it's fine.
    # Refactoring to be safer is out of scope, I will just apply the SCD Close to whatever it was selecting.
    # BUT `delete_l3vpn` template takes `@id`. 
    # I will stick to the pattern implicit in the previous code but use SCD updates.
    
    # Actually, proper cleanup requires knowing the IDs. 
    # If the previous code used `LIKE 'vpn:%'`, I will attempt to CLOSE all active VPNs matching that.
    
    def sql_delete(transaction):
        # 1. Close BGP sessions (SCD)
        # Using a broad update based on the assumption that ALL VPNs are being deleted?
        # Or did the previous code filter by something else?
        # Previous code: DELETE FROM BGPSession WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id LIKE 'vpn:%')
        transaction.execute_update(
            "UPDATE BGPSession SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vrf_id IN (SELECT id FROM VRF WHERE vpn_id LIKE 'vpn:%' AND valid_end_ts IS NULL) AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 2. Close VRFs (SCD)
        transaction.execute_update(
            "UPDATE VRF SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE vpn_id LIKE 'vpn:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 3. Close VPN services (SCD)
        transaction.execute_update(
            "UPDATE L3VPNService SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'vpn:%' AND valid_end_ts IS NULL",
            params={},
            param_types={}
        )
        
        # 4. Cleanup OwnedBy edges (L3VPNService -> Customer)
        # This is logical view-based, nothing to delete if it's a View.
        # But if we had an explicit edge table, we'd delete it. 
        # Here `OwnedBy` is a View on L3VPNService, so closing L3VPNService is enough.
    
    try:
        db_container['db'].run_in_transaction(sql_delete)
        logger.debug(f"Successfully closed L3VPN services and related entities for CRD {uid}")
    except Exception as e:
        logger.error(f"Failed to delete L3VPN service {uid}: {e}")


# ------------------------------------------
# Create BGP Peering Relationship
# ------------------------------------------
async def _create_bgp_peering(bgp_session_id, peer_ip, vrf_name, logger):
    """
    Create BGP peering relationship in BGP_Peering table.
    This finds the matching reverse BGP session and creates the peering link.
    """
    # Query to find the reverse BGP session (where local peer IP matches our peer_ip)
    query = """
        SELECT id FROM BGPSession 
        WHERE peer_ip != @peer_ip 
        AND vrf_id LIKE @vrf_pattern
        LIMIT 10
    """
    
    try:
        with db_container['db'].snapshot() as snapshot:
            results = snapshot.execute_sql(
                query,
                params={
                    'peer_ip': peer_ip,
                    'vrf_pattern': f'%:{vrf_name}'
                },
                param_types={
                    'peer_ip': spanner.param_types.STRING,
                    'vrf_pattern': spanner.param_types.STRING
                }
            )
            
            for row in results:
                peer_bgp_id = row[0]
                
                # Create bidirectional peering entries
                def sql_insert_peering(transaction):
                    # Insert both directions
                    transaction.execute_update(
                        "INSERT OR IGNORE INTO BGP_Peering (session_id_a, session_id_b) VALUES (@id_a, @id_b)",
                        params={'id_a': bgp_session_id, 'id_b': peer_bgp_id},
                        param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
                    )
                    transaction.execute_update(
                        "INSERT OR IGNORE INTO BGP_Peering (session_id_a, session_id_b) VALUES (@id_a, @id_b)",
                        params={'id_a': peer_bgp_id, 'id_b': bgp_session_id},
                        param_types={'id_a': spanner.param_types.STRING, 'id_b': spanner.param_types.STRING}
                    )
                
                db_container['db'].run_in_transaction(sql_insert_peering)
                logger.debug(f"Created BGP peering: {bgp_session_id} <-> {peer_bgp_id}")
                
    except Exception as e:
        logger.debug(f"Could not create BGP peering for {bgp_session_id}: {e}")


# ------------------------------------------
# Sync Device
# ------------------------------------------
async def sync_device(body, spec, name, uid, logger):
    """Sync Device to Spanner database (SCD Type 2)"""
    logger.debug(f"Syncing Device {name}")
    
    device_id = f"device:{name}"
    network_name = spec.get('network_name', '')
    ip_address = spec.get('ip_address', '')
    gateway = spec.get('gateway')
    vlan = spec.get('vlan')
    
    # Extract status from CRD
    device_status = 'Unknown'
    status_obj = body.get('status', {})
    if 'phase' in status_obj:
        device_status = status_obj['phase']
    
    # Prepare config (sanitized body)
    # Convert kopf Body object to dict first
    body_dict = dict(body) if not isinstance(body, dict) else body
    sanitized_body = sanitize_k8s_body(body_dict)
    config_json = json.dumps(sanitized_body)
    
    # Find the router this device connects to
    # Match by gateway IP - the gateway should be a router interface IP
    router_id = None
    
    if gateway:
        # Query to find router with interface matching the gateway IP
        sql_find_router = """
            SELECT DISTINCT r.id 
            FROM PhysicalRouter r
            JOIN PhysicalInterface i ON r.id = i.router_id
            WHERE i.ip_address = @gateway_ip
              AND r.valid_end_ts IS NULL
              AND i.valid_end_ts IS NULL
            LIMIT 1
        """
        
        try:
            with db_container['db'].snapshot() as snapshot:
                results = snapshot.execute_sql(
                    sql_find_router,
                    params={'gateway_ip': gateway},
                    param_types={'gateway_ip': spanner.param_types.STRING}
                )
                row = results.one_or_none()
                if row:
                    router_id = row[0]
                    logger.debug(f"Found router {router_id} for device {name} via gateway {gateway}")
                else:
                    logger.warning(f"No router found with interface IP {gateway} for device {name}")
        except Exception as e:
            logger.warning(f"Could not find router for device {name} via gateway {gateway}: {e}")
    else:
        logger.warning(f"Device {name} has no gateway specified, cannot determine connected router")
    
    def sql_upsert_device(transaction):
        # 1. Get active device
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_device'],
            params={'id': device_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()
        
        need_insert = True
        if row:
            # SELECT router_id, network_name, ip_address, gateway, vlan, status, config
            existing_router_id = row[0]
            existing_network = row[1]
            existing_ip = row[2]
            existing_gateway = row[3]
            existing_vlan = row[4]
            existing_status = row[5]
            existing_config = row[6]
            
            # Compare content
            if (existing_router_id == router_id and
                existing_network == network_name and
                existing_ip == ip_address and
                existing_gateway == gateway and
                existing_vlan == vlan and
                existing_status == device_status and
                existing_config == sanitized_body):
                need_insert = False
            else:
                # Close existing row
                transaction.execute_update(
                    SQL_TEMPLATES['close_device'],
                    params={'id': device_id},
                    param_types={'id': spanner.param_types.STRING}
                )
        
        if need_insert:
            transaction.execute_update(
                SQL_TEMPLATES['insert_device'],
                params={
                    'id': device_id,
                    'name': name,
                    'router_id': router_id,
                    'network_name': network_name,
                    'ip_address': ip_address,
                    'gateway': gateway,
                    'vlan': vlan,
                    'status': device_status.lower(),
                    'config': config_json
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'router_id': spanner.param_types.STRING,
                    'network_name': spanner.param_types.STRING,
                    'ip_address': spanner.param_types.STRING,
                    'gateway': spanner.param_types.STRING,
                    'vlan': spanner.param_types.INT64,
                    'status': spanner.param_types.STRING,
                    'config': spanner.param_types.JSON
                }
            )
    
    try:
        db_container['db'].run_in_transaction(sql_upsert_device)
        logger.info(f"Successfully synced Device {name} to Spanner")
    except Exception as e:
        logger.error(f"Failed to sync Device {name} to Spanner: {e}")


async def delete_device(uid, name=None):
    """Delete device from Spanner (SCD Type 2 - close the row)"""
    device_id = f"device:{name}" if name else uid
    logger.debug(f"Deleting Device {device_id}")
    
    def sql_delete_device(transaction):
        transaction.execute_update(
            SQL_TEMPLATES['delete_device'],
            params={'id': device_id},
            param_types={'id': spanner.param_types.STRING}
        )
    
    try:
        db_container['db'].run_in_transaction(sql_delete_device)
        logger.info(f"Successfully closed Device {device_id} in Spanner")
    except Exception as e:
        logger.error(f"Failed to delete Device {device_id} from Spanner: {e}")

# ------------------------------------------
# Sync Linux Network Bridge to Spanner
# ------------------------------------------
async def sync_host_network_bridge(body, spec, name, namespace, bridge_status, logger):
    """Sync Linux bridge state to LogicalSubnet table (SCD Type 2)"""
    logger.debug(f"Syncing bridge state for {name}")
    
    subnet_id = f"subnet:{spec.get('name', name)}"
    
    # Extract state from bridge_status
    operational_state = bridge_status.get('operational_state', 'unknown').upper()
    mtu = bridge_status.get('mtu', 1500)
    mac_address = bridge_status.get('mac_address', '')
    bridge_ip = bridge_status.get('ip_address', '')
    host_device_name = spec.get('name', name)
    
    # Structural properties only — these drive the SCD change detection.
    # Metrics (counters) are intentionally excluded: they change every monitoring
    # cycle and belong in NetworkMetrics, not in the topology SCD row.
    properties = {
        'network_type': spec.get('network_type'),
        'bandwidth': spec.get('bandwidth'),
        'gateway': spec.get('gateway'),
        'vlan': spec.get('vlan'),
    }
    
    def sql_upsert_subnet(transaction):
        # Get active subnet - extended query for bridge fields
        results = transaction.execute_sql(
            """SELECT operational_state, mtu, mac_address, bridge_ip, host_device_name, properties 
               FROM LogicalSubnet 
               WHERE id = @id AND valid_end_ts IS NULL""",
            params={'id': subnet_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()
        
        need_insert = True
        if row:
            existing_state = row[0] if row[0] else 'unknown'
            existing_mtu = row[1] if row[1] else 0
            existing_mac = row[2] if row[2] else ''
            existing_ip = row[3] if row[3] else ''
            existing_device = row[4] if row[4] else ''
            existing_props = row[5] if row[5] else {}
            
            # Compare
            if (existing_state == operational_state and
                existing_mtu == mtu and
                existing_mac == mac_address and
                existing_ip == bridge_ip and
                existing_device == host_device_name and
                existing_props == properties):
                need_insert = False
                logger.debug(f"LogicalSubnet {subnet_id} unchanged, skipping Spanner write")
            else:
                # Close existing
                logger.debug(f"Bridge state changed for {subnet_id}, closing old row")
                transaction.execute_update(
                    SQL_TEMPLATES['close_subnet'],
                    params={'id': subnet_id},
                    param_types={'id': spanner.param_types.STRING}
                )
        
        if need_insert:
            # Insert new row with bridge operational state
            # Note: Assumes LogicalSubnet table has these columns
            logger.debug(f"Inserting new LogicalSubnet row for {subnet_id}")
            transaction.execute_update(
                """INSERT LogicalSubnet 
                   (id, cidr, network_type, description, operational_state, mtu, mac_address, 
                    bridge_ip, host_device_name, properties, valid_start_ts, valid_end_ts) 
                   VALUES (@id, @cidr, @network_type, @description, @operational_state, @mtu, 
                           @mac_address, @bridge_ip, @host_device_name, @properties, 
                           PENDING_COMMIT_TIMESTAMP(), NULL)""",
                params={
                    'id': subnet_id,
                    'cidr': spec.get('subnet', ''),
                    'network_type': spec.get('network_type', 'unknown'),
                    'description': f"Bridge: {host_device_name}",
                    'operational_state': operational_state,
                    'mtu': mtu,
                    'mac_address': mac_address,
                    'bridge_ip': bridge_ip,
                    'host_device_name': host_device_name,
                    'properties': json.dumps(properties)
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'cidr': spanner.param_types.STRING,
                    'network_type': spanner.param_types.STRING,
                    'description': spanner.param_types.STRING,
                    'operational_state': spanner.param_types.STRING,
                    'mtu': spanner.param_types.INT64,
                    'mac_address': spanner.param_types.STRING,
                    'bridge_ip': spanner.param_types.STRING,
                    'host_device_name': spanner.param_types.STRING,
                    'properties': spanner.param_types.JSON
                }
            )
    
    try:
        db_container['db'].run_in_transaction(sql_upsert_subnet)
        logger.debug(f"Successfully synced LogicalSubnet {subnet_id} with bridge state")
    except Exception as e:
        logger.error(f"Failed to sync LogicalSubnet {subnet_id}: {e}")
        return
    
    # Sync veth pairs as host-side PhysicalInterfaces and PhysicalLinks
    veth_pairs = bridge_status.get('veth_pairs', [])
    if veth_pairs:
        await sync_veth_pairs(subnet_id, veth_pairs, logger)
    
    # Sync metrics to NetworkMetrics table (optional)
    if bridge_status.get('metrics'):
        await sync_network_metrics(subnet_id, 'LogicalSubnet', bridge_status['metrics'], logger)


async def _resolve_container_interface_id(router_prefix: str, interface_name: str, logger) -> str:
    """
    Look up the correct container PhysicalInterface ID from Spanner using the truncated
    router name prefix extracted from the veth name.

    Veth names are created as '{router_name[:8]}-{iface_name}', so the prefix stored in the
    veth name is only 8 characters of the full router name.  We query Spanner for a
    PhysicalInterface whose name matches the interface name and whose router_id starts with
    'router:{router_prefix}', giving us the correct full-name-based ID.

    Falls back to the prefix-based ID string if no match is found (so the link is still
    written to Spanner and can be repaired once the router is created).
    """
    fallback = f"router:{router_prefix}:interface:{interface_name}"
    try:
        with db_container['db'].snapshot() as snapshot:
            results = snapshot.execute_sql(
                """SELECT id FROM PhysicalInterface
                   WHERE name = @iface_name
                     AND router_id LIKE @router_pattern
                     AND valid_end_ts IS NULL
                   LIMIT 1""",
                params={
                    'iface_name': interface_name,
                    'router_pattern': f'router:{router_prefix}%'
                },
                param_types={
                    'iface_name': spanner.param_types.STRING,
                    'router_pattern': spanner.param_types.STRING
                }
            )
            row = results.one_or_none()
            if row:
                resolved = row[0]
                if resolved != fallback:
                    logger.debug(
                        f"Resolved container interface id {resolved} "
                        f"(prefix '{router_prefix}' → full router name)"
                    )
                return resolved
    except Exception as e:
        logger.debug(f"Could not resolve container interface for {router_prefix}/{interface_name}: {e}")
    
    logger.debug(
        f"No PhysicalInterface found for prefix '{router_prefix}' / iface '{interface_name}', "
        f"using fallback id '{fallback}' – will heal once router is created"
    )
    return fallback


async def sync_veth_pairs(subnet_id, veth_pairs, logger):
    """Sync veth pairs as PhysicalLinks (SCD Type 2).

    Each veth pair is modelled as:
      - PhysicalLink  — the virtual cable between the bridge and the container interface.
      - Interface_Link  — container-side PhysicalInterface → PhysicalLink.
      - Subnet_Association  — container-side PhysicalInterface → bridge LogicalSubnet.

    The Linux host VM is NOT modelled as a PhysicalRouter entity; the host-side veth
    name is stored in PhysicalLink.properties for reference only.  Connectivity queries
    ("what interfaces are connected to this bridge?") use the Subnet_Association edge.
    """
    for veth_data in veth_pairs:
        if not isinstance(veth_data, dict):
            logger.warning(f"Skipping non-dict entry in veth_pairs (got {type(veth_data).__name__}): {veth_data!r}")
            continue
        veth_name = veth_data.get('VETH')
        if not veth_name:
            continue

        # Parse router name prefix and interface name from veth name.
        # Veth names are created as '{router_name[:8]}-{iface_name}' so the prefix is
        # only 8 chars of the full router name.  We resolve the real interface ID via
        # a Spanner lookup in _resolve_container_interface_id.
        try:
            router_part = veth_name.rsplit('-', 1)[0]
            interface_part = veth_name.rsplit('-', 1)[1]

            # host_veth_name is a label stored in PhysicalLink.properties — not a Spanner entity
            host_veth_name = f"host:veth:{veth_name}"
            veth_link_id = f"link:veth:{router_part}:{interface_part}"

            # Resolve the correct full-name container interface ID from Spanner
            container_interface_id = await _resolve_container_interface_id(
                router_part, interface_part, logger
            )
        except Exception:
            logger.warning(f"Could not parse router/interface from veth name: {veth_name}")
            continue

        host_state = veth_data.get('STATE', 'unknown')
        bandwidth_limit = veth_data.get('BW', 'none')

        # 1. Sync veth pair as PhysicalLink (topology/state only — no counters)
        await _sync_veth_link(
            veth_link_id, host_veth_name, container_interface_id,
            bandwidth_limit, host_state, logger
        )

        # 2. Associate the container interface with the bridge LogicalSubnet so that
        #    graph queries can answer "which interfaces are on bridge X?" via
        #    the AssociatedWith_Edge (PhysicalInterface → LogicalSubnet).
        await _sync_container_bridge_association(container_interface_id, subnet_id, logger)

        # 3. Send per-cycle veth counters to NetworkMetrics (time-series, not topology SCD)
        veth_counters = {
            'rx_packets': int(veth_data.get('RX_PKTS', 0)),
            'tx_packets': int(veth_data.get('TX_PKTS', 0)),
            'rx_bytes':   int(veth_data.get('RX_BYTES', 0)),
            'tx_bytes':   int(veth_data.get('TX_BYTES', 0)),
        }
        await sync_network_metrics(veth_link_id, 'PhysicalLink', veth_counters, logger)


async def _sync_container_bridge_association(container_interface_id, subnet_id, logger):
    """Create (or confirm) the Subnet_Association between a container interface and its bridge.

    This is the SCD-safe version: if the association already exists it is left unchanged;
    otherwise a new row is inserted.  The bridge LogicalSubnet is identified by subnet_id
    (e.g. 'subnet:mgmt-net') — it must already exist in Spanner before this is called.
    """
    def sql_upsert_assoc(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_subnet_assoc'],
            params={'entity_id': container_interface_id, 'subnet_id': subnet_id},
            param_types={
                'entity_id': spanner.param_types.STRING,
                'subnet_id': spanner.param_types.STRING
            }
        )
        if not results.one_or_none():
            transaction.execute_update(
                SQL_TEMPLATES['insert_subnet_assoc'],
                params={
                    'entity_id': container_interface_id,
                    'subnet_id': subnet_id,
                    'entity_type': 'Interface'
                },
                param_types={
                    'entity_id': spanner.param_types.STRING,
                    'subnet_id': spanner.param_types.STRING,
                    'entity_type': spanner.param_types.STRING
                }
            )

    try:
        db_container['db'].run_in_transaction(sql_upsert_assoc)
        logger.debug(f"Associated container interface {container_interface_id} with bridge {subnet_id}")
    except Exception as e:
        logger.error(f"Failed to associate {container_interface_id} with bridge {subnet_id}: {e}")


async def _sync_veth_link(link_id, host_veth_name, container_iface_id, bandwidth, state, logger):
    """Sync veth pair as PhysicalLink (SCD Type 2).

    Only structural/topology fields are stored in PhysicalLink.properties and used for
    change detection.  Per-cycle counter data (rx/tx bytes/packets) is intentionally
    excluded from here — callers must send those to NetworkMetrics separately via
    sync_network_metrics() to avoid creating a new SCD row on every monitoring cycle.
    """
    
    # Structural properties only — drives SCD equality check.
    # host_veth_name is stored as a reference label; it is NOT a Spanner entity.
    properties = {
        'host_veth': host_veth_name,
        'container_interface': container_iface_id,
    }
    
    def sql_upsert_link(transaction):
        results = transaction.execute_sql(
            SQL_TEMPLATES['get_active_phy_link'],
            params={'id': link_id},
            param_types={'id': spanner.param_types.STRING}
        )
        row = results.one_or_none()
        
        need_insert = True
        if row and row[1] == state and row[0] == properties:
            need_insert = False
            logger.debug(f"Veth link {link_id} unchanged, skipping write")
        else:
            if row:
                logger.debug(f"Veth link {link_id} changed, closing old row")
                transaction.execute_update(
                    SQL_TEMPLATES['close_phy_link'],
                    params={'id': link_id},
                    param_types={'id': spanner.param_types.STRING}
                )
        
        if need_insert:
            logger.debug(f"Inserting new PhysicalLink for veth {link_id}")
            transaction.execute_update(
                SQL_TEMPLATES['insert_phy_link'],
                params={
                    'id': link_id,
                    'name': f"veth: {host_veth_name} ↔ {container_iface_id}",
                    'bandwidth': bandwidth if bandwidth != 'none' else 'N/A',
                    'status': state.lower(),
                    'properties': json.dumps(properties)
                },
                param_types={
                    'id': spanner.param_types.STRING,
                    'name': spanner.param_types.STRING,
                    'bandwidth': spanner.param_types.STRING,
                    'status': spanner.param_types.STRING,
                    'properties': spanner.param_types.JSON
                }
            )
            
            # Create Interface_Link for the container-side interface only.
            # The host-side veth is NOT a Spanner PhysicalInterface entity; connectivity
            # to the bridge is expressed via Subnet_Association (see sync_veth_pairs).
            def sql_create_link_assoc(transaction2):
                results = transaction2.execute_sql(
                    SQL_TEMPLATES['get_active_interface_link'],
                    params={'interface_id': container_iface_id, 'link_id': link_id},
                    param_types={'interface_id': spanner.param_types.STRING, 'link_id': spanner.param_types.STRING}
                )
                if not results.one_or_none():
                    transaction2.execute_update(
                        SQL_TEMPLATES['insert_interface_link'],
                        params={'interface_id': container_iface_id, 'link_id': link_id},
                        param_types={'interface_id': spanner.param_types.STRING, 'link_id': spanner.param_types.STRING}
                    )
            
            # Run link association in same transaction
            sql_create_link_assoc(transaction)
    
    try:
        db_container['db'].run_in_transaction(sql_upsert_link)
        logger.debug(f"Synced veth link {link_id}")
    except Exception as e:
        logger.error(f"Failed to sync veth link {link_id}: {e}")


async def sync_network_metrics(entity_id, entity_type, metrics, logger):
    """Sync network metrics to NetworkMetrics table. (Currently disabled.)"""
    return  # Operator-sourced NetworkMetrics writes are disabled; remove this line to re-enable.

    """Writes one row per metric key-value pair, matching the schema and format
    produced by the metricscollector service:
      columns: (timestamp, node_name, metric_name, metric_type, kind, value, labels, interface)

    Args:
        entity_id:   ID of the entity being measured (used as node_name and interface).
        entity_type: Type of entity (e.g. 'PhysicalLink', 'LogicalSubnet') — stored as kind.
        metrics:     Dict of {metric_name: numeric_value} pairs.
        logger:      Logger instance.
    """
    from datetime import datetime

    if not metrics:
        return

    timestamp = datetime.utcnow()
    labels_json = json.dumps({})  # No additional labels for operator-collected metrics

    # Build one row per metric in the metricscollector column order:
    # (timestamp, node_name, metric_name, metric_type, kind, value, labels, interface)
    rows = []
    for metric_name, raw_value in metrics.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            logger.warning(f"Skipping non-numeric metric {metric_name}={raw_value} for {entity_id}")
            continue
        rows.append((
            timestamp,
            entity_id,    # node_name
            metric_name,  # metric_name
            "gauge",      # metric_type — bridge/veth counters are instantaneous snapshots
            entity_type,  # kind
            value,        # value
            labels_json,  # labels
            entity_id,    # interface
        ))

    if not rows:
        return

    try:
        with db_container['db'].batch() as batch:
            batch.insert(
                table="NetworkMetrics",
                columns=("timestamp", "node_name", "metric_name", "metric_type",
                         "kind", "value", "labels", "interface"),
                values=rows
            )
        logger.debug(f"Inserted {len(rows)} metric row(s) for {entity_id}")
    except Exception as e:
        logger.error(f"Failed to sync metrics for {entity_id}: {e}")

