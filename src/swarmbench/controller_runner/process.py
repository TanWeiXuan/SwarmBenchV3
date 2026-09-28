"""One persistent, independently executed process/container per unit."""

from __future__ import annotations

import json
import os
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swarmbench.api import Observation, UnitInfo
from swarmbench.rules import OFFICIAL_RULES, Rules

from .protocol import info_to_dict, observation_to_dict, request, validate_response


class ControllerError(RuntimeError):
    pass


class ControllerTimeout(ControllerError):
    pass


class ControllerInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StepResult:
    action: Any
    elapsed: float
    missed_soft_deadline: bool = False


@dataclass(slots=True)
class ControllerStats:
    step_times: list[float] = field(default_factory=list)
    soft_misses: int = 0
    hard_failures: int = 0

    def summary(self) -> dict[str, float | int]:
        values = sorted(self.step_times)
        percentile = lambda fraction: values[min(len(values) - 1, int((len(values) - 1) * fraction))] if values else 0.0
        return {
            "mean": statistics.fmean(values) if values else 0.0,
            "p95": percentile(0.95),
            "max": max(values, default=0.0),
            "soft_misses": self.soft_misses,
            "hard_failures": self.hard_failures,
        }


def docker_run_command(image: str, path: Path, seed: int, rules: Rules) -> list[str]:
    return [
        "docker", "run", "--rm", "-i", "--network", "none", "--ipc", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--memory", f"{rules.worker_memory_mib}m", "--memory-swap", f"{rules.worker_memory_mib}m",
        "--pids-limit", str(rules.worker_process_limit), "--cpus", "1.0",
        "--tmpfs", f"/scratch:rw,noexec,nosuid,size={rules.worker_scratch_mib}m,mode=700,uid=65534,gid=65534",
        "--workdir", "/scratch",
        "--mount", f"type=bind,src={path},dst=/controller/controller.py,readonly",
        "--env", f"SWARMBENCH_CONTROLLER_SEED={seed}", "--env", "HOME=/scratch",
        "--env", "OMP_NUM_THREADS=1", "--env", "MKL_NUM_THREADS=1", "--env", "OPENBLAS_NUM_THREADS=1",
        image, "/controller/controller.py",
    ]


class ControllerProcess:
    def __init__(self, path: str | Path, *, backend: str = "local", image: str = "swarmbench-v3-controller", rules: Rules = OFFICIAL_RULES) -> None:
        if backend not in {"local", "docker"}:
            raise ValueError("backend must be local or docker")
        self.path = Path(path).resolve()
        self.backend, self.image, self.rules = backend, image, rules
        self.stats = ControllerStats()
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._logs: deque[str] = deque()
        self._log_size = 0
        self._sequence = 0
        self._scratch: tempfile.TemporaryDirectory[str] | None = None

    @property
    def logs(self) -> str:
        return "".join(self._logs)

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _environment(self, seed: int) -> dict[str, str]:
        keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USER", "USERNAME"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
        environment.update({
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONHASHSEED": str(seed % 2**32),
            "SWARMBENCH_CONTROLLER_SEED": str(seed),
            "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "CUDA_VISIBLE_DEVICES": "", "HOME": "",
        })
        return environment

    def _read_stdout(self, stream: Any) -> None:
        try:
            while True:
                line = stream.readline(self.rules.max_protocol_bytes + 2)
                if not line:
                    break
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _read_stderr(self, stream: Any) -> None:
        while True:
            line = stream.readline(2049)
            if not line:
                break
            encoded = line[:2048]
            self._logs.append(encoded)
            self._log_size += len(encoded.encode(errors="replace"))
            while self._log_size > self.rules.max_log_bytes and self._logs:
                self._log_size -= len(self._logs.popleft().encode(errors="replace"))

    def start(self, seed: int) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._scratch = tempfile.TemporaryDirectory(prefix="swarmbench-v3-unit-") if self.backend == "local" else None
        command = [sys.executable, "-u", "-m", "swarmbench.controller_runner.worker", str(self.path)] if self.backend == "local" else docker_run_command(self.image, self.path, seed, self.rules)
        try:
            self._process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", cwd=self._scratch.name if self._scratch else None,
                env=self._environment(seed) if self.backend == "local" else None,
            )
        except OSError as error:
            raise ControllerInfrastructureError(f"failed to start {self.backend} worker: {error}") from error
        threading.Thread(target=self._read_stdout, args=(self._process.stdout,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self._process.stderr,), daemon=True).start()

    def _call(self, command: str, tick: int, payload: dict[str, Any], timeout: float) -> tuple[Any, float]:
        if not self.alive or self._process is None or self._process.stdin is None:
            raise ControllerError("worker is not running")
        sequence = self._sequence
        self._sequence += 1
        encoded = json.dumps(request(sequence, tick, command, payload), allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) > self.rules.max_protocol_bytes:
            raise ControllerInfrastructureError("trusted observation exceeds protocol limit")
        started = time.perf_counter()
        try:
            self._process.stdin.write(encoded + "\n")
            self._process.stdin.flush()
            line = self._responses.get(timeout=timeout)
        except (BrokenPipeError, queue.Empty) as error:
            self.stats.hard_failures += 1
            self.terminate()
            raise ControllerTimeout(f"worker failed to reply within {timeout:.3f}s") from error
        elapsed = time.perf_counter() - started
        if line is None:
            if self.backend == "docker":
                raise ControllerInfrastructureError(f"Docker failed to launch the worker: {self.logs[-2000:]}")
            self.stats.hard_failures += 1
            raise ControllerError(f"worker exited: {self.logs[-2000:]}")
        if len(line.encode()) > self.rules.max_protocol_bytes:
            self.terminate()
            raise ControllerError("response exceeds protocol limit")
        try:
            response = validate_response(json.loads(line), sequence, tick)
        except (json.JSONDecodeError, RuntimeError) as error:
            self.terminate()
            raise ControllerError(str(error)) from error
        if response["status"] == "error":
            self.stats.hard_failures += 1
            raise ControllerError(str(response.get("error", "controller exception")))
        return response.get("result"), elapsed

    def initialize(self, info: UnitInfo) -> None:
        self.start(info.controller_seed)
        self._call("initialize", -1, info_to_dict(info), self.rules.initialization_deadline)

    def step(self, observation: Observation) -> StepResult:
        result, elapsed = self._call("step", observation.tick, observation_to_dict(observation), self.rules.hard_step_deadline)
        self.stats.step_times.append(elapsed)
        missed = elapsed > self.rules.soft_step_deadline
        if missed:
            self.stats.soft_misses += 1
        return StepResult(None if missed else result, elapsed, missed)

    def terminate(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None

    def close(self) -> None:
        if self.alive:
            try:
                self._call("shutdown", -1, {}, 1.0)
            except ControllerError:
                pass
        self.terminate()


def step_all(processes: dict[Any, ControllerProcess], observations: dict[Any, Observation]) -> tuple[dict[Any, StepResult], dict[Any, BaseException]]:
    """Collect one barrier concurrently; failures are returned together for fair forfeits."""
    results: dict[Any, StepResult] = {}
    errors: dict[Any, BaseException] = {}
    with ThreadPoolExecutor(max_workers=len(observations), thread_name_prefix="swarmbench-unit-step") as executor:
        futures = {executor.submit(processes[key].step, observation): key for key, observation in observations.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except BaseException as error:
                errors[key] = error
    return results, errors
