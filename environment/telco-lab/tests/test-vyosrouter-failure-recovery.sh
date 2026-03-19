#!/bin/bash
# =============================================================================
# VyOSRouter Failure Recovery Test Script
# Tests the transient error handling & idempotent retry logic in lifecycle.py
#
# Prerequisites:
#   - kubectl configured for the automation cluster
#   - gcloud configured with access to the network VM
#   - jq installed (for JSON parsing)
#
# Usage:
#   chmod +x test-vyosrouter-failure-recovery.sh
#   ./test-vyosrouter-failure-recovery.sh [ZONE] [NETWORK_VM_NAME]
#
# Examples:
#   ./test-vyosrouter-failure-recovery.sh us-central1-a networkvm
# =============================================================================

set -euo pipefail

ZONE="${1:-us-central1-a}"
NETWORK_VM="${2:-networkvm}"
NAMESPACE="automation"
TEST_ROUTER="test-recovery-router"
TEST_NETWORK="test-recovery-net"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PASS=0
FAIL=0

# --- Colours ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; ((PASS++)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; ((FAIL++)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
sep()  { echo -e "${BLUE}$(printf '=%.0s' {1..70})${NC}"; }

# --- Helpers ---
get_router_phase() {
    kubectl get vyosrouter "$TEST_ROUTER" -n "$NAMESPACE" \
        -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown"
}

get_router_message() {
    kubectl get vyosrouter "$TEST_ROUTER" -n "$NAMESPACE" \
        -o jsonpath='{.status.message}' 2>/dev/null || echo ""
}

wait_for_phase() {
    local target_phase="$1"
    local timeout="${2:-180}"
    local elapsed=0
    local interval=5
    log "Waiting for phase=$target_phase (timeout=${timeout}s)..."
    while [[ $elapsed -lt $timeout ]]; do
        local phase
        phase=$(get_router_phase)
        if [[ "$phase" == "$target_phase" ]]; then
            log "Reached phase: $phase"
            return 0
        fi
        echo -n "  Phase=$phase ... "
        sleep $interval
        ((elapsed+=interval))
        echo "(${elapsed}s elapsed)"
    done
    warn "Timed out waiting for phase=$target_phase (current: $(get_router_phase))"
    return 1
}

wait_for_any_phase() {
    local timeout="${1:-180}"
    shift
    local target_phases=("$@")
    local elapsed=0
    local interval=5
    log "Waiting for any phase in [${target_phases[*]}] (timeout=${timeout}s)..."
    while [[ $elapsed -lt $timeout ]]; do
        local phase
        phase=$(get_router_phase)
        for p in "${target_phases[@]}"; do
            if [[ "$phase" == "$p" ]]; then
                log "Reached phase: $phase"
                echo "$phase"
                return 0
            fi
        done
        echo -n "  Phase=$phase ... "
        sleep $interval
        ((elapsed+=interval))
        echo "(${elapsed}s elapsed)"
    done
    warn "Timed out (current: $(get_router_phase))"
    echo "Timeout"
    return 1
}

ssh_vm() {
    gcloud compute ssh "$NETWORK_VM" --zone="$ZONE" --command="$1" --quiet 2>/dev/null
}

cleanup() {
    log "Cleaning up test resources..."
    kubectl delete -f "$SCRIPT_DIR/test-vyosrouter-failure-recovery.yaml" \
        --ignore-not-found --wait=false 2>/dev/null || true
    # Also clean up container if it exists on the VM
    ssh_vm "sudo docker rm -f $TEST_ROUTER 2>/dev/null || true" || true
    log "Cleanup done."
}

