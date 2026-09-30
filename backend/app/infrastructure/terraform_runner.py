"""Thin wrapper around the terraform/tofu CLI.

* Runs with a minimal environment: the control plane's own secrets (database URL,
  SECRET_KEY, ...) are never passed to Terraform or its providers.
* Uses ``-json`` machine-readable output for plan/apply/destroy so progress and errors
  can be reported structurally.
* Enforces a timeout, interrupting Terraform gracefully (SIGINT) before killing it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.infrastructure.logging import get_logger

log = get_logger(__name__)

_PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "TMPDIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_CONFIG",
)
EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class TerraformResult:
    args: list[str]
    returncode: int
    duration_seconds: float
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    output_tail: list[str] = field(default_factory=list)
    stdout: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class TerraformTimeout(Exception):
    pass


class TerraformRunner:
    def __init__(self, binary: str, *, timeout_seconds: int = 3600, plugin_cache_dir: str = "") -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.plugin_cache_dir = plugin_cache_dir

    @property
    def is_opentofu(self) -> bool:
        return Path(self.binary).name.startswith("tofu")

    def _env(self, extra: dict[str, str]) -> dict[str, str]:
        env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
        env.update(
            {
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
                "CHECKPOINT_DISABLE": "1",
                "TF_CLI_ARGS": "-no-color",
            }
        )
        if self.plugin_cache_dir:
            Path(self.plugin_cache_dir).mkdir(parents=True, exist_ok=True)
            env["TF_PLUGIN_CACHE_DIR"] = self.plugin_cache_dir
        env.update(extra)
        return env

    def run(
        self,
        workdir: Path,
        args: list[str],
        env: dict[str, str],
        *,
        json_events: bool = False,
        on_event: EventCallback | None = None,
        capture_stdout: bool = False,
        timeout: float | None = None,
    ) -> TerraformResult:
        cmd = [self.binary, *args]
        started = time.monotonic()
        tail: deque[str] = deque(maxlen=200)
        diagnostics: list[dict[str, Any]] = []
        chunks: list[str] = []
        proc = subprocess.Popen(  # noqa: S603 - fixed binary, argument list, no shell
            cmd,
            cwd=workdir,
            env=self._env(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                if capture_stdout:
                    chunks.append(line)
                stripped = line.rstrip("\n")
                if not json_events:
                    tail.append(stripped)
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    tail.append(stripped)
                    continue
                message = event.get("@message")
                if message:
                    tail.append(str(message))
                if event.get("type") == "diagnostic" and isinstance(event.get("diagnostic"), dict):
                    diagnostics.append(event["diagnostic"])
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:  # progress reporting must never break Terraform
                        log.exception("terraform_event_callback_failed")

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        limit = timeout or self.timeout_seconds
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired as exc:
            log.error("terraform_timeout", args=args, timeout_seconds=limit)
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            raise TerraformTimeout(f"terraform {args[0]} timed out after {int(limit)}s") from exc
        finally:
            reader.join(timeout=10)

        result = TerraformResult(
            args=args,
            returncode=proc.returncode,
            duration_seconds=round(time.monotonic() - started, 2),
            diagnostics=diagnostics,
            output_tail=list(tail),
            stdout="".join(chunks),
        )
        log.info(
            "terraform_command_finished",
            command=args[0],
            returncode=result.returncode,
            duration_seconds=result.duration_seconds,
        )
        if not result.ok and not diagnostics:
            # Non-JSON commands (init) report errors as text; keep them as a diagnostic.
            text = "\n".join(line for line in tail if line.strip())
            result.diagnostics.append({"severity": "error", "summary": text[-2000:], "detail": ""})
        return result

    def init(self, workdir: Path, env: dict[str, str]) -> TerraformResult:
        return self.run(workdir, ["init", "-input=false", "-lock-timeout=60s"], env)

    def plan(
        self,
        workdir: Path,
        env: dict[str, str],
        plan_file: str,
        *,
        destroy: bool = False,
        on_event: EventCallback | None = None,
    ) -> TerraformResult:
        args = ["plan", "-input=false", "-lock-timeout=60s", "-json", f"-out={plan_file}"]
        if destroy:
            args.append("-destroy")
        return self.run(workdir, args, env, json_events=True, on_event=on_event)

    def show_plan(self, workdir: Path, env: dict[str, str], plan_file: str) -> dict[str, Any]:
        result = self.run(workdir, ["show", "-json", plan_file], env, capture_stdout=True)
        if not result.ok:
            raise RuntimeError(f"terraform show failed: {' '.join(result.output_tail[-5:])}")
        return json.loads(result.stdout)

    def apply(
        self, workdir: Path, env: dict[str, str], plan_file: str, *, on_event: EventCallback | None = None
    ) -> TerraformResult:
        args = ["apply", "-input=false", "-lock-timeout=60s", "-json", plan_file]
        return self.run(workdir, args, env, json_events=True, on_event=on_event)

    def output(self, workdir: Path, env: dict[str, str]) -> dict[str, Any]:
        result = self.run(workdir, ["output", "-json"], env, capture_stdout=True)
        if not result.ok:
            raise RuntimeError(f"terraform output failed: {' '.join(result.output_tail[-5:])}")
        raw = json.loads(result.stdout or "{}")
        return {k: v.get("value") for k, v in raw.items()}
