"""Main controller logic for Pod Resource Controller."""

import logging
import threading
import time
from typing import Optional

from kubernetes import client, watch
from kubernetes.client.rest import ApiException

from .config import WATCH_TIMEOUT_SECONDS, RECONCILE_INTERVAL_SECONDS
from .crd_client import PodResourcePolicyClient
from .policy_cache import PolicyCache
from .reconciler import PodReconciler

logger = logging.getLogger(__name__)


class PodResourceController:
    """
    CRD-based controller that watches PodResourcePolicy objects
    and reconciles pods to match their specifications.
    """
    
    def __init__(self, namespace: str = "", dry_run: bool = False):
        """
        Initialize the controller.
        
        Args:
            namespace: Namespace to watch ("" for all namespaces)
            dry_run: If True, don't make actual changes
        """
        self.namespace = namespace
        self.dry_run = dry_run
        self.v1 = client.CoreV1Api()
        
        self.policy_client = PodResourcePolicyClient()
        self.policy_cache = PolicyCache()
        self.reconciler = PodReconciler(dry_run=dry_run)
        
        self._stop_event = threading.Event()
        self._processed_pods = set()
    
    def load_existing_policies(self) -> int:
        """
        Load existing policies into cache on startup.
        
        Returns:
            Number of policies loaded
        """
        logger.info("Loading existing policies...")
        policies = self.policy_client.list_policies(self.namespace)
        
        for policy_obj in policies:
            self.policy_cache.add_or_update(policy_obj)
        
        count = len(policies)
        logger.info(f"Loaded {count} existing policies")
        return count
    
    def handle_policy_event(self, event_type: str, policy_obj: dict) -> None:
        """
        Handle a policy watch event.
        
        Args:
            event_type: ADDED, MODIFIED, or DELETED
            policy_obj: The policy object from the event
        """
        metadata = policy_obj.get("metadata", {})
        name = metadata.get("name", "")
        namespace = metadata.get("namespace", "default")
        
        if event_type in ("ADDED", "MODIFIED"):
            policy = self.policy_cache.add_or_update(policy_obj)
            logger.info(f"Policy {event_type}: {namespace}/{name}")
            
            # Trigger reconciliation for this policy
            if policy.enabled:
                results = self.reconciler.reconcile_all_pods_for_policy(policy)
                self._update_policy_status(policy, results)
                
        elif event_type == "DELETED":
            self.policy_cache.remove(namespace, name)
            logger.info(f"Policy DELETED: {namespace}/{name}")
    
    def handle_pod_event(self, event_type: str, pod) -> None:
        """
        Handle a pod watch event.
        
        Args:
            event_type: ADDED, MODIFIED, or DELETED
            pod: The pod object from the event
        """
        if event_type == "DELETED":
            return
        
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace
        pod_key = f"{pod_namespace}/{pod_name}/{pod.metadata.uid}"
        
        # Skip if already processed in this cycle
        if pod_key in self._processed_pods:
            return
        
        # Find matching policy
        policy = self.policy_cache.find_matching_policy(pod)
        
        if not policy:
            return
        
        logger.debug(f"Pod {pod_namespace}/{pod_name} matches policy {policy.name}")
        
        # Mark as processed
        self._processed_pods.add(pod_key)
        
        # Reconcile if needed
        if self.reconciler.needs_reconciliation(pod, policy):
            result = self.reconciler.reconcile_pod(pod, policy)
            self._update_policy_status(policy, [result])
    
    def _update_policy_status(self, policy, results: list) -> None:
        """Update the status of a policy after reconciliation."""
        if not results:
            return
        
        managed_pods = []
        has_error = False
        
        for result in results:
            managed_pods.append({
                "name": result["name"],
                "namespace": result["namespace"],
                "status": result["status"],
                "lastUpdated": result["lastUpdated"]
            })
            if result["status"] == "error":
                has_error = True
        
        phase = "Error" if has_error else "Reconciled"
        message = f"Processed {len(results)} pod(s)"
        
        self.policy_client.update_policy_status(
            name=policy.name,
            namespace=policy.namespace,
            phase=phase,
            managed_pods=managed_pods,
            message=message
        )
    
    def watch_policies(self) -> None:
        """Watch for PodResourcePolicy events in a loop."""
        logger.info("Starting policy watcher...")
        
        while not self._stop_event.is_set():
            try:
                for event in self.policy_client.watch_policies(
                    namespace=self.namespace,
                    timeout=WATCH_TIMEOUT_SECONDS
                ):
                    if self._stop_event.is_set():
                        break
                    
                    event_type = event["type"]
                    policy_obj = event["object"]
                    self.handle_policy_event(event_type, policy_obj)
                    
            except ApiException as e:
                logger.error(f"Policy watch error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in policy watcher: {e}")
                time.sleep(5)
    
    def watch_pods(self) -> None:
        """Watch for Pod events in a loop."""
        logger.info("Starting pod watcher...")
        w = watch.Watch()
        
        while not self._stop_event.is_set():
            try:
                # Get namespaces to watch from policies
                target_namespaces = self.policy_cache.get_target_namespaces()
                
                if not target_namespaces:
                    logger.debug("No target namespaces, watching all")
                    stream = w.stream(
                        self.v1.list_pod_for_all_namespaces,
                        timeout_seconds=WATCH_TIMEOUT_SECONDS
                    )
                else:
                    # Watch specific namespace (first one for simplicity)
                    # In production, you'd want to watch multiple namespaces
                    ns = target_namespaces[0]
                    stream = w.stream(
                        self.v1.list_namespaced_pod,
                        namespace=ns,
                        timeout_seconds=WATCH_TIMEOUT_SECONDS
                    )
                
                for event in stream:
                    if self._stop_event.is_set():
                        break
                    
                    event_type = event["type"]
                    pod = event["object"]
                    self.handle_pod_event(event_type, pod)
                    
            except ApiException as e:
                logger.error(f"Pod watch error: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in pod watcher: {e}")
                time.sleep(5)
    
    def periodic_reconcile(self) -> None:
        """Periodically reconcile all policies."""
        logger.info(f"Starting periodic reconciler (interval: {RECONCILE_INTERVAL_SECONDS}s)")
        
        while not self._stop_event.is_set():
            time.sleep(RECONCILE_INTERVAL_SECONDS)
            
            if self._stop_event.is_set():
                break
            
            # Clear processed pods to allow re-processing
            self._processed_pods.clear()
            
            logger.debug("Running periodic reconciliation...")
            
            for policy in self.policy_cache.get_all():
                if not policy.enabled:
                    continue
                
                results = self.reconciler.reconcile_all_pods_for_policy(policy)
                if results:
                    self._update_policy_status(policy, results)
    
    def run(self) -> None:
        """Run the controller."""
        logger.info("=" * 60)
        logger.info("Starting Pod Resource Controller")
        logger.info("=" * 60)
        logger.info(f"Namespace: {self.namespace or 'all namespaces'}")
        logger.info(f"Dry run: {self.dry_run}")
        
        # Load existing policies
        self.load_existing_policies()
        
        # Start watcher threads
        policy_thread = threading.Thread(
            target=self.watch_policies,
            name="policy-watcher",
            daemon=True
        )
        
        pod_thread = threading.Thread(
            target=self.watch_pods,
            name="pod-watcher",
            daemon=True
        )
        
        reconcile_thread = threading.Thread(
            target=self.periodic_reconcile,
            name="periodic-reconciler",
            daemon=True
        )
        
        policy_thread.start()
        pod_thread.start()
        reconcile_thread.start()
        
        logger.info("Controller is running. Press Ctrl+C to stop.")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
            self._stop_event.set()
    
    def stop(self) -> None:
        """Stop the controller."""
        logger.info("Stopping controller...")
        self._stop_event.set()
