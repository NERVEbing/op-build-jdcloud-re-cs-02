"""Upstream source tree, external feature sources and feeds."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from .common import OpError, log, run, step
from .config import COMMIT, Feature


def git(args: list, *, capture: bool = False, check: bool = True, what: str | None = None):
    return run(["git", *map(str, args)], capture=capture, check=check, what=what)


def prepare_source(src: Path, workspace: Path, source: dict) -> None:
    step("Preparing source")
    for cache in ("dl", "ccache"):
        (workspace / cache).mkdir(parents=True, exist_ok=True)
    url, branch, commit = source["url"], source["branch"], source["commit"]
    if (src / ".git").is_dir():
        log(f"Updating {url} ({branch})")
        git(["-C", src, "remote", "set-url", "origin", url])
        git(["-C", src, "fetch", "--depth", "1", "origin", branch], what="git fetch")
        git(["-C", src, "reset", "--hard", "FETCH_HEAD"], what="git reset")
    elif src.exists() and any(src.iterdir()):
        raise OpError(f"{src} exists but is not a git checkout")
    else:
        log(f"Cloning {url} ({branch})")
        git(["clone", "--depth", "1", "--single-branch", "--branch", branch, url, src], what="git clone")
    if commit:
        log(f"Pinning commit {commit}")
        if git(["-C", src, "fetch", "--depth", "1", "origin", commit], check=False).returncode != 0:
            git(["-C", src, "fetch", "--unshallow", "origin"], what=f"fetch commit {commit}")
        git(["-C", src, "-c", "advice.detachedHead=false", "checkout", "--detach", commit],
            what=f"checkout {commit}")
    # Untracked packages come only from enabled features; feeds.conf would shadow feeds.conf.default.
    git(["-C", src, "clean", "-ffdxq", "--", "package/"], what="git clean package/")
    if (src / "feeds.conf").exists():
        log("Removing feeds.conf; feeds.conf.default is used")
        (src / "feeds.conf").unlink()
    for name, target in (("dl", "../dl"), (".ccache", "../ccache")):
        link = src / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target)
    conf = workspace / "ccache" / "ccache.conf"
    if not conf.exists():
        conf.write_text("max_size = 20GB\n")


def prepare_external(src: Path, features: list[Feature], staging: Path) -> list[list[str]]:
    """Add feed sources and install package sources; return source records."""
    records = []
    feed_lines = []
    for feature in features:
        source = feature.source
        if source is None:
            continue
        if source["type"] == "feed":
            separator = "^" if COMMIT.fullmatch(source["ref"]) else ";"
            feed_lines.append(f"src-git {feature.name} {source['url']}{separator}{source['ref']}")
        else:
            records.append(_install_package(src, feature, staging))
    if feed_lines:
        conf = src / "feeds.conf.default"
        text = conf.read_text()
        conf.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                        + "".join(line + "\n" for line in feed_lines))
    return records


def _install_package(src: Path, feature: Feature, staging: Path) -> list[str]:
    source = feature.source
    assert source is not None
    destination = src / "package" / feature.packages[0]
    if destination.exists() or destination.is_symlink():
        raise OpError(f"feature {feature.name}: package/{feature.packages[0]} already exists upstream")
    staging.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{feature.name}-", dir=staging))
    try:
        checkout = work / "source"
        git(["init", "-q", checkout])
        git(["-C", checkout, "fetch", "-q", "--depth", "1", source["url"], source["ref"]],
            what=f"fetch {feature.name} {source['ref']}")
        git(["-C", checkout, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"])
        revision = git(["-C", checkout, "rev-parse", "HEAD"], capture=True).stdout.strip()
        package = (checkout / source["subdir"]).resolve()
        if not (package / "Makefile").is_file():
            raise OpError(f"feature {feature.name}: no Makefile in {source['subdir']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(package), str(destination))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log(f"Prepared feature {feature.name} ({revision})")
    return [feature.name, source["url"], source["ref"], revision]


def configured_feeds(src: Path) -> set[str]:
    names = set()
    for line in (src / "feeds.conf.default").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith("src-"):
            names.add(parts[1])
    return names


def update_feeds(src: Path, features: list[Feature]) -> list[list[str]]:
    step("Updating feeds")
    feeds = "./scripts/feeds"
    run([feeds, "update", "-a"], cwd=src, what="feeds update")
    configured = configured_feeds(src)
    feed_dir = src / "feeds"
    if feed_dir.is_dir():
        for entry in sorted(feed_dir.iterdir()):
            if entry.name.split(".", 1)[0] in configured:
                continue
            log(f"Removing unconfigured feed data: feeds/{entry.name}")
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    # Recreate links so packages from removed feeds cannot shadow upstream ones.
    run([feeds, "uninstall", "-a"], cwd=src, what="feeds uninstall")
    run([feeds, "install", "-a"], cwd=src, what="feeds install")
    records = []
    for feature in features:
        if feature.source and feature.source["type"] == "feed":
            revision = git(["-C", feed_dir / feature.name, "rev-parse", "HEAD"], capture=True).stdout.strip()
            records.append([feature.name, feature.source["url"], feature.source["ref"], revision])
    return records
