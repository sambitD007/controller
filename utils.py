"""Utility functions for resource parsing and calculation."""

import re
from typing import Tuple, Optional


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
    return int(memory_string)


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


def is_resource_insufficient_event(event_message: str) -> bool:
    """Check if an event message indicates insufficient resources."""
    if not event_message:
        return False
    
    patterns = [
        r"Insufficient cpu",
        r"Insufficient memory",
        r"nodes are available.*Insufficient",
        r"didn't match Pod's node affinity/selector",
    ]
    
    for pattern in patterns:
        if re.search(pattern, event_message, re.IGNORECASE):
            return True
    return False


def calculate_safe_resources(
    node_allocatable_cpu: float,
    node_allocatable_memory: int,
    safety_margin: float = 0.5
) -> Tuple[str, str, str, str]:
    """
    Calculate safe resource requests and limits.
    
    Returns:
        Tuple of (cpu_request, memory_request, cpu_limit, memory_limit)
    """
    # Apply safety margin
    safe_cpu = node_allocatable_cpu * safety_margin
    safe_memory = int(node_allocatable_memory * safety_margin)
    
    # Requests are typically lower than limits
    cpu_request = format_cpu(safe_cpu * 0.2)  # 20% of safe CPU
    cpu_limit = format_cpu(safe_cpu * 0.5)    # 50% of safe CPU
    
    memory_request = format_memory(int(safe_memory * 0.2))  # 20% of safe memory
    memory_limit = format_memory(int(safe_memory * 0.5))    # 50% of safe memory
    
    return cpu_request, memory_request, cpu_limit, memory_limit
