"""Configuration settings for the Pod Resource Controller."""

# CRD Settings
CRD_GROUP = "resources.example.com"
CRD_VERSION = "v1"
CRD_PLURAL = "podresourcepolicies"
CRD_KIND = "PodResourcePolicy"

# Controller annotations
MANAGED_BY_ANNOTATION = "managed-by-policy"
LAST_RECONCILED_ANNOTATION = "last-reconciled-at"
ORIGINAL_RESOURCES_ANNOTATION = "original-resources"

# Watch settings
WATCH_TIMEOUT_SECONDS = 300
RECONCILE_INTERVAL_SECONDS = 30

# Resource comparison tolerance (for floating point comparison)
RESOURCE_TOLERANCE = 0.001

# Default resources if not specified in policy
DEFAULT_CPU_REQUEST = "100m"
DEFAULT_MEMORY_REQUEST = "128Mi"
DEFAULT_CPU_LIMIT = "500m"
DEFAULT_MEMORY_LIMIT = "256Mi"
