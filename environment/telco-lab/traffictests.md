# TrafficTest Resource

The TrafficTest resource enables realistic traffic simulation between Customer Premise Equipment (CPE) containers and network devices for network performance analysis and monitoring.

## Overview

The TrafficTest resource generates configurable traffic patterns that simulate real-world network usage scenarios. It supports multiple traffic patterns, concurrent user simulation, and comprehensive monitoring with results collection for network analysis.

## Features

- **Multiple Traffic Patterns**: Constant, periodic (sine/square/sawtooth), burst, and Poisson arrival patterns
- **Concurrent User Simulation**: Simulate multiple users with configurable session durations and think times
- **Real-time Monitoring**: Background monitoring with automatic status updates and test completion handling
- **iperf3 Integration**: Uses industry-standard iperf3 for traffic generation and measurement
- **Container-based Testing**: Works with Docker containers representing network devices
- **Flexible Configuration**: JSON-based configuration with comprehensive pattern options
- **Results Collection**: Structured JSON output with detailed metrics and timing information

## Architecture

```
TrafficTest CR → Operator → Ansible → Container Devices
                    ↓
              Background Monitoring → Results JSON
                    ↓
              Status Updates & Completion
```

The system uses:
- **Kubernetes Operator**: Manages TrafficTest custom resources
- **Ansible Playbooks**: Deploy and manage traffic tests in containers
- **Python Traffic Generator**: Sophisticated pattern generation using iperf3
- **Background Monitoring**: Results monitoring script for status tracking

## Traffic Patterns

### 1. Constant Pattern
Maintains steady bandwidth throughout the test duration.

```yaml
spec:
  pattern_type: constant
  bandwidth: 50Mbps
  concurrent_users: 10
```

### 2. Periodic Pattern
Varies bandwidth over time using mathematical functions.

```yaml
spec:
  pattern_type: periodic
  pattern_config:
    wave_type: sine  # sine, square, or sawtooth
    period: 3600     # seconds (1 hour cycle)
    base_rate: 20Mbps
    amplitude: 30Mbps  # varies between base_rate ± amplitude
```

### 3. Burst Pattern
Alternates between high-traffic bursts and low-traffic idle periods.

```yaml
spec:
  pattern_type: burst
  pattern_config:
    burst_duration: 180    # 3 minutes of high traffic
    burst_interval: 600    # every 10 minutes
    burst_rate: 100Mbps    # traffic during bursts
    idle_rate: 5Mbps       # traffic during idle periods
```

### 4. Poisson Pattern
Simulates realistic user arrivals using Poisson distribution.

```yaml
spec:
  pattern_type: poisson
  pattern_config:
    arrival_rate: 2.0  # users per second
  concurrent_users: 100  # maximum concurrent
  session_duration: 300  # average session length
```

## Configuration Reference

### Required Fields

- `source_device`: Name of the source container/device
- `destination_device`: Name of the destination container/device  
- `protocol`: TCP or UDP

### Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `port` | 5201 | Port number for traffic test |
| `duration` | 60 | Test duration in seconds |
| `bandwidth` | 10Mbps | Bandwidth (for constant/base rate) |
| `pattern_type` | constant | Traffic pattern type |
| `concurrent_users` | 1 | Number of concurrent connections |
| `session_duration` | - | Average session duration (seconds) |
| `think_time` | 0 | Pause between requests (seconds) |
| `metrics_enabled` | true | Enable metrics collection |
| `metrics_interval` | 5 | Metrics sampling interval (seconds) |

## Usage Examples

### Basic Constant Traffic Test

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: basic-test
spec:
  source_device: customer-a-device-1
  destination_device: customer-b-device-1
  protocol: TCP
  duration: 300
  bandwidth: 50Mbps
  concurrent_users: 10
```

### Daily Usage Pattern

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: daily-pattern
spec:
  source_device: customer-a-device-1
  destination_device: customer-b-device-1
  protocol: TCP
  duration: 3600
  pattern_type: periodic
  pattern_config:
    wave_type: sine
    period: 3600
    base_rate: 20Mbps
    amplitude: 30Mbps
  concurrent_users: 25
  session_duration: 120
  think_time: 5
```

