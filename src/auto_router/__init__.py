"""auto-router package."""


def _install_runtime_policies() -> None:
    from .benchmark_routing_policy import install_benchmark_routing_policy
    from .discovery_enabled_policy import install_enabled_discovery_policy

    install_enabled_discovery_policy()
    install_benchmark_routing_policy()


_install_runtime_policies()

__all__ = ["__version__"]
__version__ = "0.1.0"
