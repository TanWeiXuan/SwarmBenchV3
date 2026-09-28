from pathlib import Path

BASELINE_NAMES = ("rush", "spread_rush", "radio_rush")


def baseline_path(name: str) -> Path:
    if name not in BASELINE_NAMES:
        raise ValueError(f"unknown baseline: {name}")
    return Path(__file__).with_name(f"{name}.py")
