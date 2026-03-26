# Linux Network State Monitoring Tests

This directory contains test configurations for validating the Linux network bridge and veth pair state monitoring functionality.

## Test Files

### 1. `test-networks-basic.yaml`
Basic network configurations without routers. Use this to test:
- Bridge creation and operational state
- Bridge metrics collection (rx/tx packets, bytes, errors)
- K8s status updates
- Spanner LogicalSubnet sync

### 2. `test-networks-with-routers.yaml`
Networks with VyOS routers attached. Use this to test:
- Veth pair creation and monitoring
- Host-side veth PhysicalInterface creation
- PhysicalLink creation for veth pairs
- Interface_Link edge table population
- Complete graph connectivity (bridge→veth→router)

---

## Test Scenarios

### Scenario 1: Basic Bridge State Monitoring

**Deploy test networks:**
```bash
kubectl apply -f environment/telco-lab/tests/test-networks-basic.yaml
```

**Verify networks are ready:**
```bash
kubectl get linuxnetwork -n automation
kubectl describe linuxnetwork test-network-1 -n automation
```

**Check K8s status (should show operational_state):**
```bash
kubectl get linuxnetwork test-network-1 -n automation -o jsonpath='{.status}'
```

**Query Spanner for bridge state:**
```sql
-- Check LogicalSubnet entries
SELECT 
  id, 
  operational_state, 
  mtu, 
  mac_address, 
  bridge_ip,
  host_device_name,
  valid_start_ts,
  valid_end_ts
FROM LogicalSubnet 
WHERE id LIKE 'subnet:test-net-%'
ORDER BY valid_start_ts DESC;
```

```bash
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, operational_state, mtu, mac_address, bridge_ip, host_device_name, valid_start_ts, valid_end_ts FROM LogicalSubnet WHERE id LIKE 'subnet:test-net-%' ORDER BY valid_start_ts DESC"
```

**Wait 60 seconds for monitor cycle:**
```bash
# Monitor should run every 60 seconds
sleep 65
kubectl logs -n automation deployment/operator -f | grep "test-net"
```

**Expected logs:**
- "Getting detailed status for network: test-net-1"
- "LogicalSubnet subnet:test-net-1 unchanged, skipping Spanner write" (if no state change)

---

### Scenario 2: State Change Detection

**Bring a bridge down manually (SSH to network VM):**
```bash
# SSH to network VM
gcloud compute ssh networkvm --zone=<your-zone>

# Bring bridge down
sudo ip link set test-net-1 down

# Check state
brctl show test-net-1
ip link show test-net-1
```

**Verify state change detection (wait up to 60 seconds):**
```bash
# Watch K8s status
kubectl get linuxnetwork test-network-1 -n automation -o jsonpath='{.status}' -w

# Should see operational_state change to "DOWN"
```

**Query Spanner temporal history:**
```sql
-- Should see 2 rows: one with state=UP (closed), one with state=DOWN (active)
SELECT 
  id, 
  operational_state, 
  valid_start_ts,
  valid_end_ts,
  CASE 
    WHEN valid_end_ts IS NULL THEN 'ACTIVE'
    ELSE 'CLOSED'
  END as row_status
FROM LogicalSubnet 
WHERE id = 'subnet:test-net-1'
ORDER BY valid_start_ts DESC;
```

```bash
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, operational_state, valid_start_ts, valid_end_ts, CASE WHEN valid_end_ts IS NULL THEN 'ACTIVE' ELSE 'CLOSED' END as row_status FROM LogicalSubnet WHERE id = 'subnet:test-net-1' ORDER BY valid_start_ts DESC"
```

**Bring bridge back up:**
```bash
sudo ip link set test-net-1 up
```

**Verify state restoration:**
- Wait 60 seconds for next monitor cycle
- Should see 3 rows in Spanner (UP→DOWN→UP)

---

### Scenario 3: Veth Pair Monitoring

**Deploy networks with routers:**
```bash
kubectl apply -f environment/telco-lab/tests/test-networks-with-routers.yaml
```

**Wait for all resources to be ready:**
```bash
kubectl wait --for=condition=Ready linuxnetwork/test-data-net -n automation --timeout=120s
kubectl wait --for=condition=Ready linuxnetwork/test-mgmt-net -n automation --timeout=120s
kubectl wait --for=condition=Ready vyosrouter/test-router-1 -n automation --timeout=180s
kubectl wait --for=condition=Ready vyosrouter/test-router-2 -n automation --timeout=180s
```