### Business Hours Simulation

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: business-hours
spec:
  source_device: edge-router-1
  destination_device: core-router-1
  protocol: TCP
  duration: 1800
  pattern_type: burst
  pattern_config:
    burst_duration: 180
    burst_interval: 600
    burst_rate: 100Mbps
    idle_rate: 5Mbps
  concurrent_users: 50
```

## Status Monitoring

The TrafficTest resource provides real-time status updates:

```bash
kubectl get traffictests
```

Output:
```
NAME           PHASE     SOURCE                DESTINATION           PATTERN    DURATION
basic-test     Running   customer-a-device-1   customer-b-device-1   constant   300
daily-pattern  Pending   customer-a-device-1   customer-b-device-1   periodic   3600
```

Detailed status:
```bash
kubectl describe traffictest basic-test
```

## Monitoring and Results

### Status Monitoring

The TrafficTest resource provides real-time status updates through Kubernetes status fields:

```bash
kubectl get traffictests
```

### Results Collection

The system uses a background monitoring script that:

1. **Process Monitoring**: Continuously watches traffic generator processes in containers
2. **Results Aggregation**: Collects exit codes, output logs, and performance metrics
3. **State Management**: Creates structured JSON results for operator consumption
4. **Automatic Cleanup**: Handles process termination and resource cleanup

### Results Structure

Traffic test results are stored in JSON format:

```json
{
  "returncode": 0,
  "stdout": "iperf3 output...",
  "stderr": "",
  "completed": true,
  "timestamp": "2025-10-30T10:00:00Z"
}
```

### Log Files and Locations

- **Operator logs**: Kubernetes operator pod logs
- **Traffic generator logs**: `/tmp/traffic_test_<name>/traffic_test.log`
- **Results file**: `/tmp/traffic_test_<name>/results.json`
- **iperf3 logs**: `/tmp/iperf3_*.log` in device containers

## Traffic Patterns Implementation

### Background Monitoring Architecture

The system uses a sophisticated monitoring approach:

1. **Results Monitoring Script**: Bash script that watches container processes
2. **Async Operations**: Non-blocking monitoring while operator handles other tasks  
3. **Container Communication**: Bridges containerized tests with Kubernetes operator
4. **Fault Tolerance**: Graceful handling of container failures and cleanup

### Traffic Generator Features

The Python traffic generator (`traffic_generator.py`) provides:

- **Mathematical Pattern Generation**: Sine, square, sawtooth wave patterns
- **Poisson Process Simulation**: Realistic user arrival patterns using exponential distribution
- **Bandwidth Management**: Automatic unit conversion (Kbps/Mbps/Gbps)
- **Concurrent Connection Handling**: Multiple simultaneous iperf3 processes
- **Signal Handling**: Graceful shutdown on SIGTERM/SIGINT
- **Asynchronous Execution**: Non-blocking I/O with asyncio

## Troubleshooting

### Common Issues

1. **Device Not Ready**: Ensure source and destination devices/containers are running and accessible
2. **Container Access**: Verify device containers have iperf3 installed and accessible
3. **Port Conflicts**: Use different ports for concurrent tests on same devices
4. **Process Cleanup**: Check for orphaned iperf3 processes in containers
5. **Network Connectivity**: Verify containers can reach each other on specified ports

### Debug Commands

```bash
# Check device/container status
docker ps | grep -E "(customer|device)"

# Check TrafficTest status
kubectl get traffictests
kubectl describe traffictest <test-name>

# Check operator logs
kubectl logs -l app=operator -f | grep -i traffic

# Check iperf3 processes in containers
docker exec <device-name> ps aux | grep iperf3

# Check container connectivity
docker exec <source-device> ping <destination-device>

# View traffic test logs
cat /tmp/traffic_test_<name>/traffic_test.log

