"""Daemons — autonomous capability packages (inspired by OpenFang).

A Daemon is a self-contained autonomous workflow that runs in the background.
Unlike agents (external processes), Daemons are internal Python coroutines
that use AGOS infrastructure (EventBus, tools, knowledge) directly.

Set a goal, and the Daemon executes the entire workflow autonomously.
"""

from agos.daemons.base import Daemon, DaemonStatus
from agos.daemons.manager import DaemonManager
from agos.daemons.domain import DomainDaemon

__all__ = ["Daemon", "DaemonStatus", "DaemonManager", "DomainDaemon"]
