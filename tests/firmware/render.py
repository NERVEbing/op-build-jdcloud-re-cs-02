"""Render tests/firmware/profile.toml into tests/firmware/rendered.

The firmware integration test uses the rendered files; test_render.py checks that
they match the renderer. Regenerate after changing either:
    uv run --python 3.12 python tests/firmware/render.py
"""

from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "engine"))

from opbuild import config, render  # noqa: E402


def render_fixture(target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="opb-fixture-") as temp:
        root = Path(temp)
        for part in ("engine", "base", "features"):
            (root / part).symlink_to(REPO / part)
        profile = root / "profiles" / "fixture"
        profile.mkdir(parents=True)
        shutil.copy(HERE / "profile.toml", profile / "profile.toml")
        resolved = config.resolve(root, "fixture")
        if target.exists():
            shutil.rmtree(target)
        render.write_overlay(target, resolved)


if __name__ == "__main__":
    render_fixture(HERE / "rendered")
