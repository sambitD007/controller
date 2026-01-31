"""Client for interacting with PodResourcePolicy CRD."""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.client.rest import ApiException

from .config import CRD_GROUP, CRD_VERSION, CRD_PLURAL

logger = logging.getLogger(__name__)


class PodResourcePolicyClient:
    """Client for PodResourcePolicy custom resources."""
    
    def __init__(self):
        """Initialize the CRD client."""
        self.custom_api = client.CustomObjectsApi()
    
    def list_policies(self, namespace: str = "") -> List[Dict[str, Any]]:
        """
        List all PodResourcePolicy objects.
        
        Args:
            namespace: Namespace to list from ("" for all namespaces)
            
        Returns:
            List of policy objects
        """
        try:
            if namespace:
                response = self.custom_api.list_namespaced_custom_object(
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    namespace=namespace,
                    plural=CRD_PLURAL
                )
            else:
                response = self.custom_api.list_cluster_custom_object(
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    plural=CRD_PLURAL
                )
            return response.get("items", [])
        except ApiException as e:
            if e.status == 404:
                logger.warning("CRD not found. Please install the CRD first.")
            else:
                logger.error(f"Error listing policies: {e}")
            return []
    
    def get_policy(self, name: str, namespace: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific PodResourcePolicy.
        
        Args:
            name: Policy name
            namespace: Policy namespace
            
        Returns:
            Policy object or None if not found
        """
        try:
            return self.custom_api.get_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                name=name
            )
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Error getting policy {namespace}/{name}: {e}")
            return None
    
    def update_policy_status(
        self,
        name: str,
        namespace: str,
        phase: str,
        managed_pods: List[Dict[str, str]],
        message: str = ""
    ) -> bool:
        """
        Update the status of a PodResourcePolicy.
        
        Args:
            name: Policy name
            namespace: Policy namespace
            phase: Status phase (Pending, Reconciling, Reconciled, Error)
            managed_pods: List of managed pod info dicts
            message: Status message
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current policy to get resourceVersion
            current = self.get_policy(name, namespace)
            if not current:
                return False
            
            # Prepare status update
            status = {
                "phase": phase,
                "managedPods": managed_pods,
                "lastReconciled": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "observedGeneration": current.get("metadata", {}).get("generation", 1)
            }
            
            # Update status subresource
            self.custom_api.patch_namespaced_custom_object_status(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                name=name,
                body={"status": status}
            )
            
            logger.debug(f"Updated status for policy {namespace}/{name}: {phase}")
            return True
            
        except ApiException as e:
            logger.error(f"Error updating policy status: {e}")
            return False
    
    def watch_policies(self, namespace: str = "", timeout: int = 300):
        """
        Create a watch stream for PodResourcePolicy objects.
        
        Args:
            namespace: Namespace to watch ("" for all namespaces)
            timeout: Watch timeout in seconds
            
        Yields:
            Watch events
        """
        from kubernetes import watch
        w = watch.Watch()
        
        try:
            if namespace:
                stream = w.stream(
                    self.custom_api.list_namespaced_custom_object,
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    namespace=namespace,
                    plural=CRD_PLURAL,
                    timeout_seconds=timeout
                )
            else:
                stream = w.stream(
                    self.custom_api.list_cluster_custom_object,
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    plural=CRD_PLURAL,
                    timeout_seconds=timeout
                )
            
            for event in stream:
                yield event
                
        except ApiException as e:
            logger.error(f"Watch error: {e}")
            raise
