from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import Team
from .arena import generate_scenario
from .match import run_match
from .replay import Replay, load_replay, save_replay, verify_reconstruction
from .replay.renderer import render_arena, render_replay
from .rules import OFFICIAL_RULES
from .version import ENGINE_VERSION, RULESET_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swarmbench")
    subparsers = parser.add_subparsers(dest="command", required=True)
    arena = subparsers.add_parser("arena")
    arena.add_argument("--seed", type=int, required=True)
    arena.add_argument("--render", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("controller", type=Path)
    validate.add_argument("--backend", choices=("local", "docker"), default="local")
    match = subparsers.add_parser("match")
    match.add_argument("--controller-a", required=True)
    match.add_argument("--controller-b", required=True)
    match.add_argument("--seed", type=int, required=True)
    match.add_argument("--backend", choices=("local", "docker"), default="local")
    match.add_argument("--replay", type=Path, required=True)
    match.add_argument("--max-control-ticks", type=int, help=argparse.SUPPRESS)
    render = subparsers.add_parser("render")
    render.add_argument("replay", type=Path)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--fps", type=int, default=20)
    render.add_argument("--width", type=int, default=1200)
    render.add_argument("--playback-speed", type=float, default=1.0)
    render.add_argument("--start", type=float, default=0.0)
    render.add_argument("--duration", type=float)
    render.add_argument("--perspective", choices=("spectator", "unit"), default="spectator")
    render.add_argument("--team", choices=("A", "B"))
    render.add_argument("--unit-id", type=int)
    render.add_argument("--fov", action="store_true")
    render.add_argument("--radio", action="store_true")
    render.add_argument("--readme-preview", action="store_true")
    tournament = subparsers.add_parser("tournament")
    tournament.add_argument("--mode", choices=("official", "exhibition"), default="exhibition")
    tournament.add_argument("--size", choices=("small", "default", "large"), default="small")
    tournament.add_argument("--seed", type=int, required=True)
    tournament.add_argument("--backend", choices=("local", "docker"), default="local")
    tournament.add_argument("--max-control-ticks", type=int, help=argparse.SUPPRESS)
    return parser


def _arena_replay(seed: int) -> Replay:
    from .engine import Simulation

    scenario = generate_scenario(seed)
    simulation = Simulation(scenario)
    return Replay(
        {"engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION, "seed": seed}, OFFICIAL_RULES, scenario,
        {"A": {"id": "spawn A", "path": "", "sha256": "0" * 64}, "B": {"id": "spawn B", "path": "", "sha256": "0" * 64}},
        (), (), tuple(simulation.physics_frames), (),
        {"winner": None, "reason": "arena_preview", "final_tick": 0, "final_time": 0.0, "survivors": {"A": 8, "B": 8}, "final_state_hash": simulation.canonical_hash()},
    )


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "arena":
        render_arena(_arena_replay(arguments.seed), arguments.render)
        print(arguments.render)
        return 0
    if arguments.command == "validate":
        from .competition.submission import validate_controller_cli

        return validate_controller_cli(arguments.controller, backend=arguments.backend)
    if arguments.command == "match":
        result = run_match(arguments.controller_a, arguments.controller_b, seed=arguments.seed, backend=arguments.backend, max_control_ticks=arguments.max_control_ticks)
        save_replay(result.replay, arguments.replay)
        print(json.dumps({"winner": result.winner.value if result.winner else None, "reason": result.reason, "survivors_a": result.survivors_a, "survivors_b": result.survivors_b, "wall_time": result.wall_time, "replay": str(arguments.replay)}, sort_keys=True))
        return 0
    if arguments.command == "render":
        replay = load_replay(arguments.replay)
        verify_reconstruction(replay)
        output = render_replay(replay, arguments.output, fps=arguments.fps, width=arguments.width, playback_speed=arguments.playback_speed, start=arguments.start, duration=arguments.duration, perspective=arguments.perspective, team=arguments.team, unit_id=arguments.unit_id, fov=arguments.fov, radio=arguments.radio, readme_preview=arguments.readme_preview)
        print(output)
        return 0
    if arguments.command == "tournament":
        from .competition.tournament import tournament_cli

        return tournament_cli(seed=arguments.seed, size=arguments.size, mode=arguments.mode, backend=arguments.backend, max_control_ticks=arguments.max_control_ticks)
    raise AssertionError(arguments.command)
