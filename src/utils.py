"""Utility functions for resource parsing and comparison."""

import re
from typing import Tuple, Optional, Dict, Any
import json


def parse_cpu(cpu_string: str) -> float:
    """
    Parse CPU string to cores (float).
    
    Examples:
        "100m" -> 0.1
        "1" -> 1.0
        "2500m" -> 2.5
    """
    if not cpu_string:
        return 0.0
    
    cpu_string = str(cpu_string).strip()
    
    if cpu_string.endswith('m'):
        return float(cpu_string[:-1]) / 1000
    return float(cpu_string)


def format_cpu(cores: float) -> str:
    """
    Format CPU cores to Kubernetes string.
    
    Examples:
        0.1 -> "100m"
        1.5 -> "1500m"
    """
    millicores = int(cores * 1000)
    return f"{millicores}m"


def parse_memory(memory_string: str) -> int:
    """
    Parse memory string to bytes.
    
    Examples:
        "128Mi" -> 134217728
        "1Gi" -> 1073741824
        "512M" -> 536870912
    """
    if not memory_string:
        return 0
    
    memory_string = str(memory_string).strip()
    
    units = {
        'Ki': 1024,
        'Mi': 1024 ** 2,
        'Gi': 1024 ** 3,
        'Ti': 1024 ** 4,
        'K': 1000,
        'M': 1000 ** 2,
        'G': 1000 ** 3,
        'T': 1000 ** 4,
    }
    
    for suffix, multiplier in units.items():
        if memory_string.endswith(suffix):
            value = float(memory_string[:-len(suffix)])
            return int(value * multiplier)
    
    # Plain bytes
    return int(float(memory_string))


def format_memory(bytes_value: int) -> str:
    """
    Format bytes to human-readable Kubernetes memory string.
    
    Uses Mi (mebibytes) for readability.
    """
    mi = bytes_value / (1024 ** 2)
    if mi >= 1024:
        gi = mi / 1024
        return f"{int(gi)}Gi"
    return f"{int(mi)}Mi"


def resources_match(
    actual_cpu: str,
    actual_memory: str,
    desired_cpu: str,
    desired_memory: str,
    tolerance: float = 0.001
) -> bool:
    """
    Compare actual resources with desired resources.
    
    Returns True if they match within tolerance.
    """
    actual_cpu_val = parse_cpu(actual_cpu)
    desired_cpu_val = parse_cpu(desired_cpu)
    
    actual_memory_val = parse_memory(actual_memory)
    desired_memory_val = parse_memory(desired_memory)
    
    cpu_matches = abs(actual_cpu_val - desired_cpu_val) <= tolerance
    memory_matches = actual_memory_val == desired_memory_val
    
    return cpu_matches and memory_matches


def get_pod_resources(pod) -> Dict[str, Dict[str, str]]:
    """
    Extract resource requests and limits from a pod.
    
    Returns:
        {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"}
        }
    """
    result = {
        "requests": {"cpu": "", "memory": ""},
        "limits": {"cpu": "", "memory": ""}
    }
    
    try:
        # Get resources from first container
        if pod.spec.containers:
            resources = pod.spec.containers[0].resources
            if resources:
                if resources.requests:
                    result["requests"]["cpu"] = str(resources.requests.get("cpu", ""))
                    result["requests"]["memory"] = str(resources.requests.get("memory", ""))
                if resources.limits:
                    result["limits"]["cpu"] = str(resources.limits.get("cpu", ""))
                    result["limits"]["memory"] = str(resources.limits.get("memory", ""))
    except (AttributeError, IndexError):
        pass
    
    return result


def pod_matches_label_selector(pod, label_selector: Dict[str, str]) -> bool:
    """Check if a pod's labels match the given selector."""
    if not label_selector:
        return False
    
    pod_labels = pod.metadata.labels or {}
    
    for key, value in label_selector.items():
        if pod_labels.get(key) != value:
            return False
    
    return True


def serialize_resources(resources: Dict) -> str:
    """Serialize resources dict to JSON string for annotation."""
    return json.dumps(resources)


def deserialize_resources(resources_str: str) -> Dict:
    """Deserialize resources JSON string from annotation."""
    try:
        return json.loads(resources_str)
    except (json.JSONDecodeError, TypeError):
        return {}
