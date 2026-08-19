"""Production settings finalizer for FPT Auth."""

from .common import install_backends


def plugin_settings(settings):
    """Deduplicate backends added by Open edX production settings."""

    install_backends(settings)
