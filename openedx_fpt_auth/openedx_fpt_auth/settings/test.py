"""Test settings finalizer for FPT Auth."""

from .common import install_backends


def plugin_settings(settings):
    install_backends(settings)
