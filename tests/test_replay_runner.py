import shutil
import subprocess
from pathlib import Path

import pytest

from swarmbench.match import run_match
from swarmbench.controller_runner.process import docker_run_command
from swarmbench.rules import OFFICIAL_RULES
from swarmbench.replay import load_replay, save_replay, verify_reconstruction
from swarmbench.replay.renderer import render_replay


def test_sixteen_workers_have_private_module_state(tmp_path: Path) -> None:
    controller = tmp_path / "controller.py"
    controller.write_text(
        "from swarmbench import *\ncount=0\nclass UnitController(BaseUnitController):\n"
        " def initialize(self,info):\n  global count\n  count+=1\n  self.count=count\n"
        " def step(self,o): return Action(broadcast=self.count)\n",
        encoding="utf-8",
    )
    result = run_match(controller, controller, seed=4, max_control_ticks=1)
    values = [action["broadcast"] for action in result.replay.accepted_actions[0]["units"].values()]
    assert values == [1] * 16


def test_replay_is_deterministic_validated_and_reconstructs(tmp_path: Path) -> None:
    result = run_match("rush", "radio_rush", seed=42, max_control_ticks=4)
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    save_replay(result.replay, first)
    save_replay(result.replay, second)
    assert first.read_bytes() == second.read_bytes()
    loaded = load_replay(first)
    verify_reconstruction(loaded)
    assert loaded.result["final_state_hash"] == result.replay.result["final_state_hash"]


def test_controller_exceptions_forfeit_fairly_at_one_barrier(tmp_path: Path) -> None:
    controller = tmp_path / "broken.py"
    controller.write_text("from swarmbench import *\nclass UnitController(BaseUnitController):\n def step(self,o): raise RuntimeError('boom')\n", encoding="utf-8")
    one = run_match(controller, "rush", seed=5, max_control_ticks=1)
    both = run_match(controller, controller, seed=5, max_control_ticks=1)
    assert one.winner.value == "B" and one.reason == "controller_forfeit"
    assert both.winner is None and both.reason == "double_controller_forfeit"


def test_docker_command_enforces_per_unit_limits(tmp_path: Path) -> None:
    path = tmp_path / "controller.py"
    path.write_text("# placeholder", encoding="utf-8")
    command = docker_run_command("image", path, 1, OFFICIAL_RULES)
    joined = " ".join(command)
    for value in ("--network none", "--ipc none", "--read-only", "--cap-drop ALL", "no-new-privileges", "--memory 256m", "--tmpfs /scratch:rw,noexec,nosuid,size=16m", "readonly"):
        assert value in joined


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg tools unavailable")
def test_mp4_and_unit_view_smoke(tmp_path: Path) -> None:
    replay = run_match("rush", "radio_rush", seed=9, max_control_ticks=3).replay
    output = render_replay(replay, tmp_path / "match.mp4", width=600)
    render_replay(replay, tmp_path / "unit.mp4", width=600, perspective="unit", team="A", unit_id=3)
    data = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt,width,height", "-of", "json", str(output)], text=True, capture_output=True, check=True).stdout
    assert '"codec_name": "h264"' in data and '"pix_fmt": "yuv420p"' in data
    assert output.with_suffix(".png").is_file()


def test_gif_requires_bounded_readme_preview(tmp_path: Path) -> None:
    replay = run_match("rush", "rush", seed=1, max_control_ticks=2).replay
    with pytest.raises(ValueError, match="readme-preview"):
        render_replay(replay, tmp_path / "bad.gif", width=600, fps=10, duration=12)


def test_explicit_mp4_fails_when_ffmpeg_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = run_match("rush", "rush", seed=2, max_control_ticks=1).replay
    monkeypatch.setattr("swarmbench.replay.renderer.shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="FFmpeg"):
        render_replay(replay, tmp_path / "missing.mp4", width=600)
