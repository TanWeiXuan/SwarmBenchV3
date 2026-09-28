"""Fresh-exec unit worker. Standard output is reserved for bounded JSON lines."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import random
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from swarmbench import Action, BaseUnitController
from swarmbench.controller_runner.protocol import info_from_dict, observation_from_dict, validate_request
from swarmbench.version import PROTOCOL_VERSION


def _seed_libraries(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy  # type: ignore
    except ImportError:
        pass
    else:
        numpy.random.seed(seed % (2**32))
    if "torch" in sys.modules:
        torch = sys.modules["torch"]
        torch.manual_seed(seed)
        torch.set_num_threads(1)


def _load(path: Path) -> BaseUnitController:
    spec = importlib.util.spec_from_file_location(f"swarmbench_submission_{os.getpid()}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load controller {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    controller_type = getattr(module, "UnitController", None)
    if not isinstance(controller_type, type) or not issubclass(controller_type, BaseUnitController):
        raise TypeError("controller must define UnitController(BaseUnitController)")
    return controller_type()


def _response(sequence: int, tick: int, status: str, **values: Any) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "sequence": sequence, "tick": tick, "status": status, **values}


def main() -> int:
    protocol_out = sys.stdout
    seed = int(os.environ["SWARMBENCH_CONTROLLER_SEED"])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            _seed_libraries(seed)
            controller = _load(Path(sys.argv[1]).resolve())
    except BaseException:
        print(json.dumps(_response(0, -1, "error", error=traceback.format_exc(limit=20))), file=protocol_out, flush=True)
        return 1
    for line in sys.stdin:
        sequence, tick = -1, -1
        try:
            message = validate_request(json.loads(line))
            sequence, tick = message["sequence"], message["tick"]
            with contextlib.redirect_stdout(sys.stderr):
                if message["command"] == "initialize":
                    controller.initialize(info_from_dict(message["payload"]))
                    result: Any = None
                elif message["command"] == "step":
                    result = controller.step(observation_from_dict(message["payload"]))
                else:
                    print(json.dumps(_response(sequence, tick, "ok", result=None)), file=protocol_out, flush=True)
                    return 0
            if isinstance(result, Action) or is_dataclass(result):
                result = asdict(result)
            print(json.dumps(_response(sequence, tick, "ok", result=result), allow_nan=True), file=protocol_out, flush=True)
        except BaseException:
            print(json.dumps(_response(sequence, tick, "error", error=traceback.format_exc(limit=20))), file=protocol_out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
