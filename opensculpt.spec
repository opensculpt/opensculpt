# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OpenSculpt — The Self-Evolving Agentic OS.

Builds a single-file `OpenSculpt.exe` (one-file mode) for distribution.
Includes the dashboard HTML template (embedded in app.py) and static assets.
"""

import os
import sys
from pathlib import Path

block_cipher = None
ROOT = os.path.abspath('.')

a = Analysis(
    ['agos/serve.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[
        ('agos/dashboard/static', 'agos/dashboard/static'),
    ] + ([('OpenSculpt_icon.jpg', '.')] if os.path.exists('OpenSculpt_icon.jpg') else []),
    hiddenimports=[
        # Core
        'agos', 'agos.cli', 'agos.cli.main', 'agos.cli.setup', 'agos.cli.agents', 'agos.cli.system',
        'agos.config', 'agos.serve', 'agos.boot', 'agos.os_agent', 'agos.session', 'agos.guard',
        'agos.types', 'agos.exceptions', 'agos.environment', 'agos.setup_store',
        # Kernel
        'agos.kernel', 'agos.kernel.runtime', 'agos.kernel.agent',
        # Events
        'agos.events', 'agos.events.bus', 'agos.events.tracing',
        # Knowledge
        'agos.knowledge', 'agos.knowledge.manager', 'agos.knowledge.db',
        'agos.knowledge.episodic', 'agos.knowledge.semantic', 'agos.knowledge.graph',
        'agos.knowledge.constraints', 'agos.knowledge.resolutions', 'agos.knowledge.tagged_store',
        # Processes
        'agos.processes', 'agos.processes.manager', 'agos.processes.registry',
        'agos.processes.workload', 'agos.processes.resources',
        # Tools
        'agos.tools', 'agos.tools.registry', 'agos.tools.builtins', 'agos.tools.extended',
        'agos.tools.docker_tool',
        # Evolution
        'agos.evolution', 'agos.evolution.cycle', 'agos.evolution.state',
        'agos.evolution.demand', 'agos.evolution.demand_solver', 'agos.evolution.tool_evolver',
        'agos.evolution.codegen', 'agos.evolution.sandbox', 'agos.evolution.meta',
        'agos.evolution.scout', 'agos.evolution.sync', 'agos.evolution.analyzer',
        'agos.evolution.source_patcher', 'agos.evolution.community', 'agos.evolution.contribute',
        'agos.evolution.manifest', 'agos.evolution.packages',
        # Dashboard
        'agos.dashboard', 'agos.dashboard.app',
        # Daemons
        'agos.daemons', 'agos.daemons.base', 'agos.daemons.manager',
        'agos.daemons.goal_runner', 'agos.daemons.domain', 'agos.daemons.gc',
        'agos.daemons.scheduler',
        # Policy
        'agos.policy', 'agos.policy.engine', 'agos.policy.audit', 'agos.policy.schema',
        # Other
        'agos.approval', 'agos.approval.gate',
        'agos.mcp', 'agos.mcp.client', 'agos.mcp.config',
        'agos.a2a', 'agos.a2a.server', 'agos.a2a.client',
        'agos.task_planner', 'agos.vibe_tools',
        'agos.intent', 'agos.intent.engine', 'agos.intent.planner', 'agos.intent.proactive',
        'agos.coordination', 'agos.coordination.team',
        'agos.sandbox', 'agos.sandbox.runner', 'agos.sandbox.executor',
        'agos.llm', 'agos.llm.claude_code',
        # Dependencies
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'starlette', 'pydantic', 'pydantic_settings',
        'httpx', 'anthropic', 'orjson', 'structlog', 'typer', 'rich',
        'aiosqlite', 'websockets', 'mcp',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL', 'cv2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OpenSculpt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    icon='OpenSculpt_icon.jpg' if os.path.exists('OpenSculpt_icon.jpg') else None,
    onefile=True,
)
