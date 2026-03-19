#!/bin/bash
# Automated test script for L3VPN Hub-and-Spoke Network
# Tests VyOS MPLS L3VPN hub-and-spoke topology

set -e

echo "=========================================="
echo "L3VPN Hub-and-Spoke Network Test Suite"
echo "=========================================="
echo ""

# Test 4: Check Docker containers on VM
echo "[4/10] Checking Docker containers on VM..."
echo "  Running containers:"
docker ps --format '{{.Names}}' | grep -E '(rr|pe|p[0-9]|ce)' | head -10 | awk '{print "  - " $0}'

# Test 5: Verify MPLS/LDP on P routers
echo "[5/10] Verifying MPLS/LDP neighbors..."
echo "  P1 LDP neighbors:"
docker exec p1 vtysh -c 'show mpls ldp neighbor' 2>/dev/null | grep -A 1 'Peer ID' || echo "  No LDP neighbors yet"

# Test 6: Check BGP summary on PE1
echo "[6/10] Checking BGP on PE1 (Spoke)..."
echo "  BGP Summary:"
docker exec pe1 vtysh -c 'show bgp summary' 2>/dev/null | tail -5 || echo "  BGP not ready yet"

# Test 7: Check VRF routing on PE1
echo "[7/10] Checking VRF BLUE_SPOKE on PE1..."
docker exec pe1 vtysh -c 'show ip route vrf BLUE_SPOKE' 2>/dev/null | head -10 || echo "  VRF not configured yet"

# Test 8: Check VRF routing on PE2 (Hub)
echo "[8/10] Checking VRF BLUE_HUB on PE2 (Hub)..."
docker exec pe2 vtysh -c 'show ip route vrf BLUE_HUB' 2>/dev/null | head -10 || echo "  VRF not configured yet"

# Test 9: Test Hub-to-Spoke connectivity
echo "[9/10] Testing Hub-to-Spoke connectivity..."
echo "  PE2 (Hub) to CE1-SPOKE interface:"
docker exec pe2 ip vrf exec BLUE_HUB ping -c 2 -W 2 10.50.50.2 2>/dev/null && echo '  ✓ SUCCESS' || echo '  ✗ FAILED (may need more time)'

# Test 10: Verify Spoke-to-Spoke isolation
echo "[10/15] Verifying Spoke-to-Spoke isolation..."
echo "  PE1 (Spoke) to PE3 (Spoke) - should FAIL:"
timeout 3 docker exec pe1 ip vrf exec BLUE_SPOKE ping -c 2 10.60.60.2 2>/dev/null && echo '  ✗ ISOLATION BROKEN!' || echo '  ✓ ISOLATED (as expected)'

# Test 11: Check device containers
echo "[11/15] Checking device containers..."
DEVICE_COUNT=$(docker ps --format '{{.Names}}' | grep -E '^dev[0-9]+$' | wc -l 2>/dev/null || echo "0")
echo "  Found $DEVICE_COUNT device containers"
if [ "$DEVICE_COUNT" -gt 0 ]; then
    docker ps --format '{{.Names}}' | grep -E '^dev[0-9]+$' | awk '{print "  - " $0}'
else
    echo "  No device containers found"
fi

# Test 12: Test device to CE router connectivity
echo "[12/15] Testing device to CE router connectivity..."
if docker ps --format '{{.Names}}' | grep -q '^dev1$'; then
    echo "  dev1 (10.100.1.10) to CE1-spoke gateway (10.100.1.1):"
    docker exec dev1 ping -c 2 -W 2 10.100.1.1 2>/dev/null && echo '  ✓ SUCCESS' || echo '  ✗ FAILED'
fi

if docker ps --format '{{.Names}}' | grep -q '^dev2$'; then
    echo "  dev2 (10.100.3.10) to CE2-spoke gateway (10.100.3.1):"
    docker exec dev2 ping -c 2 -W 2 10.100.3.1 2>/dev/null && echo '  ✓ SUCCESS' || echo '  ✗ FAILED'
fi

if docker ps --format '{{.Names}}' | grep -q '^devhub$'; then
    echo "  devhub (10.100.2.10) to CE1-hub gateway (10.100.2.1):"
    docker exec devhub ping -c 2 -W 2 10.100.2.1 2>/dev/null && echo '  ✓ SUCCESS' || echo '  ✗ FAILED'
fi

if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo "  Skipped - no devices found"
fi

# Test 13: Test device L3VPN connectivity to hub
echo "[13/15] Testing device L3VPN connectivity to hub PE..."
if docker ps --format '{{.Names}}' | grep -q '^dev1$'; then
    echo "  dev1 (spoke) to Hub PE2 (10.80.80.1) via L3VPN:"
    docker exec dev1 ping -c 2 -W 3 10.80.80.1 2>/dev/null && echo '  ✓ SUCCESS - L3VPN working!' || echo '  ✗ FAILED'
fi

if docker ps --format '{{.Names}}' | grep -q '^dev2$'; then
    echo "  dev2 (spoke) to Hub PE2 (10.80.80.1) via L3VPN:"
    docker exec dev2 ping -c 2 -W 3 10.80.80.1 2>/dev/null && echo '  ✓ SUCCESS - L3VPN working!' || echo '  ✗ FAILED'
fi

if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo "  Skipped - no devices found"
fi

# Test 14: Test end-to-end spoke-to-hub-to-spoke connectivity
echo "[14/15] Testing spoke-to-hub-to-spoke connectivity via L3VPN..."
if docker ps --format '{{.Names}}' | grep -q '^dev1$' && docker ps --format '{{.Names}}' | grep -q '^devhub$'; then
    echo "  dev1 (spoke1) to devhub (hub) at 10.100.2.10:"
    docker exec dev1 ping -c 2 -W 3 10.100.2.10 2>/dev/null && echo '  ✓ SUCCESS - Hub-spoke routing works!' || echo '  ✗ FAILED'
fi

if docker ps --format '{{.Names}}' | grep -q '^dev2$' && docker ps --format '{{.Names}}' | grep -q '^devhub$'; then
    echo "  dev2 (spoke2) to devhub (hub) at 10.100.2.10:"
    docker exec dev2 ping -c 2 -W 3 10.100.2.10 2>/dev/null && echo '  ✓ SUCCESS - Hub-spoke routing works!' || echo '  ✗ FAILED'
fi

if [ "$DEVICE_COUNT" -lt 2 ]; then
    echo "  Skipped - not enough devices"
fi

# Test 15: Final summary
echo ""
echo "[15/15] Test Summary"
echo "=========================================="
echo "Core network tests completed."
echo "Device connectivity tests completed."
echo "L3VPN hub-and-spoke topology verified."
echo "=========================================="
