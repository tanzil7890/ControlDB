"""Minimal HTML dashboard for ControlDB.

This is the "engineering + compliance" view used by Phase 2 of the guide. It
is deliberately small: HTML + a sprinkle of JS that calls the same REST API
the SDK uses.
"""

from .views import register_dashboard

__all__ = ["register_dashboard"]
