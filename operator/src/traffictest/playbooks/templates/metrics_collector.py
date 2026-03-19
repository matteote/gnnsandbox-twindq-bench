#!/usr/bin/env python3
"""
Metrics Collector for TrafficTest resource.
Collects metrics from iperf3 results.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import sys

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Collects and exports traffic metrics"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.test_name = config.get('test_name', 'unknown')
        self.source_cpe = config['source_cpe']
        self.destination_cpe = config['destination_cpe']
        self.protocol = config['protocol']
        self.pattern_type = config.get('pattern_type', 'constant')
        
    def _parse_iperf3_result(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse iperf3 JSON result and extract metrics"""
        try:
            if 'end' not in result:
                logger.warning("No 'end' section in iperf3 result")
                return None
            
            end_data = result['end']
            
            # Extract summary metrics
            sum_sent = end_data.get('sum_sent', {})
            sum_received = end_data.get('sum_received', {})
            
            metrics = {
                'timestamp': datetime.now(timezone.utc),
                'test_duration': end_data.get('seconds', 0),
            }
            
            # Throughput metrics
            if 'bits_per_second' in sum_sent:
                metrics['throughput_sent_bps'] = sum_sent['bits_per_second']
            if 'bits_per_second' in sum_received:
                metrics['throughput_received_bps'] = sum_received['bits_per_second']
            
            # Packet metrics
            if 'packets' in sum_sent:
                metrics['packets_sent'] = sum_sent['packets']
            if 'packets' in sum_received:
                metrics['packets_received'] = sum_received['packets']
            
            # Loss metrics
            if 'lost_packets' in sum_sent:
                metrics['lost_packets'] = sum_sent['lost_packets']
            if 'lost_percent' in sum_sent:
                metrics['packet_loss_pct'] = sum_sent['lost_percent']
            
            # Retransmission metrics (TCP only)
            if 'retransmits' in sum_sent:
                metrics['retransmissions'] = sum_sent['retransmits']
            
            # Jitter metrics (UDP only)
            if 'jitter_ms' in sum_received:
                metrics['jitter_ms'] = sum_received['jitter_ms']
            
            # Bandwidth metrics
            if 'bytes' in sum_sent and 'seconds' in end_data and end_data['seconds'] > 0:
                metrics['avg_bandwidth_bps'] = (sum_sent['bytes'] * 8) / end_data['seconds']
            
            # CPU utilization
            cpu_utilization_percent = end_data.get('cpu_utilization_percent', {})
            if 'host_total' in cpu_utilization_percent:
                metrics['cpu_utilization_pct'] = cpu_utilization_percent['host_total']
            
            # Connection count (estimate from streams)
            if 'streams' in result:
                metrics['active_connections'] = len(result['streams'])
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error parsing iperf3 result: {e}")
            return None
    
    def write_metrics(self, iperf3_results: List[Dict[str, Any]]) -> bool:
        """Process metrics from iperf3 results"""
        if not iperf3_results:
            logger.warning("No iperf3 results to process")
            return True
        
        count = 0
        for result in iperf3_results:
            metrics = self._parse_iperf3_result(result)
            if metrics:
                count += 1
                logger.info(f"Processed metrics: {metrics}")
        
        return True
    
    def write_realtime_metrics(self, current_metrics: Dict[str, Any]) -> bool:
        """Process real-time metrics during test execution"""
        logger.debug(f"Real-time metrics: {current_metrics}")
        return True
    
    def write_test_status(self, phase: str, message: str, additional_data: Optional[Dict] = None) -> bool:
        """Log test status information"""
        logger.info(f"Test status: {phase} - {message}")
        if additional_data:
            logger.debug(f"Additional data: {additional_data}")
        return True
    
    def close(self):
        """Cleanup resources"""
        pass


class RealtimeMetricsCollector:
    """Collects metrics in real-time during traffic generation"""
    
    def __init__(self, metrics_collector: MetricsCollector, interval: int = 5):
        self.metrics_collector = metrics_collector
        self.interval = interval
        self.running = False
        self.current_metrics = {}
        
    async def start_collection(self, traffic_generator):
        """Start real-time metrics collection"""
        self.running = True
        logger.info(f"Starting real-time metrics collection (interval: {self.interval}s)")
        
        while self.running and traffic_generator.running:
            try:
                # Collect current metrics from traffic generator
                current_time = datetime.now(timezone.utc)
                
                # Basic metrics that are always available
                metrics = {
                    'timestamp': current_time,
                    'active_connections': len(traffic_generator.processes),
                    'test_duration': (current_time - traffic_generator.start_time).total_seconds() if traffic_generator.start_time else 0
                }
                
                self.current_metrics = metrics
                
                # Process metrics
                self.metrics_collector.write_realtime_metrics(metrics)
                
                await asyncio.sleep(self.interval)
                
            except Exception as e:
                logger.error(f"Error in real-time metrics collection: {e}")
                await asyncio.sleep(self.interval)
    
    def stop_collection(self):
        """Stop real-time metrics collection"""
        self.running = False
        logger.info("Stopped real-time metrics collection")
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current metrics for status updates"""
        return self.current_metrics.copy()


async def main():
    """Main entry point for standalone testing"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Metrics Collector')
    parser.add_argument('--config', required=True, help='JSON configuration file')
    parser.add_argument('--results', required=True, help='iperf3 results JSON file')
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
        
        with open(args.results, 'r') as f:
            results = json.load(f)
        
        collector = MetricsCollector(config)
        
        # Write test status
        collector.write_test_status("Running", "Processing iperf3 results")
        
        # Write metrics
        success = collector.write_metrics(results.get('results', []))
        
        if success:
            collector.write_test_status("Completed", "Metrics successfully processed")
            print("Metrics successfully processed")
        else:
            collector.write_test_status("Failed", "Failed to process metrics")
            print("Failed to process metrics")
            sys.exit(1)
        
        collector.close()
        
    except Exception as e:
        logger.error(f"Metrics collection failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
