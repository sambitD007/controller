# CRD-Based Pod Resource Controller - Architecture

## Overview

A Kubernetes controller that watches **PodResourcePolicy** Custom Resources (CRD) 
and ensures that specified pods match the resource specifications defined in the CR.
When pods don't match the desired resources, the controller reconciles them.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      Pod Resource Controller (Operator)                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────────┐                                                           │
│  │   CRD Watcher    │                                                           │
│  │                  │                                                           │
│  │ Watch            │                                                           │
│  │ PodResourcePolicy│                                                           │
│  │ objects          │                                                           │
│  └────────┬─────────┘                                                           │
│           │                                                                      │
│           ▼                                                                      │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐          │
│  │  Policy Cache    │───▶│   Pod Watcher    │───▶│   Reconciler     │          │
│  │                  │    │                  │    │                  │          │
│  │ Store active     │    │ Watch pods that  │    │ Compare actual   │          │
│  │ policies with    │    │ match policy     │    │ vs desired       │          │
│  │ target pods      │    │ selectors        │    │ resources        │          │
│  └──────────────────┘    └──────────────────┘    └────────┬─────────┘          │
│                                                           │                     │
│                                                           ▼                     │
│                                                  ┌──────────────────┐          │
│                                                  │   Pod Fixer      │          │
│                                                  │                  │          │
│                                                  │ Delete & recreate│          │
│                                                  │ pod with correct │          │
│                                                  │ resources        │          │
│                                                  └──────────────────┘          │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │      Kubernetes Cluster       │
                        │                               │
                        │  ┌─────────────────────────┐  │
                        │  │  PodResourcePolicy CRD  │  │
                        │  │  (Custom Resources)     │  │
                        │  └─────────────────────────┘  │
                        │                               │
                        │  ┌─────────────────────────┐  │
                        │  │   Managed Pods          │  │
                        │  └─────────────────────────┘  │
                        └───────────────────────────────┘
```

## Custom Resource Definition (CRD)

### PodResourcePolicy CRD Schema

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: podresourcepolicies.resources.example.com
spec:
  group: resources.example.com
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              required:
                - targetPods
                - resources
              properties:
                targetPods:
                  type: object
                  properties:
                    names:
                      type: array
                      items:
                        type: string
                    labelSelector:
                      type: object
                      additionalProperties:
                        type: string
                    namespace:
                      type: string
                resources:
                  type: object
                  properties:
                    requests:
                      type: object
                      properties:
                        cpu:
                          type: string
                        memory:
                          type: string
                    limits:
                      type: object
                      properties:
                        cpu:
                          type: string
                        memory:
                          type: string
            status:
              type: object
              properties:
                phase:
                  type: string
                managedPods:
                  type: array
                  items:
                    type: string
                lastReconciled:
                  type: string
                message:
                  type: string
      subresources:
        status: {}
  scope: Namespaced
  names:
    plural: podresourcepolicies
    singular: podresourcepolicy
    kind: PodResourcePolicy
    shortNames:
      - prp
```

### Example Custom Resource

```yaml
apiVersion: resources.example.com/v1
kind: PodResourcePolicy
metadata:
  name: fix-pending-pods
  namespace: default
spec:
  targetPods:
    # Option 1: Target specific pods by name
    names:
      - pending-demo
      - another-pod
    # Option 2: Target pods by label selector
    labelSelector:
      app: my-app
      environment: test
    # Namespace to watch (defaults to CR's namespace)
    namespace: default
  resources:
    requests:
      cpu: "100m"
      memory: "128Mi"
    limits:
      cpu: "500m"
      memory: "256Mi"
```

## Components

### 1. CRD Watcher
- Watches for PodResourcePolicy custom resources
- Handles ADDED, MODIFIED, DELETED events
- Updates the Policy Cache with active policies

### 2. Policy Cache
- In-memory store of active PodResourcePolicy objects
- Maps policy name → target pod selectors + desired resources
- Thread-safe access for concurrent operations

### 3. Pod Watcher
- Watches pods across namespaces defined in policies
- Filters pods based on:
  - Exact name match (from policy.spec.targetPods.names)
  - Label selector match (from policy.spec.targetPods.labelSelector)
- Triggers reconciliation on pod events

### 4. Reconciler
- Core reconciliation logic
- Compares pod's actual resources with desired resources from policy
- Determines if pod needs fixing based on:
  - Resource requests mismatch
  - Resource limits mismatch
  - Pod in Pending state due to resources

### 5. Pod Fixer
- Deletes the non-compliant pod
- Recreates with resources from the policy
- Adds annotations for tracking:
  - `managed-by-policy: <policy-name>`
  - `last-reconciled: <timestamp>`

## Control Flow