# =============================================================================
# SCENARIO 1: Dependency Wait (Pending Retry)
# Tests: LinuxNetwork not ready when router is applied → TemporaryError → retry
# Expected: Router stays Pending while network is not ready, then moves to Running
# =============================================================================
test_dependency_wait() {
    sep
    log "SCENARIO 1: Dependency Wait (Pending → retry → Running)"
    log "Apply router FIRST, then delay network creation by 30 seconds"

    # Apply only the router (not the network)
    kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: VyOSRouter
metadata:
  name: $TEST_ROUTER
  namespace: $NAMESPACE
  labels:
    test: failure-recovery
    scenario: dependency-wait
spec:
  hostname: recovery-router
  router_id: 10.99.1.100
  image: vyos:1.5
  interfaces:
    - name: eth0
      address: 10.99.1.100/24
      linux_network: $TEST_NETWORK
      description: "Test recovery interface"
      enabled: true
  protocols: {}
  services: {}
EOF

    sleep 5

    local phase
    phase=$(get_router_phase)
    local message
    message=$(get_router_message)

    if [[ "$phase" == "Pending" ]]; then
        pass "Router correctly entered Pending state while network doesn't exist"
        log "  Message: $message"
    else
        fail "Expected Pending, got $phase"
    fi

    # Check that message mentions LinuxNetwork
    if echo "$message" | grep -qi "linuxnetwork\|waiting\|ready"; then
        pass "Status message correctly describes the dependency wait"
    else
        fail "Status message doesn't mention network dependency: '$message'"
    fi

    # Now create the network
    log "Creating the LinuxNetwork (30s after router)..."
    kubectl apply -f - <<EOF
apiVersion: google.dev/v1
kind: LinuxNetwork
metadata:
  name: $TEST_NETWORK
  namespace: $NAMESPACE
  labels:
    test: failure-recovery
spec:
  name: test-recovery
  network_type: custom
  subnet: 10.99.1.0/24
  gateway: 10.99.1.1
  bandwidth: 1gbit
  description: "Network for failure recovery testing"
EOF

    log "Waiting for network to become Ready..."
    if kubectl wait --for=jsonpath='{.status.phase}'=Ready \
        linuxnetwork/"$TEST_NETWORK" -n "$NAMESPACE" --timeout=120s 2>/dev/null; then
        pass "LinuxNetwork became Ready"
    else
        warn "LinuxNetwork not Ready yet, continuing..."
    fi

    log "Waiting for router to reach Running (kopf will retry when network is ready)..."
    if wait_for_phase "Running" 300; then
        pass "SCENARIO 1 PASSED: Router recovered from Pending to Running after network became ready"
    else
        fail "SCENARIO 1 FAILED: Router did not reach Running state"
        log "Final phase: $(get_router_phase)"
        log "Final message: $(get_router_message)"
    fi
}

# =============================================================================
# SCENARIO 2: Container Already Exists (Idempotent Retry)
# Tests: Container manually pre-created on VM → Ansible create → should succeed
# Expected: Playbook detects existing container, skips creation, proceeds to configure
# =============================================================================
test_container_already_exists() {
    sep
    log "SCENARIO 2: Container Already Exists (idempotent creation)"
    log "Pre-creating container on VM before operator runs..."

    # Pre-create the container on the network VM to simulate a partial previous run
    if ssh_vm "sudo docker inspect $TEST_ROUTER >/dev/null 2>&1"; then
        log "Container $TEST_ROUTER already exists on VM from previous test"
    else
        log "Manually creating container $TEST_ROUTER on $NETWORK_VM..."
        ssh_vm "sudo docker run -d \
            --name $TEST_ROUTER \
            --hostname recovery-router \
            --privileged \
            --network none \
            --restart unless-stopped \
            --cap-add NET_ADMIN \
            -v /lib/modules:/lib/modules:ro \
            vyos:1.5 /sbin/init" || warn "Could not pre-create container (might not have image)"
    fi

    # Delete existing K8s resource and re-create to trigger create handler
    log "Deleting existing K8s resource to re-trigger creation..."
    kubectl delete vyosrouter "$TEST_ROUTER" -n "$NAMESPACE" --ignore-not-found --wait=true 2>/dev/null || true
    sleep 5

    log "Re-applying router resource..."
    kubectl apply -f "$SCRIPT_DIR/test-vyosrouter-failure-recovery.yaml"

    log "Watching for operator to detect and re-use existing container..."
    if wait_for_phase "Running" 300; then
        pass "SCENARIO 2 PASSED: Operator handled pre-existing container idempotently"
    else
        local final_phase
        final_phase=$(get_router_phase)
        # If it hit Pending (due to network) then recovered, that's also ok
        if [[ "$final_phase" == "Failed" ]]; then
            # Check if it's failing because of "already exists" - which should now be treated as transient
            local msg
            msg=$(get_router_message)
            if echo "$msg" | grep -qi "already\|exists\|retry"; then
                fail "SCENARIO 2 FAILED: 'already exists' error not being treated as transient"
                log "  Message: $msg"
            else
                fail "SCENARIO 2 FAILED: Router failed for another reason"
                log "  Phase: $final_phase, Message: $msg"
            fi
        else
            fail "SCENARIO 2 FAILED: Router did not reach Running (phase=$final_phase)"
        fi
    fi
}

