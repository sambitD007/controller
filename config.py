"""Configuration settings for the Pod Resource Controller."""

# Annotation that enables auto-fix for a pod
OPT_IN_ANNOTATION = "auto-fix-resources"
OPT_IN_VALUE = "true"

# Label to identify pods managed by this controller
MANAGED_LABEL = "resource-controller-managed"

# Safety margin: use this percentage of node's allocatable resources
RESOURCE_SAFETY_MARGIN = 0.5  # 50%

# Default resource values if calculation fails
DEFAULT_CPU_REQUEST = "100m"
DEFAULT_MEMORY_REQUEST = "128Mi"
DEFAULT_CPU_LIMIT = "500m"
DEFAULT_MEMORY_LIMIT = "256Mi"

# Watch settings
WATCH_NAMESPACE = ""  # Empty string = all namespaces
WATCH_TIMEOUT_SECONDS = 300

# Retry settings
MAX_FIX_ATTEMPTS = 3
FIX_ATTEMPT_ANNOTATION = "fix-attempt-count"
