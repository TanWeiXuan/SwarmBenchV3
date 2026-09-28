"""Pillow/FFmpeg replay consumer; never imported by the authoritative engine."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

from swarmbench.api import Team
from swarmbench.geometry import unit_visible, visible_terrain

from .format import Replay

TEAM_COLORS = {"A": (45, 120, 235), "B": (225, 70, 70)}


def _dimensions(width: int) -> tuple[int, int, int]:
    if width < 300 or width % 2:
        raise ValueError("viewport width must be an even integer of at least 300")
    arena_height = round(width * 2 / 3)
    arena_height += arena_height % 2
    hud = max(40, round(width / 15))
    hud += hud % 2
    return width, arena_height, hud


def _background(replay: Replay, width: int, arena_height: int):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, arena_height), (226, 224, 211))
    draw = ImageDraw.Draw(image)
    sx, sy = width / replay.rules.arena_width, arena_height / replay.rules.arena_height
    for y, row in enumerate(replay.scenario.arena.rows):
        for x, value in enumerate(row):
            if value == "#":
                draw.rectangle((round(x * sx), round(arena_height - (y + 1) * sy), round((x + 1) * sx), round(arena_height - y * sy)), fill=(58, 62, 66))
    return image


def _point(position: list[float] | tuple[float, float], replay: Replay, width: int, arena_height: int) -> tuple[int, int]:
    return round(position[0] * width / replay.rules.arena_width), round(arena_height - position[1] * arena_height / replay.rules.arena_height)


def _frame_at(replay: Replay, time_value: float) -> dict[str, Any]:
    low, high = 0, len(replay.frames) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if float(replay.frames[middle]["time"]) <= time_value + 1e-9:
            low = middle
        else:
            high = middle - 1
    return replay.frames[low]


def _unit_map(frame: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(item["team"], int(item["unit_id"])): item for item in frame["units"]}


def _draw_unit(draw: Any, replay: Replay, item: dict[str, Any], width: int, arena_height: int, *, faded: bool = False, private_details: bool = True) -> None:
    position = _point(item["position"], replay, width, arena_height)
    scale = width / replay.rules.arena_width
    radius = max(4, round(replay.rules.unit_radius * scale))
    color = TEAM_COLORS[item["team"]]
    if faded:
        color = tuple((channel + 220) // 2 for channel in color)
    if not item["alive"]:
        return
    draw.ellipse((position[0] - radius, position[1] - radius, position[0] + radius, position[1] + radius), fill=color, outline=(15, 20, 25), width=max(1, radius // 3))
    heading = float(item["heading"])
    end = position[0] + math.cos(heading) * radius * 1.8, position[1] - math.sin(heading) * radius * 1.8
    draw.line((position, end), fill=(10, 10, 10), width=max(2, radius // 2))
    draw.text((position[0] + radius + 2, position[1] - radius - 2), str(item["unit_id"]), fill=(10, 10, 10))
    if private_details:
        bar_width = radius * 3
        left, top = position[0] - bar_width // 2, position[1] - radius - 7
        draw.rectangle((left, top, left + bar_width, top + 3), fill=(65, 65, 65))
        draw.rectangle((left, top, left + round(bar_width * item["health"] / replay.rules.max_health), top + 3), fill=(55, 195, 80))
        if item.get("reload_complete_tick") is not None:
            draw.arc((position[0] - radius - 3, position[1] - radius - 3, position[0] + radius + 3, position[1] + radius + 3), 20, 300, fill=(250, 190, 30), width=2)


def _events_near(replay: Replay, time_value: float, kind: str, duration: float) -> list[dict[str, Any]]:
    return [event for event in replay.events if event.get("type") == kind and 0 <= time_value - event.get("tick", 0) * replay.rules.control_dt <= duration]


def _draw_spectator(base: Any, replay: Replay, frame: dict[str, Any], time_value: float, width: int, arena_height: int, *, fov: bool, radio: bool):
    from PIL import ImageDraw

    image = base.copy()
    draw = ImageDraw.Draw(image, "RGBA")
    for item in frame["units"]:
        if fov and item["alive"]:
            centre = _point(item["position"], replay, width, arena_height)
            radius = replay.rules.visual_range * width / replay.rules.arena_width
            start = -math.degrees(item["heading"] + replay.rules.field_of_view / 2)
            end = -math.degrees(item["heading"] - replay.rules.field_of_view / 2)
            draw.pieslice((centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius), start, end, fill=(*TEAM_COLORS[item["team"]], 20))
    for event in _events_near(replay, time_value, "shot", 0.18):
        color = (255, 40, 20, 235) if event.get("friendly_fire") else (255, 225, 60, 230)
        draw.line((_point(event["origin"], replay, width, arena_height), _point(event["endpoint"], replay, width, arena_height)), fill=color, width=max(2, width // 400))
    if radio:
        units = _unit_map(frame)
        for event in _events_near(replay, time_value, "radio_transmit", 0.12):
            sender = units.get((event["team"], int(event["unit_id"])))
            if sender is None:
                continue
            for recipient in event.get("recipients", []):
                receiver = units.get((event["team"], int(recipient)))
                if receiver:
                    draw.line((_point(sender["position"], replay, width, arena_height), _point(receiver["position"], replay, width, arena_height)), fill=(90, 230, 255, 100), width=2)
    for item in frame["units"]:
        _draw_unit(draw, replay, item, width, arena_height)
    for event in _events_near(replay, time_value, "death", 0.7):
        point = _point(event["position"], replay, width, arena_height)
        draw.line((point[0] - 7, point[1] - 7, point[0] + 7, point[1] + 7), fill=(255, 100, 20, 220), width=3)
        draw.line((point[0] - 7, point[1] + 7, point[0] + 7, point[1] - 7), fill=(255, 100, 20, 220), width=3)
    return image


def _draw_unit_view(base: Any, replay: Replay, frame: dict[str, Any], time_value: float, width: int, arena_height: int, team: Team, unit_id: int, known: dict[tuple[int, int], bool], *, fov: bool):
    from PIL import Image, ImageDraw

    blank = Image.new("RGB", (width, arena_height), (22, 25, 28))
    units = _unit_map(frame)
    selected = units.get((team.value, unit_id))
    if selected is None:
        raise ValueError("selected unit is absent")
    draw = ImageDraw.Draw(blank, "RGBA")
    if selected["alive"]:
        current = visible_terrain(replay.scenario.arena, tuple(selected["position"]), float(selected["heading"]), replay.rules.visual_range, replay.rules.field_of_view, replay.rules.close_awareness)
        for x, y, blocked in current:
            known[(x, y)] = blocked
    else:
        current = ()
    current_cells = {(x, y) for x, y, _ in current}
    sx, sy = width / replay.rules.arena_width, arena_height / replay.rules.arena_height
    for (x, y), blocked in known.items():
        active = (x, y) in current_cells
        color = (70, 74, 76) if blocked else (155, 155, 145)
        if not active:
            color = tuple(channel // 2 for channel in color)
        draw.rectangle((round(x * sx), round(arena_height - (y + 1) * sy), round((x + 1) * sx), round(arena_height - y * sy)), fill=color)
    if fov and selected["alive"]:
        centre = _point(selected["position"], replay, width, arena_height)
        draw.line((centre, _point((selected["position"][0] + math.cos(selected["heading"] - replay.rules.field_of_view / 2) * replay.rules.visual_range, selected["position"][1] + math.sin(selected["heading"] - replay.rules.field_of_view / 2) * replay.rules.visual_range), replay, width, arena_height)), fill=(240, 240, 120, 100), width=2)
        draw.line((centre, _point((selected["position"][0] + math.cos(selected["heading"] + replay.rules.field_of_view / 2) * replay.rules.visual_range, selected["position"][1] + math.sin(selected["heading"] + replay.rules.field_of_view / 2) * replay.rules.visual_range), replay, width, arena_height)), fill=(240, 240, 120, 100), width=2)
    if selected["alive"]:
        for item in frame["units"]:
            if not item["alive"] or item is selected:
                continue
            if unit_visible(replay.scenario.arena, tuple(selected["position"]), float(selected["heading"]), tuple(item["position"]), replay.rules.visual_range, replay.rules.field_of_view, replay.rules.close_awareness):
                _draw_unit(draw, replay, item, width, arena_height, private_details=False)
    _draw_unit(draw, replay, selected, width, arena_height)
    return blank


def _radio_for_unit(replay: Replay, time_value: float, team: Team, unit_id: int) -> list[tuple[int, int]]:
    tick = math.floor(time_value * replay.rules.controller_hz + 1e-9)
    received = [
        (int(event["sender_id"]), int(event["payload"]))
        for event in replay.events
        if event.get("type") == "radio_deliver" and event.get("team") == team.value and event.get("tick") == tick and event.get("unit_id") == unit_id
    ]
    return sorted(received)


def _compose(replay: Replay, frame: dict[str, Any], time_value: float, width: int, arena_height: int, hud_height: int, *, perspective: str, team: Team | None, unit_id: int | None, known: dict[tuple[int, int], bool], fov: bool, radio: bool):
    from PIL import Image, ImageDraw

    base = _background(replay, width, arena_height)
    if perspective == "spectator":
        arena_image = _draw_spectator(base, replay, frame, time_value, width, arena_height, fov=fov, radio=radio)
    else:
        if team is None or unit_id is None:
            raise ValueError("unit perspective requires --team and --unit-id")
        observation_time = math.floor(time_value * replay.rules.controller_hz + 1e-9) / replay.rules.controller_hz
        frame = _frame_at(replay, observation_time)
        arena_image = _draw_unit_view(base, replay, frame, observation_time, width, arena_height, team, unit_id, known, fov=fov)
    canvas = Image.new("RGB", (width, arena_height + hud_height), (15, 18, 22))
    canvas.paste(arena_image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    controllers = replay.controllers
    draw.text((12, arena_height + 9), f"{controllers['A']['id']}  vs  {controllers['B']['id']}    {time_value:06.1f}s", fill=(240, 240, 240))
    if perspective == "spectator":
        units = frame["units"]
        counts = {name: sum(item["alive"] and item["team"] == name for item in units) for name in ("A", "B")}
        draw.text((12, arena_height + 30), f"Living: A {counts['A']} / B {counts['B']}", fill=(210, 210, 210))
    else:
        selected = _unit_map(frame)[(team.value, unit_id)]
        state = "ALIVE" if selected["alive"] else "LOCAL VIEW ENDED — UNIT ELIMINATED"
        draw.text((12, arena_height + 30), f"Unit {team.value}{unit_id}: {state}  HP {selected['health']}  ammo {selected['ammunition']}", fill=(210, 210, 210))
        messages = _radio_for_unit(replay, time_value, team, unit_id)
        if messages:
            raw = "  ".join(f"from {sender}: 0x{payload:016x}" for sender, payload in messages)
            draw.text((12, arena_height + 50), f"Raw radio: {raw[:150]}", fill=(130, 220, 245))
    if time_value >= float(replay.result["final_time"]) - 1e-9:
        winner = replay.result.get("winner") or "DRAW"
        draw.text((width - 270, arena_height + 9), f"FINAL: {winner} — {replay.result['reason']}", fill=(255, 215, 80))
    return canvas


def _times(replay: Replay, fps: int, speed: float, start: float, duration: float | None, *, unit_end: float | None = None) -> Iterator[float]:
    end = float(replay.result["final_time"])
    if unit_end is not None:
        end = min(end, unit_end)
    if duration is not None:
        end = min(end, start + duration)
    count = max(1, math.floor(max(0.0, end - start) * fps / speed) + 1)
    for index in range(count):
        yield min(end, start + index * speed / fps)


def _unit_death_time(replay: Replay, team: Team, unit_id: int) -> float | None:
    for event in replay.events:
        if event.get("type") == "death" and event.get("team") == team.value and event.get("unit_id") == unit_id:
            return event["tick"] * replay.rules.control_dt
    return None


def render_replay(
    replay: Replay,
    output: str | Path,
    *,
    fps: int = 20,
    width: int = 1200,
    playback_speed: float = 1.0,
    start: float = 0.0,
    duration: float | None = None,
    perspective: str = "spectator",
    team: Team | str | None = None,
    unit_id: int | None = None,
    fov: bool = False,
    radio: bool = False,
    readme_preview: bool = False,
) -> Path:
    if perspective not in {"spectator", "unit"} or fps <= 0 or playback_speed <= 0 or start < 0:
        raise ValueError("invalid rendering options")
    selected_team = Team(team) if team is not None else None
    if perspective == "unit" and (selected_team is None or unit_id is None or not 0 <= unit_id < replay.rules.units_per_team):
        raise ValueError("unit perspective requires a valid team and unit ID")
    destination = Path(output)
    suffix = destination.suffix.lower()
    if suffix not in {".mp4", ".gif", ".png"}:
        raise ValueError("output must be .mp4, .gif, or .png")
    if suffix == ".gif" and (not readme_preview or duration is None or not 10 <= duration <= 15 or fps != 10 or width > 650):
        raise ValueError("GIF is limited to --readme-preview, 10 FPS, 10–15 seconds, and width <= 650")
    viewport_width, arena_height, hud_height = _dimensions(width)
    destination.parent.mkdir(parents=True, exist_ok=True)
    death_time = _unit_death_time(replay, selected_team, unit_id) if perspective == "unit" else None
    timeline = list(_times(replay, fps, playback_speed, start, duration, unit_end=death_time))
    known: dict[tuple[int, int], bool] = {}

    def images() -> Iterator[Any]:
        for time_value in timeline:
            yield _compose(replay, _frame_at(replay, time_value), time_value, viewport_width, arena_height, hud_height, perspective=perspective, team=selected_team, unit_id=unit_id, known=known, fov=fov, radio=radio)

    if suffix == ".png":
        image = next(images()) if duration == 0 else _compose(replay, _frame_at(replay, timeline[-1]), timeline[-1], viewport_width, arena_height, hud_height, perspective=perspective, team=selected_team, unit_id=unit_id, known=known, fov=fov, radio=radio)
        image.save(destination)
        return destination
    if suffix == ".gif":
        frames = [image.quantize(colors=128) for image in images()]
        temporary = destination.with_name(destination.stem + ".part.gif")
        frames[0].save(temporary, save_all=True, append_images=frames[1:], duration=1000 // fps, loop=0, optimize=True, disposal=2)
        os.replace(temporary, destination)
        return destination
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for MP4 output; install FFmpeg with the libx264 encoder")
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], text=True, capture_output=True)
    if encoders.returncode or "libx264" not in encoders.stdout:
        raise RuntimeError("FFmpeg is installed but libx264 is unavailable; install an FFmpeg build with H.264/libx264")
    temporary = destination.with_name(destination.stem + ".part.mp4")
    size = f"{viewport_width}x{arena_height + hud_height}"
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([
            ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", size,
            "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
        ], stdin=subprocess.PIPE, stderr=stderr)
        try:
            assert process.stdin is not None
            last = None
            for last in images():
                process.stdin.write(last.tobytes())
            process.stdin.close()
            code = process.wait()
            if code:
                stderr.seek(0)
                raise RuntimeError(f"FFmpeg failed ({code}): {stderr.read(8192).decode(errors='replace')}")
        except BaseException:
            process.kill()
            process.wait()
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, destination)
    poster = destination.with_suffix(".png")
    last_time = timeline[-1]
    _compose(replay, _frame_at(replay, last_time), last_time, viewport_width, arena_height, hud_height, perspective=perspective, team=selected_team, unit_id=unit_id, known=known, fov=fov, radio=radio).save(poster)
    return destination


def render_arena(replay: Replay, output: str | Path, *, width: int = 1200) -> Path:
    destination = Path(output)
    viewport_width, arena_height, _ = _dimensions(width)
    image = _background(replay, viewport_width, arena_height)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    for team, points in (("A", replay.scenario.spawns_a), ("B", replay.scenario.spawns_b)):
        for unit_id, point in enumerate(points):
            pixel = _point(point, replay, viewport_width, arena_height)
            draw.ellipse((pixel[0] - 4, pixel[1] - 4, pixel[0] + 4, pixel[1] + 4), fill=TEAM_COLORS[team])
            draw.text((pixel[0] + 5, pixel[1] - 5), str(unit_id), fill=(10, 10, 10))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return destination