# =============================================================================
# SCENARIO 3: Runtime State Change (Running → Failed → Running)
# Tests: monitor_vyosrouter detects container going down and recovering
# Expected: Status changes to Failed within 60s of container stop, then back to Running
# =============================================================================
test_runtime_state_change() {
    sep
    log "SCENARIO 3: Runtime State Change (Running → Failed → Running)"

    # Ensure router is running first
    if [[ "$(get_router_phase)" != "Running" ]]; then
        log "Router not Running, waiting..."
        wait_for_phase "Running" 300 || { fail "Router never reached Running state"; return; }
    fi
    pass "Router is Running - ready to test state change detection"

    # Stop the container on the VM
    log "Stopping VyOS container on $NETWORK_VM..."
    if ssh_vm "sudo docker stop $TEST_ROUTER"; then
        log "Container stopped. Waiting up to 75s for monitor to detect failure (60s interval + margin)..."
    else
        fail "Could not stop container on VM"
        return
    fi

    # Wait for Failed (monitor runs every 60s)
    if wait_for_phase "Failed" 90; then
        pass "Monitor detected container failure within 60s window"
    else
        fail "Monitor did not detect container failure within 90s"
        log "Current phase: $(get_router_phase)"
    fi

    # Now restart the container
    log "Restarting VyOS container on $NETWORK_VM..."
    if ssh_vm "sudo docker start $TEST_ROUTER"; then
        log "Container restarted. Waiting up to 75s for monitor to detect recovery..."
    else
        fail "Could not restart container on VM"
        return
    fi

    if wait_for_phase "Running" 90; then
        pass "SCENARIO 3 PASSED: Monitor detected recovery from Failed to Running within 60s window"
    else
        fail "SCENARIO 3 FAILED: Monitor did not detect recovery within 90s"
        log "Final phase: $(get_router_phase)"
    fi
}

# =============================================================================
# SCENARIO 4: Verify No Spurious Failed on Normal Creation
# Tests: Clean creation should never touch Failed state
# Expected: Pending (briefly) → Creating → Configuring → Running, no Failed
# =============================================================================
test_clean_creation_no_failed() {
    sep
    log "SCENARIO 4: Clean Creation - verifying no spurious Failed state"

    # Clean up then re-apply from scratch
    log "Cleaning up existing resources..."
    kubectl delete vyosrouter "$TEST_ROUTER" -n "$NAMESPACE" --ignore-not-found --wait=true 2>/dev/null || true
    kubectl delete linuxnetwork "$TEST_NETWORK" -n "$NAMESPACE" --ignore-not-found --wait=true 2>/dev/null || true
    ssh_vm "sudo docker rm -f $TEST_ROUTER 2>/dev/null || true" || true
    sleep 10

    log "Applying both network and router simultaneously..."
    kubectl apply -f "$SCRIPT_DIR/test-vyosrouter-failure-recovery.yaml"

    # Poll status every 3 seconds, recording every state seen
    local states_seen=()
    local elapsed=0
    local timeout=300

    log "Monitoring state transitions (${timeout}s timeout)..."
    while [[ $elapsed -lt $timeout ]]; do
        local phase
        phase=$(get_router_phase)
        
        # Track unique states
        if [[ ${#states_seen[@]} -eq 0 ]] || [[ "${states_seen[-1]}" != "$phase" ]]; then
            states_seen+=("$phase")
            log "  → $phase (${elapsed}s)"
        fi

        if [[ "$phase" == "Running" ]]; then
            break
        fi
        if [[ "$phase" == "Failed" ]]; then
            fail "SCENARIO 4 FAILED: Entered Failed state during clean creation"
            log "  State progression: ${states_seen[*]}"
            log "  Message: $(get_router_message)"
            return
        fi
        sleep 3
        ((elapsed+=3))
    done

    local final_phase
    final_phase=$(get_router_phase)
    log "State progression observed: ${states_seen[*]}"

    if [[ "$final_phase" == "Running" ]]; then
        # Verify Failed was never in the sequence
        local hit_failed=false
        for s in "${states_seen[@]}"; do
            [[ "$s" == "Failed" ]] && hit_failed=true
        done

        if $hit_failed; then
            fail "SCENARIO 4 FAILED: Passed through Failed state before reaching Running"
        else
            pass "SCENARIO 4 PASSED: Clean path to Running with no Failed state (${states_seen[*]})"
        fi
    else
        fail "SCENARIO 4 FAILED: Never reached Running (final=$final_phase)"
    fi
}

# =============================================================================
# MAIN
# =============================================================================
main() {
    sep
    log "VyOSRouter Failure Recovery Test Suite"
    log "Zone: $ZONE | VM: $NETWORK_VM | Namespace: $NAMESPACE"
    sep

    # Trap cleanup on exit
    trap cleanup EXIT

    # Run scenarios
    test_dependency_wait
    test_container_already_exists
    test_runtime_state_change
    test_clean_creation_no_failed

    # Summary
    sep
    log "Test Results:"
    echo -e "  ${GREEN}PASSED: $PASS${NC}"
    echo -e "  ${RED}FAILED: $FAIL${NC}"
    sep

    if [[ $FAIL -gt 0 ]]; then
        echo -e "${RED}Some tests failed. Check operator logs:${NC}"
        echo "  kubectl logs -n $NAMESPACE deployment/operator --tail=200 | grep -E '(ERROR|Failed|$TEST_ROUTER)'"
        exit 1
    else
        echo -e "${GREEN}All tests passed!${NC}"
        exit 0
    fi
}

main "$@"
