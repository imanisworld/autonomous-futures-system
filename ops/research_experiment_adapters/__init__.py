"""Explicit built-in Experiment Runner adapter registrations."""
from __future__ import annotations


def register_builtin_adapters() -> None:
    """Register only reviewed, setup-specific adapters."""
    from ops.research_experiment_runner import register_execution_adapter
    from .options_212c_target_geometry import (
        SETUP_TYPE,
        run_options_212c_target_geometry,
    )

    register_execution_adapter(SETUP_TYPE, run_options_212c_target_geometry)
