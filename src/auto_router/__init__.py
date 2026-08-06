"""auto-router package."""


def _install_runtime_policies() -> None:
    from .discovery_enabled_policy import install_enabled_discovery_policy

    install_enabled_discovery_policy()


_install_runtime_policies()

__all__ = ["__version__"]
__version__ = "0.1.0"
