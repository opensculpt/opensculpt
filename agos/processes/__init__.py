"""Process management — OS-level subprocess supervision.

AGOS treats agent workloads as processes. This module provides:
- ProcessManager: spawn, monitor, kill real OS subprocesses
- ResourceMonitor: track CPU, memory, file I/O per process
- WorkloadDiscovery: auto-detect and auto-install agents from /workloads/
"""
