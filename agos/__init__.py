"""OpenSculpt — The Self-Evolving Agentic OS."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("opensculpt")
except PackageNotFoundError:
    __version__ = "0.1.0"  # fallback for development
