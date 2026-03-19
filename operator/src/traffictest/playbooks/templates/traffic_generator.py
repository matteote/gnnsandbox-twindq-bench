#!/usr/bin/env python3
"""
Traffic Pattern Generator for TrafficTest resource.
Generates various traffic patterns using iperf3 as the underlying tool.
"""

import asyncio
import json
import logging
import math
import random
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import signal
import sys

logger = logging.getLogger(__name__)

class TrafficGenerator:
    """Generates traffic patterns between devices using iperf3"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.source_device = config['source_device']
        self.source_ip = config['source_ip']
        self.destination_device = config['destination_device']
        self.destination_ip = config['destination_ip']
        self.protocol = config['protocol']
        self.port = config.get('port', 5201)
        self.duration = config['duration']
        self.bandwidth = config.get('bandwidth', '10Mbps')
        self.pattern_type = config.get('pattern_type', 'constant')
        self.pattern_config = config.get('pattern_config', {})
        self.concurrent_users = config.get('concurrent_users', 1)
        self.session_duration = config.get('session_duration')
        self.think_time = config.get('think_time', 0)
        
        self.start_time = None
        self.end_time = None
        self.running = False
        self.processes = []
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()
        sys.exit(0)
    
    def _parse_bandwidth(self, bandwidth_str: str) -> int:
        """Parse bandwidth string to bits per second"""
        if not bandwidth_str:
            return 0
            
        bandwidth_str = bandwidth_str.strip()
        if bandwidth_str.endswith('Kbps'):
            return int(bandwidth_str[:-4]) * 1000
        elif bandwidth_str.endswith('Mbps'):
            return int(bandwidth_str[:-4]) * 1000000
        elif bandwidth_str.endswith('Gbps'):
            return int(bandwidth_str[:-4]) * 1000000000
        else:
            # Assume bps if no unit
            return int(bandwidth_str)
    
    def _format_bandwidth(self, bps: int) -> str:
        """Format bits per second to iperf3 format"""
        if bps >= 1000000000:
            return f"{bps // 1000000000}G"
        elif bps >= 1000000:
            return f"{bps // 1000000}M"
        elif bps >= 1000:
            return f"{bps // 1000}K"
        else:
            return str(bps)
    
    def _calculate_pattern_bandwidth(self, elapsed_time: float) -> int:
        """Calculate current bandwidth based on pattern type and elapsed time"""
        if self.pattern_type == 'constant':
            return self._parse_bandwidth(self.bandwidth)
        
        elif self.pattern_type == 'periodic':
            wave_type = self.pattern_config.get('wave_type', 'sine')
            period = self.pattern_config.get('period', 3600)  # 1 hour default
            base_rate = self._parse_bandwidth(self.pattern_config.get('base_rate', '10Mbps'))
            amplitude = self._parse_bandwidth(self.pattern_config.get('amplitude', '5Mbps'))
            
            # Calculate phase (0 to 2π)
            phase = (elapsed_time % period) / period * 2 * math.pi
            
            if wave_type == 'sine':
                multiplier = math.sin(phase)
            elif wave_type == 'square':
                multiplier = 1 if math.sin(phase) >= 0 else -1
            elif wave_type == 'sawtooth':
                multiplier = (phase / math.pi) - 1  # -1 to 1
            else:
                multiplier = 0
            
            return max(0, int(base_rate + amplitude * multiplier))
        
        elif self.pattern_type == 'burst':
            burst_duration = self.pattern_config.get('burst_duration', 60)
            burst_interval = self.pattern_config.get('burst_interval', 300)
            burst_rate = self._parse_bandwidth(self.pattern_config.get('burst_rate', '100Mbps'))
            idle_rate = self._parse_bandwidth(self.pattern_config.get('idle_rate', '1Mbps'))
            
            # Determine if we're in a burst period
            cycle_time = elapsed_time % burst_interval
            if cycle_time < burst_duration:
                return burst_rate
            else:
                return idle_rate
        
        elif self.pattern_type == 'poisson':
            # For Poisson, we use the base bandwidth but vary connection timing
            return self._parse_bandwidth(self.bandwidth)
        
        else:
            logger.warning(f"Unknown pattern type: {self.pattern_type}, using constant")
            return self._parse_bandwidth(self.bandwidth)
    
    async def _run_iperf3_client(self, bandwidth_bps: int, duration: int, parallel_streams: int = 1, client_id: int = 0) -> Optional[Dict]:
        """Run iperf3 client with specified parameters"""
        bandwidth_str = self._format_bandwidth(bandwidth_bps)
        
        # Build iperf3 command
        cmd = [
            'iperf3',
            '-c', self.destination_ip,  # Connect to destination device
            '-p', str(self.port),
            '-t', str(duration),
            '-b', bandwidth_str,
            '-J',  # JSON output
            '--logfile', f'/tmp/iperf3_client_{client_id}.log'
        ]
        
        # Add parallel streams if more than 1
        if parallel_streams > 1:
            cmd.extend(['-P', str(parallel_streams)])
        
        if self.protocol == 'UDP':
            cmd.append('-u')
        
        logger.info(f"Running iperf3 client {client_id} with {parallel_streams} parallel streams: {' '.join(cmd)}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            self.processes.append(process)
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                try:
                    result = json.loads(stdout.decode())
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse iperf3 JSON output: {e}")
                    logger.error(f"Raw output: {stdout.decode()}")
                    return None
            else:
                logger.error(f"iperf3 client {client_id} failed with return code {process.returncode}")
                logger.error(f"stderr: {stderr.decode()}")
                return None
                
        except Exception as e:
            logger.error(f"Error running iperf3 client {client_id}: {e}")
            return None
    
    async def _run_constant_pattern(self):
        """Run constant bandwidth traffic pattern"""
        bandwidth_bps = self._parse_bandwidth(self.bandwidth)
        connection_duration = self.session_duration or self.duration
        
        # Use parallel streams for concurrent users instead of multiple processes
        result = await self._run_iperf3_client(
            bandwidth_bps, 
            connection_duration, 
            parallel_streams=self.concurrent_users,
            client_id=0
        )
        return [result] if result else []
    
    async def _run_dynamic_pattern(self):
        """Run dynamic patterns (periodic, burst) with bandwidth changes"""
        results = []
        segment_duration = 10  # Update bandwidth every 10 seconds
        total_segments = self.duration // segment_duration
        
        for segment in range(total_segments):
            elapsed_time = segment * segment_duration
            current_bandwidth = self._calculate_pattern_bandwidth(elapsed_time)
            
            logger.info(f"Segment {segment + 1}/{total_segments}: "
                       f"bandwidth={self._format_bandwidth(current_bandwidth)}bps")
            
            # Use parallel streams for concurrent users
            result = await self._run_iperf3_client(
                current_bandwidth, 
                segment_duration, 
                parallel_streams=self.concurrent_users,
                client_id=segment
            )
            if result:
                results.append(result)
        
        # Handle remaining time
        remaining_time = self.duration % segment_duration
        if remaining_time > 0:
            elapsed_time = total_segments * segment_duration
            current_bandwidth = self._calculate_pattern_bandwidth(elapsed_time)
            
            result = await self._run_iperf3_client(
                current_bandwidth, 
                remaining_time, 
                parallel_streams=self.concurrent_users,
                client_id=total_segments
            )
            if result:
                results.append(result)
        
        return results
    
    async def _run_poisson_pattern(self):
        """Run Poisson arrival pattern for realistic user simulation"""
        arrival_rate = self.pattern_config.get('arrival_rate', 1.0)  # users per second
        session_duration = self.session_duration or 60  # default 1 minute sessions
        bandwidth_bps = self._parse_bandwidth(self.bandwidth)
        
        results = []
        active_connections = []
        next_arrival_time = 0
        
        start_time = time.time()
        
        while time.time() - start_time < self.duration:
            current_time = time.time() - start_time
            
            # Check if it's time for a new arrival
            if current_time >= next_arrival_time and len(active_connections) < self.concurrent_users:
                # Schedule new connection
                connection_task = asyncio.create_task(
                    self._run_iperf3_client(
                        bandwidth_bps // max(1, len(active_connections) + 1),
                        session_duration,
                        client_id=len(active_connections)
                    )
                )
                active_connections.append(connection_task)
                
                # Calculate next arrival time using exponential distribution
                inter_arrival_time = random.expovariate(arrival_rate)
                next_arrival_time = current_time + inter_arrival_time
                
                logger.info(f"Started connection {len(active_connections)}, "
                           f"next arrival in {inter_arrival_time:.2f}s")
            
            # Check for completed connections
            completed = []
            for i, task in enumerate(active_connections):
                if task.done():
                    try:
                        result = await task
                        if result:
                            results.append(result)
                    except Exception as e:
                        logger.error(f"Connection {i} failed: {e}")
                    completed.append(task)
            
            # Remove completed connections
            for task in completed:
                active_connections.remove(task)
            
            await asyncio.sleep(0.1)  # Small sleep to prevent busy waiting
        
        # Wait for remaining connections to complete
        if active_connections:
            logger.info(f"Waiting for {len(active_connections)} remaining connections...")
            remaining_results = await asyncio.gather(*active_connections, return_exceptions=True)
            results.extend([r for r in remaining_results if r and not isinstance(r, Exception)])
        
        return results
    
    async def start(self) -> List[Dict]:
        """Start traffic generation based on pattern type"""
        if self.running:
            logger.warning("Traffic generator is already running")
            return []
        
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        
        logger.info(f"Starting traffic test: {self.source_device} -> {self.destination_device}")
        logger.info(f"Pattern: {self.pattern_type}, Duration: {self.duration}s, "
                   f"Concurrent users: {self.concurrent_users}")
        
        try:
            if self.pattern_type == 'constant':
                results = await self._run_constant_pattern()
            elif self.pattern_type in ['periodic', 'burst']:
                results = await self._run_dynamic_pattern()
            elif self.pattern_type == 'poisson':
                results = await self._run_poisson_pattern()
            else:
                logger.error(f"Unsupported pattern type: {self.pattern_type}")
                results = []
            
            self.end_time = datetime.now(timezone.utc)
            logger.info(f"Traffic test completed. Generated {len(results)} result sets.")
            
            return results
            
        except Exception as e:
            logger.error(f"Traffic generation failed: {e}")
            self.end_time = datetime.now(timezone.utc)
            return []
        finally:
            self.running = False
    
    def stop(self):
        """Stop all running traffic generation processes"""
        logger.info("Stopping traffic generation...")
        self.running = False
        
        for process in self.processes:
            try:
                if process.returncode is None:  # Process is still running
                    process.terminate()
            except Exception as e:
                logger.error(f"Error terminating process: {e}")
        
        self.processes.clear()


async def main():
    """Main entry point for standalone execution"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Traffic Pattern Generator')
    parser.add_argument('--config', required=True, help='JSON configuration file')
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
        
        generator = TrafficGenerator(config)
        results = await generator.start()
        
        # Output results as JSON
        output = {
            'start_time': generator.start_time.isoformat() if generator.start_time else None,
            'end_time': generator.end_time.isoformat() if generator.end_time else None,
            'results': results
        }
        
        print(json.dumps(output, indent=2))
        
    except Exception as e:
        logger.error(f"Traffic generation failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
