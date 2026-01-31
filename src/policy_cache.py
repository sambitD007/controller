"""In-memory cache for PodResourcePolicy objects."""

import logging
import threading
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PolicySpec:
    """Parsed policy specification."""
    name: str
    namespace: str
    target_names: List[str] = field(default_factory=list)
    target_label_selector: Dict[str, str] = field(default_factory=dict)
    target_namespace: str = ""
    resources: Dict[str, Dict[str, str]] = field(default_factory=dict)
    enabled: bool = True
    
    @classmethod
    def from_crd(cls, crd_object: Dict[str, Any]) -> "PolicySpec":
        """Create PolicySpec from CRD object."""
        metadata = crd_object.get("metadata", {})
        spec = crd_object.get("spec", {})
        target_pods = spec.get("targetPods", {})
        
        return cls(
            name=metadata.get("name", ""),
            namespace=metadata.get("namespace", "default"),
            target_names=target_pods.get("names", []),
            target_label_selector=target_pods.get("labelSelector", {}),
            target_namespace=target_pods.get("namespace", metadata.get("namespace", "default")),
            resources=spec.get("resources", {}),
            enabled=spec.get("enabled", True)
        )
    
    def matches_pod(self, pod) -> bool:
        """Check if a pod matches this policy's target criteria."""
        if not self.enabled:
            return False
        
        pod_name = pod.metadata.name
        pod_namespace = pod.metadata.namespace
        pod_labels = pod.metadata.labels or {}
        
        # Check namespace
        if self.target_namespace and pod_namespace != self.target_namespace:
            return False
        
        # Check by name
        if self.target_names and pod_name in self.target_names:
            return True
        
        # Check by label selector
        if self.target_label_selector:
            matches_all = True
            for key, value in self.target_label_selector.items():
                if pod_labels.get(key) != value:
                    matches_all = False
                    break
            if matches_all:
                return True
        
        return False


class PolicyCache:
    """Thread-safe cache for PodResourcePolicy objects."""
    
    def __init__(self):
        """Initialize the cache."""
        self._policies: Dict[str, PolicySpec] = {}
        self._lock = threading.RLock()
    
    def _make_key(self, namespace: str, name: str) -> str:
        """Create a cache key from namespace and name."""
        return f"{namespace}/{name}"
    
    def add_or_update(self, crd_object: Dict[str, Any]) -> PolicySpec:
        """
        Add or update a policy in the cache.
        
        Args:
            crd_object: The CRD object from Kubernetes API
            
        Returns:
            The parsed PolicySpec
        """
        policy = PolicySpec.from_crd(crd_object)
        key = self._make_key(policy.namespace, policy.name)
        
        with self._lock:
            self._policies[key] = policy
            logger.info(f"Cached policy: {key}")
        
        return policy
    
    def remove(self, namespace: str, name: str) -> Optional[PolicySpec]:
        """
        Remove a policy from the cache.
        
        Args:
            namespace: Policy namespace
            name: Policy name
            
        Returns:
            The removed PolicySpec or None
        """
        key = self._make_key(namespace, name)
        
        with self._lock:
            policy = self._policies.pop(key, None)
            if policy:
                logger.info(f"Removed policy from cache: {key}")
            return policy
    
    def get(self, namespace: str, name: str) -> Optional[PolicySpec]:
        """
        Get a policy from the cache.
        
        Args:
            namespace: Policy namespace
            name: Policy name
            
        Returns:
            The PolicySpec or None
        """
        key = self._make_key(namespace, name)
        
        with self._lock:
            return self._policies.get(key)
    
    def get_all(self) -> List[PolicySpec]:
        """
        Get all policies in the cache.
        
        Returns:
            List of all PolicySpec objects
        """
        with self._lock:
            return list(self._policies.values())
    
    def find_matching_policy(self, pod) -> Optional[PolicySpec]:
        """
        Find a policy that matches the given pod.
        
        Args:
            pod: Kubernetes Pod object
            
        Returns:
            The first matching PolicySpec or None
        """
        with self._lock:
            for policy in self._policies.values():
                if policy.matches_pod(pod):
                    return policy
        return None
    
    def get_target_namespaces(self) -> List[str]:
        """
        Get list of all namespaces that have pods targeted by policies.
        
        Returns:
            List of namespace names
        """
        namespaces = set()
        
        with self._lock:
            for policy in self._policies.values():
                if policy.enabled and policy.target_namespace:
                    namespaces.add(policy.target_namespace)
        
        return list(namespaces)
    
    def clear(self) -> None:
        """Clear all policies from cache."""
        with self._lock:
            self._policies.clear()
            logger.info("Cleared policy cache")