```
                    ┌─────────────────────────┐
                    │    Controller Start     │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │  Register CRD Watcher   │
                    │  Register Pod Watcher   │
                    └───────────┬─────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │                                       │
            ▼                                       ▼
┌───────────────────────┐               ┌───────────────────────┐
│  CRD Event Received   │               │  Pod Event Received   │
└───────────┬───────────┘               └───────────┬───────────┘
            │                                       │
            ▼                                       ▼
┌───────────────────────┐               ┌───────────────────────┐
│  Update Policy Cache  │               │  Find Matching Policy │
│  - Add/Update policy  │               │  in Cache             │
│  - Remove on delete   │               └───────────┬───────────┘
└───────────┬───────────┘                           │
            │                                       │
            │                           ┌───────────┴───────────┐
            │                           │    Policy Found?      │
            │                           └───────────┬───────────┘
            │                                       │
            │                           ┌───────Yes─┴─No────────┐
            │                           │                       │
            │                           ▼                       ▼
            │               ┌───────────────────────┐   ┌───────────┐
            │               │  Compare Resources    │   │   Skip    │
            │               │  Actual vs Desired    │   └───────────┘
            │               └───────────┬───────────┘
            │                           │
            │               ┌───────────┴───────────┐
            │               │   Resources Match?    │
            │               └───────────┬───────────┘
            │                           │
            │               ┌─────Yes───┴───No──────┐
            │               │                       │
            │               ▼                       ▼
            │       ┌───────────────┐   ┌───────────────────────┐
            │       │  Pod OK       │   │  Delete & Recreate    │
            │       │  Update Status│   │  Pod with Correct     │
            │       └───────────────┘   │  Resources            │
            │                           └───────────┬───────────┘
            │                                       │
            └───────────────────────────────────────┤
                                                    │
                                                    ▼
                                        ┌───────────────────────┐
                                        │  Update Policy Status │
                                        │  - managedPods list   │
                                        │  - lastReconciled     │
                                        │  - phase/message      │
                                        └───────────────────────┘
```

## Reconciliation Logic

```
For each pod matching a policy:

1. GET pod's current resources
   - requests.cpu, requests.memory
   - limits.cpu, limits.memory

2. GET desired resources from PodResourcePolicy
   - spec.resources.requests.cpu/memory
   - spec.resources.limits.cpu/memory

3. COMPARE resources
   - Parse and normalize values (e.g., "100m" vs "0.1")
   - Check if actual matches desired (within tolerance)

4. IF mismatch detected:
   a. Log the discrepancy
   b. Delete the pod
   c. Wait for deletion
   d. Create new pod with:
      - Same metadata (name, labels, annotations)
      - Updated resources from policy
      - Added annotation: managed-by-policy

5. UPDATE PodResourcePolicy status:
   - Add pod to managedPods list
   - Update lastReconciled timestamp
   - Set phase to "Reconciled" or "Error"
```

## File Structure

```
controller/
├── ARCHITECTURE.md          # This file
├── requirements.txt         # Python dependencies
├── crd/
│   └── podresourcepolicy.yaml   # CRD definition
├── examples/
│   ├── sample-policy.yaml       # Example PodResourcePolicy
│   └── test-pod.yaml            # Test pod for the policy
├── src/
│   ├── __init__.py
│   ├── config.py            # Configuration settings
│   ├── utils.py             # Helper functions
│   ├── crd_client.py        # Custom resource client
│   ├── policy_cache.py      # In-memory policy cache
│   ├── reconciler.py        # Reconciliation logic
│   └── controller.py        # Main controller
└── run.py                   # Entry point
```

## Status Subresource

The controller updates the PodResourcePolicy status to reflect current state:

```yaml
status:
  phase: Reconciled          # Reconciled | Pending | Error
  managedPods:
    - default/pending-demo
    - default/another-pod
  lastReconciled: "2024-01-15T10:30:00Z"
  message: "Successfully reconciled 2 pods"
```

## Key Design Decisions

### CRD-Based Configuration
- Declarative: Users define desired state in CR
- Kubernetes-native: Uses standard K8s patterns
- Auditable: Changes tracked via K8s API

### Namespace Scoping
- PodResourcePolicy is namespaced
- Can target pods in same namespace (default)
- Or specify different namespace in spec

### Selector Options
- **By Name**: Exact pod name matching
- **By Labels**: Flexible label selector matching
- Can combine both for precise targeting

### Idempotent Reconciliation
- Controller continuously reconciles to desired state
- Safe to restart or run multiple times
- No harmful side effects from repeated runs

## Limitations (Local Dev Controller)

1. **No HA**: Single instance, no leader election
2. **No Webhooks**: No admission control
3. **Simple Caching**: In-memory only
4. **Basic Error Handling**: No exponential backoff
5. **No Metrics**: No Prometheus integration

## Future Enhancements (Production)

- Add validating/mutating admission webhooks
- Implement leader election for HA
- Add Prometheus metrics and alerts
- Support for Deployments/StatefulSets (not just Pods)
- Dry-run mode in CR spec
- Resource auto-calculation based on node capacity