# View results file
cat /tmp/traffic_test_<name>/results.json
```

### Performance Considerations

#### Resource Usage
- Each concurrent user creates a separate iperf3 process
- Memory usage scales with concurrent users and test duration  
- CPU usage depends on bandwidth and packet processing
- Container resources should accommodate multiple iperf3 processes

#### Scaling Guidelines
- **Small tests**: 1-10 concurrent users, < 100Mbps
- **Medium tests**: 10-50 concurrent users, 100Mbps-1Gbps  
- **Large tests**: 50+ concurrent users, > 1Gbps

#### Network Considerations
- Tests generate real network traffic between containers
- Consider container network capacity when running multiple tests
- Monitor for network congestion and adjust test parameters
- Use appropriate Docker network configurations for testing

## Implementation Details

### Traffic Generation Process

1. **Operator receives TrafficTest CR**: Validates source/destination devices
2. **Ansible playbook execution**: Deploys traffic generator scripts to containers
3. **iperf3 server startup**: Destination container starts iperf3 server
4. **Traffic generator execution**: Source container runs Python traffic generator
5. **Background monitoring**: Results monitoring script tracks progress
6. **Results collection**: Structured JSON output for operator status updates

### File Structure

```
operator/src/traffictest/
├── lifecycle.py              # Kubernetes operator handlers
├── lifecycle_tasks.py        # Async task implementations  
├── playbooks/
│   ├── traffic.yaml          # Ansible playbook for traffic tests
│   └── templates/
│       ├── traffic_generator.py    # Main traffic generator script
│       └── metrics_collector.py   # Optional metrics collection
└── README.md                 # This documentation
```

### Container Requirements

For traffic testing to work, containers must have:
- **iperf3 installed**: For traffic generation and measurement
- **Python 3**: For running traffic generator scripts
- **Network connectivity**: Between source and destination containers
- **Sufficient resources**: CPU and memory for concurrent connections

## Integration Examples

### With CustomerPremiseEquipment

```yaml
# First create CPE devices
apiVersion: google.dev/v1
kind: CustomerPremiseEquipment
metadata:
  name: customer-a-device-1
spec:
  network_name: customer-access-net
  ip_address: "192.168.1.100"
  image: alpine-networking:latest

---
# Then test traffic between them
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: cpe-performance-test
spec:
  source_device: customer-a-device-1
  destination_device: customer-b-device-1
  protocol: TCP
  duration: 600
  pattern_type: periodic
  pattern_config:
    wave_type: sine
    period: 300
    base_rate: 10Mbps
    amplitude: 40Mbps
```

### With VyOS Routers

```yaml
apiVersion: google.dev/v1
kind: TrafficTest
metadata:
  name: router-capacity-test
spec:
  source_device: edge-router-1
  destination_device: core-router-1 
  protocol: UDP
  duration: 1800
  pattern_type: burst
  pattern_config:
    burst_duration: 120
    burst_interval: 300
    burst_rate: 1Gbps
    idle_rate: 100Mbps
  concurrent_users: 20
```

## Best Practices

1. **Test Naming**: Use descriptive names that indicate test purpose and devices
2. **Duration Planning**: Balance test duration with resource usage and objectives
3. **Pattern Selection**: Choose patterns that match real-world scenarios being tested
4. **Resource Monitoring**: Monitor container and network resources during tests
5. **Port Management**: Use unique ports for concurrent tests on same device pairs
6. **Container Preparation**: Ensure containers have iperf3 and necessary tools installed
7. **Network Isolation**: Use appropriate Docker networks for realistic testing scenarios
8. **Results Validation**: Check results JSON files for test completion and errors

## Contributing

To extend the TrafficTest resource:

1. **New Traffic Patterns**: Add pattern logic to `traffic_generator.py`
2. **Enhanced Monitoring**: Extend background monitoring in `traffic.yaml`
3. **Container Support**: Add support for new container types and tools
4. **Validation Logic**: Enhance device readiness checks in operator code
5. **Documentation**: Update this README and add inline code documentation

## See Also

- [CustomerPremiseEquipment Documentation](../cpe/README.md)
- [VyOSRouter Documentation](../vyosrouter/README.md) 
- [Operator Development Guide](../../README.md)
- [Telco Lab Examples](../../../telco-lab/README.md)
