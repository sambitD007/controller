#!/usr/bin/env python3
"""
Pod Resource Controller - Entry Point

A CRD-based Kubernetes controller that watches PodResourcePolicy objects
and reconciles pods to match their resource specifications.

Usage:
    python run.py [--namespace NAMESPACE] [--dry-run] [--in-cluster]
"""

import argparse
import logging
import sys

from kubernetes import config

# Add src to path
sys.path.insert(0, ".")

from src.controller import PodResourceController

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Pod Resource Controller - Reconcile pods to match PodResourcePolicy specs"
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
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging"
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
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
        controller.stop()
        logger.info("Controller stopped")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Controller error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