**Verify veth pairs created (SSH to network VM):**
```bash
# Check bridges and attached veths
brctl show test-data
brctl show test-mgmt

# Should show veth pairs like:
# test-router-1-eth0
# test-router-1-eth1
# test-router-2-eth0
# test-router-2-eth1
```

**Query Spanner for veth state:**
```sql
-- Check host router exists
SELECT * FROM PhysicalRouter 
WHERE id = 'router:host:networkvm' 
AND valid_end_ts IS NULL;

-- Check host-side veth interfaces
SELECT 
  id,
  name,
  media_type,
  speed,
  status,
  valid_start_ts
FROM PhysicalInterface 
WHERE router_id = 'router:host:networkvm'
AND valid_end_ts IS NULL
ORDER BY name;

-- Check veth pair links
SELECT 
  id,
  name,
  bandwidth,
  status,
  properties,
  valid_start_ts
FROM PhysicalLink 
WHERE id LIKE 'link:veth:test-router-%'
AND valid_end_ts IS NULL;

-- Check Interface_Link associations
SELECT 
  il.interface_id,
  il.link_id,
  i.name as interface_name,
  l.name as link_name
FROM Interface_Link il
JOIN PhysicalInterface i ON il.interface_id = i.id
JOIN PhysicalLink l ON il.link_id = l.id
WHERE il.link_id LIKE 'link:veth:test-router-%'
AND il.valid_end_ts IS NULL
AND i.valid_end_ts IS NULL
AND l.valid_end_ts IS NULL;
```

```bash
# Check host router exists
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT * FROM PhysicalRouter WHERE id = 'router:host:networkvm' AND valid_end_ts IS NULL"

# Check host-side veth interfaces
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, name, media_type, speed, status, valid_start_ts FROM PhysicalInterface WHERE router_id = 'router:host:networkvm' AND valid_end_ts IS NULL ORDER BY name"

# Check veth pair links
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, name, bandwidth, status, properties, valid_start_ts FROM PhysicalLink WHERE id LIKE 'link:veth:test-router-%' AND valid_end_ts IS NULL"

# Check Interface_Link associations
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT il.interface_id, il.link_id, i.name as interface_name, l.name as link_name FROM Interface_Link il JOIN PhysicalInterface i ON il.interface_id = i.id JOIN PhysicalLink l ON il.link_id = l.id WHERE il.link_id LIKE 'link:veth:test-router-%' AND il.valid_end_ts IS NULL AND i.valid_end_ts IS NULL AND l.valid_end_ts IS NULL"
```

**Test veth state change:**
```bash
# Bring down a veth pair (SSH to network VM)
sudo ip link set test-router-1-eth0 down

# Wait 60 seconds for monitor cycle
# Check Spanner for state change
```

```sql
-- Should see veth interface state changed to "down"
SELECT 
  id,
  status,
  valid_start_ts,
  valid_end_ts
FROM PhysicalInterface 
WHERE id = 'host:veth:test-router-1-eth0'
ORDER BY valid_start_ts DESC;
```

```bash
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, status, valid_start_ts, valid_end_ts FROM PhysicalInterface WHERE id = 'host:veth:test-router-1-eth0' ORDER BY valid_start_ts DESC"
```

---

### Scenario 4: Metrics Collection

**Query network metrics:**
```sql
-- Check if metrics are being collected
SELECT 
  id,
  kind,
  name,
  timestamp,
  metrics
FROM NetworkMetrics 
WHERE interface_id LIKE 'subnet:test-%'
ORDER BY timestamp DESC
LIMIT 10;
```

```bash
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT id, kind, name, timestamp, metrics FROM NetworkMetrics WHERE interface_id LIKE 'subnet:test-%' ORDER BY timestamp DESC LIMIT 10"
```

**Expected metrics in properties.metrics:**
```json
{
  "rx_packets": 12345,
  "tx_packets": 67890,
  "rx_bytes": 1234567,
  "tx_bytes": 7654321,
  "rx_errors": 0,
  "tx_errors": 0
}
```

---

### Scenario 5: Graph Traversal

**Test complete connectivity graph:**
```sql
-- Find all routers connected to a specific bridge
SELECT DISTINCT
  r.id as router_id,
  r.name as router_name,
  r.role,
  r.status,
  i.name as interface_name,
  l.name as link_name
FROM LogicalSubnet s
JOIN PhysicalLink l ON l.properties->>'$.subnet' = s.cidr OR l.id LIKE CONCAT('link:veth:%')
JOIN Interface_Link il ON il.link_id = l.id
JOIN PhysicalInterface i ON i.id = il.interface_id
JOIN PhysicalRouter r ON r.id = i.router_id
WHERE s.id = 'subnet:test-data'
  AND s.valid_end_ts IS NULL
  AND l.valid_end_ts IS NULL
  AND il.valid_end_ts IS NULL
  AND i.valid_end_ts IS NULL
  AND r.valid_end_ts IS NULL
  AND r.id != 'router:host:networkvm';  -- Exclude host router
```

