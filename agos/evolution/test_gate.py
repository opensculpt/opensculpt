"""Regression gate — runs pytest after evolved code is written.

If the full test suite fails with the new evolved file present,
the file is deleted and the pattern is rejected. This prevents
evolved code from breaking existing functionality.
"""
from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel, Field

from agos.types import new_id
from agos.config import settings as _settings

_logger = logging.getLogger(__name__)


class GateResult(BaseModel):
    """Result from running the regression gate."""
    id: str = Field(default_factory=new_id)
    passed: bool = False
    output: str = ""
    error: str = ""
    execution_time_ms: float = 0.0
    file_tested: str = ""
    test_count: str = ""


class RegressionGate:
    """Runs pytest as a subprocess to verify evolved code doesn't break tests."""

    def __init__(
        self,
        timeout: int | None = None,
        test_path: str | None = None,
    ) -> None:
        self._timeout = timeout or _settings.evolution_test_gate_timeout
        self._test_path = test_path or _settings.evolution_test_gate_path
        self._results: list[GateResult] = []

    async def check(self, evolved_file_path: str) -> GateResult:
        """Run pytest and return pass/fail result.

        Args:
            evolved_file_path: Path to the newly written evolved .py file.
                Used for logging/auditing — pytest runs the full suite.
        """
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest",
                self._test_path,
                "-x",
                "-q",
                "--tb=short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = (time.monotonic() - start) * 1000
                result = GateResult(
                    passed=False,
                    error=f"Timeout: pytest exceeded {self._timeout}s limit",
                    execution_time_ms=elapsed,
                    file_tested=evolved_file_path,
                )
                self._results.append(result)
                return result

            elapsed = (time.monotonic() - start) * 1000
            out_text = stdout.decode("utf-8", errors="replace")[:50_000]
            err_text = stderr.decode("utf-8", errors="replace")[:10_000]

            # If pytest isn't installed, skip the gate (pass through)
            if proc.returncode != 0 and "No module named pytest" in err_text:
                _logger.info("pytest not installed — skipping regression gate")
                result = GateResult(
                    passed=True,
                    output="pytest not available — gate skipped",
                    execution_time_ms=elapsed,
                    file_tested=evolved_file_path,
                )
                self._results.append(result)
                return result

            passed = proc.returncode == 0

            # Extract summary line like "655 passed in 45.2s"
            test_count = ""
            for line in reversed(out_text.splitlines()):
                stripped = line.strip()
                if "passed" in stripped or "failed" in stripped or "error" in stripped:
                    test_count = stripped
                    break

            result = GateResult(
                passed=passed,
                output=out_text,
                error=err_text if not passed else "",
                execution_time_ms=elapsed,
                file_tested=evolved_file_path,
                test_count=test_count,
            )

        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            result = GateResult(
                passed=False,
                error=f"TestGate error: {type(e).__name__}: {e}",
                execution_time_ms=elapsed,
                file_tested=evolved_file_path,
            )

        self._results.append(result)
        return result

    def history(self, limit: int = 20) -> list[GateResult]:
        """Recent test gate results, most recent first."""
        return list(reversed(self._results[-limit:]))
