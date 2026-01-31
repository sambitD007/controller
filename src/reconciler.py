"""Reconciliation logic for Pod Resource Controller."""

import logging
import copy
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.client.rest import ApiException

from .config import (
    MANAGED_BY_ANNOTATION,
    LAST_RECONCILED_ANNOTATION,
    ORIGINAL_RESOURCES_ANNOTATION,
    RESOURCE_TOLERANCE,
)
from .utils import (
    resources_match,
    get_pod_resources,
    serialize_resources,
)
from .policy_cache import PolicySpec

logger = logging.getLogger(__name__)


class PodReconciler:
    """Reconciles pods to match their policy specifications."""
    
    def __init__(self, dry_run: bool = False):
        """
        Initialize the reconciler.
        
        Args:
            dry_run: If True, don't make actual changes
        """
        self.dry_run = dry_run
        self.v1 = client.CoreV1Api()
        self._reconciled_pods: Dict[str, str] = {}  # pod_key -> last_reconciled_time
    
    def needs_reconciliation(self, pod, policy: PolicySpec) -> bool:
        """
        Check if a pod needs to be reconciled.
        
        Args:
            pod: Kubernetes Pod object
            policy: The matching PolicySpec
            
        Returns:
            True if pod needs reconciliation
        """
        # Get current pod resources
        current = get_pod_resources(pod)
        desired = policy.resources
        
        # Check requests
        desired_requests = desired.get("requests", {})
        if desired_requests:
            if not resources_match(
                current["requests"].get("cpu", ""),
                current["requests"].get("memory", ""),
                desired_requests.get("cpu", ""),
                desired_requests.get("memory", ""),
                RESOURCE_TOLERANCE
            ):
                logger.debug(f"Pod {pod.metadata.name} requests don't match policy")
                return True
        
        # Check limits
        desired_limits = desired.get("limits", {})
        if desired_limits:
            if not resources_match(
                current["limits"].get("cpu", ""),
                current["limits"].get("memory", ""),
                desired_limits.get("cpu", ""),
                desired_limits.get("memory", ""),
                RESOURCE_TOLERANCE
            ):
                logger.debug(f"Pod {pod.metadata.name} limits don't match policy")
                return True
        
        return False
    
    def reconcile_pod(self, pod, policy: PolicySpec) -> Dict[str, Any]:
        """
        Reconcile a pod to match the policy specification.
        
        Args:
            pod: Kubernetes Pod object
            policy: The matching PolicySpec
            
        Returns:
            Result dict with status info
        """
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace
        pod_key = f"{pod_namespace}/{pod_name}"
        
        result = {
            "name": pod_name,
            "namespace": pod_namespace,
            "status": "unchanged",
            "lastUpdated": datetime.now(timezone.utc).isoformat()
        }
        
        # Check if reconciliation is needed
        if not self.needs_reconciliation(pod, policy):
            logger.debug(f"Pod {pod_key} already matches policy, no action needed")
            result["status"] = "compliant"
            return result
        
        logger.info(f"Pod {pod_key} needs reconciliation to match policy {policy.name}")
        
        # Store original resources
        original_resources = get_pod_resources(pod)
        
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would reconcile pod {pod_key}")
            logger.info(f"[DRY-RUN] Current: {original_resources}")
            logger.info(f"[DRY-RUN] Desired: {policy.resources}")
            result["status"] = "dry-run"
            return result
        
        # Create new pod spec with desired resources
        new_pod = self._create_reconciled_pod(pod, policy, original_resources)
        
        # Delete original pod
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name,
                namespace=pod_namespace,
                grace_period_seconds=0
            )
            logger.info(f"Deleted pod {pod_key}")
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Error deleting pod {pod_key}: {e}")
                result["status"] = "error"
                return result
        
        # Wait for deletion
        time.sleep(1)
        
        # Create new pod
        try:
            self.v1.create_namespaced_pod(
                namespace=pod_namespace,
                body=new_pod
            )
            logger.info(f"Created reconciled pod {pod_key}")
            result["status"] = "reconciled"
            self._reconciled_pods[pod_key] = datetime.now(timezone.utc).isoformat()
        except ApiException as e:
            logger.error(f"Error creating pod {pod_key}: {e}")
            result["status"] = "error"
        
        return result
    
    def _create_reconciled_pod(
        self,
        original_pod,
        policy: PolicySpec,
        original_resources: Dict
    ) -> client.V1Pod:
        """
        Create a new pod spec with resources from policy.
        
        Args:
            original_pod: Original Kubernetes Pod object
            policy: The PolicySpec with desired resources
            original_resources: Original resource values for annotation
            
        Returns:
            New V1Pod object
        """
        desired = policy.resources
        
        # Build new resource requirements
        requests = {}
        limits = {}
        
        if "requests" in desired:
            if desired["requests"].get("cpu"):
                requests["cpu"] = desired["requests"]["cpu"]
            if desired["requests"].get("memory"):
                requests["memory"] = desired["requests"]["memory"]
        
        if "limits" in desired:
            if desired["limits"].get("cpu"):
                limits["cpu"] = desired["limits"]["cpu"]
            if desired["limits"].get("memory"):
                limits["memory"] = desired["limits"]["memory"]
        
        # Create new pod
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
        new_pod.metadata.annotations[MANAGED_BY_ANNOTATION] = policy.name
        new_pod.metadata.annotations[LAST_RECONCILED_ANNOTATION] = datetime.now(timezone.utc).isoformat()
        new_pod.metadata.annotations[ORIGINAL_RESOURCES_ANNOTATION] = serialize_resources(original_resources)
        
        # Update resources for all containers
        for container in new_pod.spec.containers:
            container.resources = client.V1ResourceRequirements(
                requests=requests if requests else None,
                limits=limits if limits else None
            )
        
        # Clear fields that shouldn't be set on creation
        new_pod.metadata.resource_version = None
        new_pod.metadata.uid = None
        new_pod.metadata.creation_timestamp = None
        new_pod.status = None
        
        return new_pod
    
    def reconcile_all_pods_for_policy(self, policy: PolicySpec) -> List[Dict[str, Any]]:
        """
        Find and reconcile all pods matching a policy.
        
        Args:
            policy: The PolicySpec to reconcile
            
        Returns:
            List of result dicts for each pod
        """
        results = []
        
        if not policy.enabled:
            logger.debug(f"Policy {policy.name} is disabled, skipping")
            return results
        
        # List pods in target namespace
        try:
            pods = self.v1.list_namespaced_pod(
                namespace=policy.target_namespace
            )
        except ApiException as e:
            logger.error(f"Error listing pods in {policy.target_namespace}: {e}")
            return results
        
        # Find matching pods
        for pod in pods.items:
            if policy.matches_pod(pod):
                result = self.reconcile_pod(pod, policy)
                results.append(result)
        
        return results
