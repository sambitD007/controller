#!/usr/bin/env python3
"""
Simple Pod Resource Controller

A lightweight Kubernetes controller that watches for Pods stuck in Pending state
due to insufficient resources and recreates them with adjusted resource requests.

Usage:
    python controller.py [--namespace NAMESPACE] [--dry-run]
"""

import argparse
import logging
import sys
import time
import copy
from typing import Optional, Dict, Any

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

from config import (
    OPT_IN_ANNOTATION,
    OPT_IN_VALUE,
    MANAGED_LABEL,
    RESOURCE_SAFETY_MARGIN,
    DEFAULT_CPU_REQUEST,
    DEFAULT_MEMORY_REQUEST,
    DEFAULT_CPU_LIMIT,
    DEFAULT_MEMORY_LIMIT,
    WATCH_TIMEOUT_SECONDS,
    MAX_FIX_ATTEMPTS,
    FIX_ATTEMPT_ANNOTATION,
)
from utils import (
    parse_cpu,
    parse_memory,
    is_resource_insufficient_event,
    calculate_safe_resources,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class PodResourceController:
    """Controller that fixes pods with excessive resource requests."""
    
    def __init__(self, namespace: str = "", dry_run: bool = False):
        """
        Initialize the controller.
        
        Args:
            namespace: Namespace to watch ("" for all namespaces)
            dry_run: If True, don't make any changes
        """
        self.namespace = namespace
        self.dry_run = dry_run
        self.v1 = client.CoreV1Api()
        self._processed_pods: set = set()
    
    def get_cluster_capacity(self) -> tuple[float, int]:
        """
        Get the minimum allocatable resources across all nodes.
        
        Returns:
            Tuple of (cpu_cores, memory_bytes)
        """
        nodes = self.v1.list_node()
        
        if not nodes.items:
            logger.warning("No nodes found in cluster")
            return 1.0, 1024 * 1024 * 1024  # Default: 1 CPU, 1Gi
        
        min_cpu = float('inf')
        min_memory = float('inf')
        
        for node in nodes.items:
            allocatable = node.status.allocatable
            if allocatable:
                cpu = parse_cpu(allocatable.get('cpu', '1'))
                memory = parse_memory(allocatable.get('memory', '1Gi'))
                min_cpu = min(min_cpu, cpu)
                min_memory = min(min_memory, memory)
        
        logger.info(f"Cluster minimum allocatable: CPU={min_cpu} cores, Memory={min_memory / (1024**3):.2f}Gi")
        return min_cpu, int(min_memory)
    
    def should_process_pod(self, pod: client.V1Pod) -> bool:
        """Check if a pod should be processed by this controller."""
        # Must be in Pending phase
        if pod.status.phase != "Pending":
            return False
        
        # Must have opt-in annotation
        annotations = pod.metadata.annotations or {}
        if annotations.get(OPT_IN_ANNOTATION) != OPT_IN_VALUE:
            return False
        
        # Check max fix attempts
        attempt_count = int(annotations.get(FIX_ATTEMPT_ANNOTATION, "0"))
        if attempt_count >= MAX_FIX_ATTEMPTS:
            logger.warning(f"Pod {pod.metadata.name} has exceeded max fix attempts ({MAX_FIX_ATTEMPTS})")
            return False
        
        # Skip if already processed in this session
        pod_key = f"{pod.metadata.namespace}/{pod.metadata.name}/{pod.metadata.uid}"
        if pod_key in self._processed_pods:
            return False
        
        return True
    
    def is_pending_due_to_resources(self, pod: client.V1Pod) -> bool:
        """Check if pod is pending due to insufficient resources."""
        try:
            # Get events for this pod
            field_selector = f"involvedObject.name={pod.metadata.name},involvedObject.namespace={pod.metadata.namespace}"
            events = self.v1.list_namespaced_event(
                namespace=pod.metadata.namespace,
                field_selector=field_selector
            )
            
            for event in events.items:
                if event.reason == "FailedScheduling":
                    if is_resource_insufficient_event(event.message):
                        logger.info(f"Pod {pod.metadata.name} pending due to: {event.message}")
                        return True
        except ApiException as e:
            logger.error(f"Error fetching events: {e}")
        
        return False
    
    def create_fixed_pod_spec(self, original_pod: client.V1Pod) -> client.V1Pod:
        """Create a new pod spec with fixed resources."""
        # Get cluster capacity
        node_cpu, node_memory = self.get_cluster_capacity()
        
        # Calculate safe resources
        cpu_req, mem_req, cpu_lim, mem_lim = calculate_safe_resources(
            node_cpu, node_memory, RESOURCE_SAFETY_MARGIN
        )
        
        logger.info(f"Calculated resources: CPU={cpu_req}/{cpu_lim}, Memory={mem_req}/{mem_lim}")
        
        # Deep copy the pod
        new_pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=original_pod.metadata.name,
                namespace=original_pod.metadata.namespace,
                labels=copy.deepcopy(original_pod.metadata.labels) or {},
                annotations=copy.deepcopy(original_pod.metadata.annotations) or {},
            ),
            spec=copy.deepcopy(original_pod.spec)
        )
        
        # Update annotations
        new_pod.metadata.annotations[MANAGED_LABEL] = "true"
        new_pod.metadata.annotations["original-cpu-request"] = self._get_original_cpu(original_pod)
        new_pod.metadata.annotations["original-memory-request"] = self._get_original_memory(original_pod)
        
        # Increment fix attempt counter
        current_attempts = int(new_pod.metadata.annotations.get(FIX_ATTEMPT_ANNOTATION, "0"))
        new_pod.metadata.annotations[FIX_ATTEMPT_ANNOTATION] = str(current_attempts + 1)
        
        # Update resources for all containers
        for container in new_pod.spec.containers:
            container.resources = client.V1ResourceRequirements(
                requests={"cpu": cpu_req, "memory": mem_req},
                limits={"cpu": cpu_lim, "memory": mem_lim}
            )
        
        # Clear fields that shouldn't be set on creation
        new_pod.metadata.resource_version = None
        new_pod.metadata.uid = None
        new_pod.metadata.creation_timestamp = None
        new_pod.status = None
        
        return new_pod
    
    def _get_original_cpu(self, pod: client.V1Pod) -> str:
        """Get original CPU request from pod."""
        try:
            resources = pod.spec.containers[0].resources
            if resources and resources.requests:
                return str(resources.requests.get('cpu', 'unknown'))
        except (IndexError, AttributeError):
            pass
        return "unknown"
    
    def _get_original_memory(self, pod: client.V1Pod) -> str:
        """Get original memory request from pod."""
        try:
            resources = pod.spec.containers[0].resources
            if resources and resources.requests:
                return str(resources.requests.get('memory', 'unknown'))
        except (IndexError, AttributeError):
            pass
        return "unknown"
    
    def fix_pod(self, pod: client.V1Pod) -> bool:
        """
        Fix a pod by deleting and recreating with proper resources.
        
        Returns:
            True if successful, False otherwise
        """
        pod_name = pod.metadata.name
        namespace = pod.metadata.namespace
        
        logger.info(f"Fixing pod {namespace}/{pod_name}")
        
        # Create the fixed pod spec before deleting
        try:
            new_pod = self.create_fixed_pod_spec(pod)
        except Exception as e:
            logger.error(f"Error creating fixed pod spec: {e}")
            return False
        
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would delete and recreate pod {namespace}/{pod_name}")
            logger.info(f"[DRY-RUN] New resources: {new_pod.spec.containers[0].resources}")
            return True
        
        # Delete the original pod
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                grace_period_seconds=0
            )
            logger.info(f"Deleted pod {namespace}/{pod_name}")
        except ApiException as e:
            if e.status != 404:  # Ignore if already deleted
                logger.error(f"Error deleting pod: {e}")
                return False
        
        # Wait a moment for deletion to complete
        time.sleep(1)
        
        # Create the new pod
        try:
            self.v1.create_namespaced_pod(
                namespace=namespace,
                body=new_pod
            )
            logger.info(f"Created fixed pod {namespace}/{pod_name}")
            return True
        except ApiException as e:
            logger.error(f"Error creating pod: {e}")
            return False
    
    def process_pod(self, pod: client.V1Pod) -> None:
        """Process a single pod event."""
        pod_name = pod.metadata.name
        namespace = pod.metadata.namespace
        pod_key = f"{namespace}/{pod_name}/{pod.metadata.uid}"
        
        if not self.should_process_pod(pod):
            return
        
        logger.info(f"Processing pending pod: {namespace}/{pod_name}")
        
        if not self.is_pending_due_to_resources(pod):
            logger.info(f"Pod {pod_name} is not pending due to resources, skipping")
            return
        
        # Mark as processed to avoid re-processing
        self._processed_pods.add(pod_key)
        
        # Fix the pod
        if self.fix_pod(pod):
            logger.info(f"Successfully fixed pod {namespace}/{pod_name}")
        else:
            logger.error(f"Failed to fix pod {namespace}/{pod_name}")
    
    def run(self) -> None:
        """Run the controller main loop."""
        logger.info("Starting Pod Resource Controller")
        logger.info(f"Watching namespace: {self.namespace or 'all namespaces'}")
        logger.info(f"Dry run mode: {self.dry_run}")
        logger.info(f"Opt-in annotation: {OPT_IN_ANNOTATION}={OPT_IN_VALUE}")
        
        w = watch.Watch()
        
        while True:
            try:
                logger.info("Starting watch for pod events...")
                
                if self.namespace:
                    stream = w.stream(
                        self.v1.list_namespaced_pod,
                        namespace=self.namespace,
                        timeout_seconds=WATCH_TIMEOUT_SECONDS
                    )
                else:
                    stream = w.stream(
                        self.v1.list_pod_for_all_namespaces,
                        timeout_seconds=WATCH_TIMEOUT_SECONDS
                    )
                
                for event in stream:
                    event_type = event['type']
                    pod = event['object']
                    
                    if event_type in ('ADDED', 'MODIFIED'):
                        self.process_pod(pod)
                        
            except ApiException as e:
                logger.error(f"API error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(5)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Pod Resource Controller - Fixes pods with excessive resource requests"
    )
    parser.add_argument(
        "--namespace", "-n",
        default="",
        help="Namespace to watch (default: all namespaces)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no changes made)"
    )
    parser.add_argument(
        "--in-cluster",
        action="store_true",
        help="Use in-cluster config (for running inside Kubernetes)"
    )
    
    args = parser.parse_args()
    
    # Load Kubernetes configuration
    try:
        if args.in_cluster:
            config.load_incluster_config()
            logger.info("Loaded in-cluster configuration")
        else:
            config.load_kube_config()
            logger.info("Loaded kubeconfig from default location")
    except Exception as e:
        logger.error(f"Failed to load Kubernetes config: {e}")
        sys.exit(1)
    
    # Create and run controller
    controller = PodResourceController(
        namespace=args.namespace,
        dry_run=args.dry_run
    )
    
    try:
        controller.run()
    except KeyboardInterrupt:
        logger.info("Controller stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