```bash
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --sql="SELECT DISTINCT r.id as router_id, r.name as router_name, r.role, r.status, i.name as interface_name, l.name as link_name FROM LogicalSubnet s JOIN PhysicalLink l ON l.properties->>'$.subnet' = s.cidr OR l.id LIKE CONCAT('link:veth:%') JOIN Interface_Link il ON il.link_id = l.id JOIN PhysicalInterface i ON i.id = il.interface_id JOIN PhysicalRouter r ON r.id = i.router_id WHERE s.id = 'subnet:test-data' AND s.valid_end_ts IS NULL AND l.valid_end_ts IS NULL AND il.valid_end_ts IS NULL AND i.valid_end_ts IS NULL AND r.valid_end_ts IS NULL AND r.id != 'router:host:networkvm'"
```

---

## Monitoring and Debugging

### Watch operator logs:
```bash
kubectl logs -n automation deployment/operator -f | grep -E "(test-net|test-router|bridge|veth)"
```

### Check for errors:
```bash
kubectl logs -n automation deployment/operator --tail=100 | grep ERROR
```

### Verify Ansible playbook execution:
```bash
# Look for playbook execution logs
kubectl logs -n automation deployment/operator -f | grep "detailed_status_network.yaml"
```

### Manual playbook test (SSH to network VM):
```bash
# Test bridge status collection manually
cat /sys/class/net/test-net-1/operstate
cat /sys/class/net/test-net-1/statistics/rx_packets
brctl show test-net-1
```

---

## Cleanup

**Delete test resources:**
```bash
# Delete routers first
kubectl delete -f environment/telco-lab/tests/test-networks-with-routers.yaml

# Wait for routers to be fully deleted
kubectl wait --for=delete vyosrouter/test-router-1 -n automation --timeout=120s
kubectl wait --for=delete vyosrouter/test-router-2 -n automation --timeout=120s

# Delete basic networks
kubectl delete -f environment/telco-lab/tests/test-networks-basic.yaml

# Verify all cleaned up
kubectl get linuxnetwork -n automation | grep test-
```

**Spanner cleanup (if needed):**
```sql
-- Close all test network entries
UPDATE LogicalSubnet 
SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() 
WHERE id LIKE 'subnet:test-%' AND valid_end_ts IS NULL;

UPDATE PhysicalInterface 
SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() 
WHERE id LIKE 'host:veth:test-router-%' AND valid_end_ts IS NULL;

UPDATE PhysicalLink 
SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() 
WHERE id LIKE 'link:veth:test-router-%' AND valid_end_ts IS NULL;
```

```bash
# Close all test LogicalSubnet entries
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --enable-partitioned-dml \
  --sql="UPDATE LogicalSubnet SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'subnet:test-%' AND valid_end_ts IS NULL"

# Close all test PhysicalInterface entries
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --enable-partitioned-dml \
  --sql="UPDATE PhysicalInterface SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'host:veth:test-router-%' AND valid_end_ts IS NULL"

# Close all test PhysicalLink entries
gcloud spanner databases execute-sql networktopology-db \
  --instance=networktopology-instance \
  --enable-partitioned-dml \
  --sql="UPDATE PhysicalLink SET valid_end_ts = PENDING_COMMIT_TIMESTAMP() WHERE id LIKE 'link:veth:test-router-%' AND valid_end_ts IS NULL"
```

---

## Troubleshooting

### Bridge not showing operational_state in K8s status
- Check operator logs for errors
- Verify Ansible playbook ran successfully
- Ensure detailed_status_network.yaml exists
- Check if bridge exists on network VM

### Veth pairs not appearing in Spanner
- Verify routers are fully deployed
- Check that veth naming follows pattern: `{router-name}-{interface}`
- Ensure host router was created: `router:host:networkvm`
- Check operator logs for parsing errors

### No state changes detected
- Verify monitoring interval (60 seconds)
- Check that state actually changed on the host
- Look for "state unchanged, skipping" log messages
- Ensure comparison logic is working correctly

### Metrics not appearing
- Check if sync_network_metrics is being called
- Verify NetworkMetrics table exists in Spanner
- Check for timestamp format issues
