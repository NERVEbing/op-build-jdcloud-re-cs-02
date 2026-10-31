"""Setup, compile, artifact collection and maintenance actions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import textwrap
import time

from . import kconfig, render, source
from .common import (OpError, format_duration, log, replace_once, run, run_logged, step, warn,
                     write_private)
from .config import STAGES, Resolved

STAMP = re.compile(r"\d{8}-\d{6}")
FINGERPRINT_EXCLUDED_DIRS = {"__pycache__"}


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / "workspace"
        self.src = self.dir / "src"
        self.meta = self.src / ".op"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


# --------------------------------------------------------------------- info --

def print_info(resolved: Resolved, threads: int | None = None) -> None:
    root = resolved.root
    source, target, system = resolved.source, resolved.target, resolved.system
    pinned = f", commit {source['commit']}" if source["commit"] else ""
    rows = [
        ("profile", resolved.profile),
        ("source", f"{source['url']} ({source['branch']}{pinned})"),
        ("device", f"{target['target']}/{target['subtarget']}/{target['device']}"),
        ("hostname", system["hostname"]),
        ("LAN IP", system["lan_ip"]),
        ("version", f"{system['dist']} {system['version']}"),
        ("strict config", "yes" if resolved.build["strict_config"] else "no"),
        ("features", " ".join(resolved.enabled) or "none"),
    ]
    if threads:
        rows.append(("threads", str(threads)))
    print("=== Build parameters ===")
    for label, value in rows:
        print(f"  {label:<14}: {value}")
    print("  config layers :")
    for label, _values in resolved.layers:
        print(f"    {label}")
    print("  file layers   :")
    for layer in resolved.file_layers():
        print(f"    {_relative(layer, root)}")
    print("    generated parameters, post-config hook output")
    if (resolved.profile_dir / "files").is_dir():
        print(f"    {_relative(resolved.profile_dir / 'files', root)} (last)")
    print("  hooks         :")
    for stage in STAGES:
        hooks = ", ".join(_relative(hook, root) for hook in resolved.hooks(stage)) or "-"
        print(f"    {stage}: {hooks}")
    params = resolved.document(masked=True)["params"]
    if params:
        print("  parameters    :")
        print(textwrap.indent(json.dumps(params, indent=2, ensure_ascii=False), "    "))
    sys.stdout.flush()


# -------------------------------------------------------------------- setup --

def setup(resolved: Resolved, ws: Workspace) -> None:
    print_info(resolved)
    # The shared source tree is unusable until this setup finishes.
    for marker in ("profile", "fingerprint"):
        (ws.meta / marker).unlink(missing_ok=True)
    source.prepare_source(ws.src, ws.dir, resolved.source)
    ws.meta.mkdir(exist_ok=True)
    write_private(ws.meta / "resolved.json", json.dumps(resolved.document(masked=False), indent=2) + "\n")

    records = source.prepare_external(ws.src, resolved.features, ws.meta / "staging")
    run_hooks(resolved, ws, "pre-feeds")
    records += source.update_feeds(ws.src, resolved.features)
    (ws.meta / "sources.tsv").write_text("".join("\t".join(record) + "\n" for record in records))
    run_hooks(resolved, ws, "post-feeds")

    assemble_config(resolved, ws)
    apply_branding(resolved, ws)
    assemble_files(resolved, ws)
    run_defconfig(resolved, ws)
    run_hooks(resolved, ws, "post-config")
    apply_profile_files(resolved, ws)

    (ws.meta / "profile").write_text(resolved.profile + "\n")
    (ws.meta / "fingerprint").write_text(fingerprint(resolved) + "\n")
    log("Setup complete")


def hook_environment(resolved: Resolved, ws: Workspace) -> dict[str, str]:
    # Profile parameters stay in OP_RESOLVED; exporting them would leak into make variables.
    return dict(
        os.environ,
        OP_ROOT=str(resolved.root),
        OP_SRC=str(ws.src),
        OP_PROFILE=resolved.profile,
        OP_PROFILE_DIR=str(resolved.profile_dir),
        OP_RESOLVED=str(ws.meta / "resolved.json"),
        OP_HOSTNAME=resolved.system["hostname"],
        OP_LAN_IP=resolved.system["lan_ip"],
    )


def run_hooks(resolved: Resolved, ws: Workspace, stage: str) -> None:
    env = hook_environment(resolved, ws)
    for hook in resolved.hooks(stage):
        label = _relative(hook, resolved.root)
        step(f"Running hook {label}")
        run(["bash", str(hook)], cwd=ws.src, env=env, what=f"hook {label}")


def assemble_config(resolved: Resolved, ws: Workspace) -> None:
    step("Assembling .config")
    for label, values in resolved.layers:
        log(f"  + {label} ({len(values)})")
    text = kconfig.render(resolved.requested)
    (ws.src / ".config").write_text(text)
    (ws.meta / "requested.config").write_text(text)


def apply_branding(resolved: Resolved, ws: Workspace) -> None:
    step("Applying hostname, LAN address and branding")
    system = resolved.system
    generate = ws.src / "package/base-files/files/bin/config_generate"
    replace_once(generate, r"set system\.@system\[-1\]\.hostname='[^']*'",
                 f"set system.@system[-1].hostname='{system['hostname']}'", "Set hostname")
    replace_once(generate, r'"192\.168\.1\.1"', f'"{system["lan_ip"]}"', "Set LAN IP")

    # Best effort: the default SSID only matters when the wifi feature is not enabled.
    script = ws.src / "package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc"
    pattern = re.compile(r"""(defaults\?\.ssid \|\| )(['"])[^'"\n]*\2""")
    text = script.read_text() if script.is_file() else ""
    if pattern.search(text):
        script.write_text(pattern.sub(lambda match: f"{match[1]}{match[2]}{system['dist']}{match[2]}",
                                      text, count=1))
    else:
        warn("Default SSID anchor not found in mac80211.uc; keeping the upstream default SSID")

    banner = resolved.banner()
    log(f"  banner <- {_relative(banner, resolved.root)}")
    shutil.copyfile(banner, ws.src / "package/base-files/files/etc/banner")


def assemble_files(resolved: Resolved, ws: Workspace) -> None:
    step("Assembling files overlay")
    files = ws.src / "files"
    _remove(files)
    files.mkdir()
    for layer in resolved.file_layers():
        log(f"  + {_relative(layer, resolved.root)}")
        shutil.copytree(layer, files, symlinks=True, dirs_exist_ok=True)
    for path in render.write_overlay(files, resolved):
        log(f"  + {_relative(path, files)} (generated)")


def apply_profile_files(resolved: Resolved, ws: Workspace) -> None:
    files = ws.src / "files"
    profile_files = resolved.profile_dir / "files"
    if profile_files.is_dir():
        step(f"Applying {_relative(profile_files, resolved.root)} (last layer)")
        shutil.copytree(profile_files, files, symlinks=True, dirs_exist_ok=True)
    defaults = files / "etc" / "uci-defaults"
    if defaults.is_dir():
        for script in defaults.iterdir():
            if script.is_file() and not script.is_symlink():
                script.chmod(script.stat().st_mode | 0o111)


def run_defconfig(resolved: Resolved, ws: Workspace) -> None:
    step("Applying configuration (make defconfig)")
    log_path = ws.meta / "defconfig.log"
    label = _relative(log_path, resolved.root)
    # Upstream turns Kconfig recursive dependencies into errors with this switch.
    code = run_logged(["make", "RECURSIVE_DEP_IS_ERROR=1", "defconfig"], cwd=ws.src, log_path=log_path)
    if code != 0:
        raise OpError(f"make defconfig failed (exit {code}); see {label}")
    if re.search(r"(^|[\s:])error:", log_path.read_text(errors="replace"), re.I | re.M):
        raise OpError(f"Kconfig reported an error; see {label}")

    actual = kconfig.read(ws.src / ".config")
    if actual.get("VERSION_DIST") != f'"{resolved.system["dist"]}"':
        raise OpError("defconfig dropped CONFIG_VERSION_DIST; the image would carry upstream branding")
    if actual.get(resolved.device_symbol) != "y":
        raise OpError(f"defconfig did not select CONFIG_{resolved.device_symbol}")
    if actual.get("TARGET_MULTI_PROFILE", kconfig.NOT_SET) != kconfig.NOT_SET:
        raise OpError("CONFIG_TARGET_MULTI_PROFILE must stay disabled")
    verify_feature_sources(resolved, ws)

    lost = kconfig.lost(resolved.requested, actual)
    if lost:
        warn("defconfig dropped requested values (missing upstream, renamed or unmet dependencies):")
        for item in lost:
            print(f"      {item}", file=sys.stderr)
        if resolved.build["strict_config"]:
            raise OpError("configuration does not match the request; fix the seeds, "
                          "or set build.strict_config = false in the profile")
        warn("build.strict_config is false; continuing without these values")
    log("Configuration verified")


def verify_feature_sources(resolved: Resolved, ws: Workspace) -> None:
    """Refuse packages provided by another source than the feature's own."""
    info = ws.src / "tmp" / ".packageinfo"
    if not info.is_file():
        raise OpError("tmp/.packageinfo not found after defconfig")
    origins: dict[str, list[str]] = {}
    makefile = ""
    for line in info.read_text(errors="replace").splitlines():
        if line.startswith("Source-Makefile: "):
            makefile = line.split(": ", 1)[1].strip()
        elif line.startswith("Package: "):
            origins.setdefault(line.split(": ", 1)[1].strip(), []).append(makefile)
    for feature in resolved.features:
        if feature.source is None:
            continue
        prefix = (f"package/feeds/{feature.name}/" if feature.source["type"] == "feed"
                  else f"package/{feature.packages[0]}/")
        for package in feature.packages:
            found = origins.get(package)
            if not found:
                raise OpError(f"feature {feature.name}: package {package} not found in tmp/.packageinfo")
            unexpected = [path for path in found if not path.startswith(prefix)]
            if unexpected:
                raise OpError(f"feature {feature.name}: package {package} is also provided by "
                              + ", ".join(unexpected))


def fingerprint(resolved: Resolved) -> str:
    """Hash of the build inputs as setup reads them: paths, types, executable bits and
    contents. Links are followed, except inside files/ overlays: those are copied into the
    image with links preserved, so their link targets are what the build uses."""
    root = resolved.root
    overlays = {root / "base" / "files", resolved.profile_dir / "files",
                *(feature / "files" for feature in (root / "features").iterdir())}
    digest = hashlib.sha256()

    def add(path: Path, name: str, in_overlay: bool, parents: tuple[Path, ...]) -> None:
        if path.is_symlink():
            digest.update(f"link {name} {os.readlink(path)}\n".encode())
            if in_overlay:
                return
        if path.is_dir():
            real = path.resolve()
            if real in parents:
                raise OpError(f"symlink loop in build inputs: {name}")
            digest.update(f"dir {name}\n".encode())
            for child in sorted(path.iterdir()):
                if child.name not in FINGERPRINT_EXCLUDED_DIRS and not child.name.endswith(".pyc"):
                    add(child, f"{name}/{child.name}", in_overlay or path in overlays, (*parents, real))
        elif path.is_file():
            executable = "x" if path.stat().st_mode & 0o111 else "-"
            digest.update(f"file {name} {executable} {hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                          .encode())

    for top in (root / "engine", root / "base", root / "features", resolved.profile_dir):
        add(top, _relative(top, root), False, ())
    return digest.hexdigest()


# ------------------------------------------------------------------ compile --

def _marker(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def compile_firmware(resolved: Resolved, ws: Workspace, threads: int) -> None:
    config_path = ws.src / ".config"
    prepared = _marker(ws.meta / "profile")
    if not config_path.is_file() or not prepared:
        raise OpError(f"the source tree is not set up; run: op setup {resolved.profile}")
    if prepared != resolved.profile:
        raise OpError(f"the source tree is set up for '{prepared}', not '{resolved.profile}'; "
                      f"run: op setup {resolved.profile}")
    if _marker(ws.meta / "fingerprint") != fingerprint(resolved):
        raise OpError(f"build inputs changed since setup; run: op setup {resolved.profile}")
    print_info(resolved, threads)

    kernel = re.search(r"^CONFIG_LINUX_(\d+)_(\d+)=y$", config_path.read_text(), re.M)
    commit = run(["git", "-C", str(ws.src), "show", "-s", "--date=short", "--format=%h %cd %s"],
                 capture=True, check=False).stdout.strip()
    log(f"Kernel {kernel[1] + '.' + kernel[2] if kernel else 'unknown'}; source {commit or 'unknown'}")
    started = time.time()

    step("Downloading sources")
    run(["make", "download", f"-j{threads}"], cwd=ws.src, what="make download")
    step(f"Compiling with {threads} jobs")
    purge_stale_artifacts(resolved, ws)
    if run(["make", f"-j{threads}"], cwd=ws.src, check=False).returncode != 0:
        warn("Parallel build failed; rerunning with -j1 V=s to show the error")
        run(["make", "-j1", "V=s"], cwd=ws.src, what="make -j1 V=s")

    step("Collecting artifacts")
    out = collect_artifacts(resolved, ws)
    log(f"Build complete in {format_duration(time.time() - started)}: {_relative(out, resolved.root)}")


def _bin_dir(resolved: Resolved, ws: Workspace) -> Path:
    return ws.src / "bin" / "targets" / resolved.target["target"] / resolved.target["subtarget"]


def purge_stale_artifacts(resolved: Resolved, ws: Workspace) -> None:
    """Remove top-level target files from earlier builds; package directories stay."""
    bin_dir = _bin_dir(resolved, ws)
    if not bin_dir.is_dir():
        return
    stale = [path for path in bin_dir.iterdir() if path.is_file() or path.is_symlink()]
    for path in stale:
        path.unlink()
    if stale:
        log(f"Removed {len(stale)} old artifacts from {_relative(bin_dir, ws.src)}")


def collect_artifacts(resolved: Resolved, ws: Workspace, stamp: str | None = None) -> Path:
    bin_dir = _bin_dir(resolved, ws)
    if not bin_dir.is_dir():
        raise OpError(f"artifact directory not found: {bin_dir}")
    profile, device = resolved.profile, resolved.target["device"]
    base = resolved.root / "output" / profile
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    out = base / stamp
    out.mkdir(parents=True, exist_ok=True)
    try:
        files = sorted(path for path in bin_dir.iterdir() if path.is_file())
        records = [path for path in files if path.name.endswith((".manifest", ".buildinfo"))]
        images = [path for path in files if device in path.name and path not in records]
        if not images:
            raise OpError(f"no images matching *{device}* in {bin_dir}")
        for path in images + records:
            shutil.copy2(path, out / f"{profile}-{path.name}")
        shutil.copy2(ws.src / ".config", out / f"{profile}.config")
        if (ws.meta / "sources.tsv").is_file():
            shutil.copy2(ws.meta / "sources.tsv", out / f"{profile}-sources.tsv")
    except OSError as failure:
        shutil.rmtree(out, ignore_errors=True)
        raise OpError(f"collecting artifacts: {failure}") from failure
    except OpError:
        shutil.rmtree(out, ignore_errors=True)
        raise

    pending = base / ".latest.tmp"
    pending.unlink(missing_ok=True)
    pending.symlink_to(stamp)
    os.replace(pending, base / "latest")

    # Keep this build and the newest previous one; skip symlinks and unrelated entries.
    builds = sorted(path for path in base.iterdir()
                    if path.is_dir() and not path.is_symlink() and STAMP.fullmatch(path.name))
    previous = [path for path in builds if path != out]
    for old in previous[:-1]:
        shutil.rmtree(old)
        log(f"Removed old artifacts: {_relative(old, resolved.root)}")

    log(f"Artifacts in {_relative(out, resolved.root)} ({len(images)} images):")
    for path in sorted(out.iterdir()):
        log(f"  {path.name} ({path.stat().st_size} bytes)")
    return out


# ---------------------------------------------------------------- cleaning --

def clean(ws: Workspace) -> None:
    if not ws.src.is_dir():
        raise OpError(f"source tree not found: {ws.src}")
    step("Removing build outputs")
    for name in ("bin", "build_dir", "staging_dir", "tmp", "logs"):
        _remove(ws.src / name)
    log("Clean complete; dl, ccache and feeds are kept")


def distclean(ws: Workspace) -> None:
    if not ws.src.is_dir():
        raise OpError(f"source tree not found: {ws.src}")
    step("Running make distclean")
    run(["make", "distclean"], cwd=ws.src, what="make distclean")
    _remove(ws.meta)
    log("Distclean complete; run setup before compiling")
