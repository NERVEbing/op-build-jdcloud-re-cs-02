"""Resolve base settings, the feature catalog and a local profile."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import ipaddress
from pathlib import Path
import re
import tomllib
from typing import Any, Callable

from . import kconfig, schema, wifi
from .common import OpError

STAGES = ("pre-feeds", "post-feeds", "post-config")
FEATURE_NAME = re.compile(r"[a-z][a-z0-9-]*")
FEED_NAME = re.compile(r"[a-z][a-z0-9_]*")
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
COMMIT = re.compile(r"[0-9a-f]{40}")
REF = re.compile(r"[A-Za-z0-9._/-]+")

SOURCE_SPEC = {
    "url": {"type": "string", "min": 1},
    "branch": {"type": "string", "pattern": r"[A-Za-z0-9._/-]+"},
    "commit": {"type": "string", "pattern": r"([0-9a-f]{40})?"},
}
TARGET_SPEC = {
    "target": {"type": "string", "pattern": r"[a-z0-9]+"},
    "subtarget": {"type": "string", "pattern": r"[a-z0-9]+"},
    "device": {"type": "string", "pattern": r"[a-z0-9_-]+"},
}
SYSTEM_SPEC = {
    "hostname": {"type": "string", "pattern": r"[A-Za-z0-9][A-Za-z0-9-]{0,62}"},
    "lan_ip": {"type": "ipv4"},
    "dist": {"type": "string", "pattern": r"[A-Za-z0-9._-]{1,32}"},
    "version": {"type": "string", "pattern": r"[A-Za-z0-9._-]{1,32}"},
}
BUILD_SPEC = {"strict_config": {"type": "boolean"}}
# Cross-field checks for features whose parameters the schema cannot fully describe.
CHECKS: dict[str, Callable[[dict, str], None]] = {"wifi": wifi.check}


@dataclass(frozen=True)
class Feature:
    name: str
    path: Path
    description: str
    packages: tuple[str, ...]
    source: dict | None
    params: dict

    def hook(self, stage: str) -> Path:
        return self.path / "hooks" / f"{stage}.sh"


@dataclass
class Resolved:
    root: Path
    profile: str
    profile_dir: Path
    source: dict
    target: dict
    system: dict
    build: dict
    enabled: list[str]
    catalog: dict[str, Feature]
    params: dict[str, dict]
    layers: list[tuple[str, dict[str, str]]]
    requested: dict[str, str]
    warnings: list[str]

    @property
    def features(self) -> list[Feature]:
        return [self.catalog[name] for name in self.enabled]

    @property
    def device_symbol(self) -> str:
        t = self.target
        return f"TARGET_{t['target']}_{t['subtarget']}_DEVICE_{t['device']}"

    def hooks(self, stage: str) -> list[Path]:
        candidates = [self.root / "base" / "hooks" / f"{stage}.sh"]
        candidates += [feature.hook(stage) for feature in self.features]
        candidates.append(self.profile_dir / "hooks" / f"{stage}.sh")
        return [path for path in candidates if path.is_file()]

    def file_layers(self) -> list[Path]:
        """Overlay directories applied before rendering; profile files are applied last."""
        layers = [self.root / "base" / "files"] + [feature.path / "files" for feature in self.features]
        return [path for path in layers if path.is_dir()]

    def banner(self) -> Path:
        custom = self.profile_dir / "banner"
        return custom if custom.is_file() else self.root / "base" / "banner"

    def document(self, masked: bool) -> dict[str, Any]:
        params = {
            name: schema.mask(self.catalog[name].params, values) if masked else values
            for name, values in self.params.items()
        }
        return {
            "profile": self.profile,
            "source": self.source,
            "target": self.target,
            "system": self.system,
            "build": self.build,
            "features": self.enabled,
            "params": params,
        }


def _load_toml(path: Path, label: str) -> dict:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise OpError(f"{label}: {error}") from error


def _required(specs: dict, optional: tuple[str, ...] = ()) -> dict:
    return {name: {**spec, "required": True} if name not in optional else spec
            for name, spec in specs.items()}


def load_base(root: Path) -> dict:
    data = _load_toml(root / "base" / "base.toml", "base/base.toml")
    tables = {
        "source": _required(SOURCE_SPEC),
        "target": _required(TARGET_SPEC),
        "system": _required(SYSTEM_SPEC, optional=("version",)),
        "build": _required(BUILD_SPEC),
    }
    unknown = sorted(set(data) - set(tables))
    if unknown:
        raise OpError(f"base/base.toml: unknown table(s): {', '.join(unknown)}")
    return {name: schema.apply(specs, data.get(name, {}), name) for name, specs in tables.items()}


def load_catalog(root: Path) -> dict[str, Feature]:
    catalog: dict[str, Feature] = {}
    owners: dict[str, str] = {}
    for manifest in sorted((root / "features").glob("*/feature.toml")):
        name = manifest.parent.name
        where = f"features/{name}/feature.toml"
        if not FEATURE_NAME.fullmatch(name):
            raise OpError(f"{where}: feature names must match {FEATURE_NAME.pattern}")
        data = _load_toml(manifest, where)
        unknown = sorted(set(data) - {"description", "packages", "source", "params"})
        if unknown:
            raise OpError(f"{where}: unknown key(s): {', '.join(unknown)}")
        description = data.get("description")
        if not isinstance(description, str) or not description or "\n" in description:
            raise OpError(f"{where}: description must be a single-line string")
        packages = data.get("packages", [])
        if not isinstance(packages, list) or not all(
                isinstance(pkg, str) and PACKAGE_NAME.fullmatch(pkg) for pkg in packages):
            raise OpError(f"{where}: packages must be a list of package names")
        for pkg in packages:
            if pkg in owners:
                raise OpError(f"{where}: package {pkg} is already owned by feature {owners[pkg]}")
            owners[pkg] = name
        source = _check_source(data.get("source"), name, packages, where)
        params = data.get("params", {})
        if not isinstance(params, dict):
            raise OpError(f"{where}: params must be a table")
        for param, spec in params.items():
            schema.check_spec(spec, f"{where}: params.{param}")
        catalog[name] = Feature(name, manifest.parent, description, tuple(packages), source, params)
    return catalog


def _check_source(source: Any, name: str, packages: list[str], where: str) -> dict | None:
    if source is None:
        return None
    if not isinstance(source, dict):
        raise OpError(f"{where}: source must be a table")
    unknown = sorted(set(source) - {"type", "url", "ref", "subdir"})
    if unknown:
        raise OpError(f"{where}: unknown source key(s): {', '.join(unknown)}")
    kind = source.get("type")
    if kind not in ("feed", "package"):
        raise OpError(f"{where}: source.type must be feed or package")
    for key in ("url", "ref"):
        if not isinstance(source.get(key), str) or not source[key]:
            raise OpError(f"{where}: source.{key} must be a non-empty string")
    if not REF.fullmatch(source["ref"]):
        raise OpError(f"{where}: source.ref contains unsupported characters")
    if not packages:
        raise OpError(f"{where}: a feature with a source must own its packages")
    if kind == "feed":
        if "subdir" in source:
            raise OpError(f"{where}: source.subdir only applies to package sources")
        if not FEED_NAME.fullmatch(name):
            raise OpError(f"{where}: feed feature names must match {FEED_NAME.pattern}")
        return dict(source)
    if len(packages) != 1:
        raise OpError(f"{where}: a package source must own exactly one package")
    subdir = source.get("subdir", ".")
    if not isinstance(subdir, str) or subdir.startswith("/") or ".." in Path(subdir).parts:
        raise OpError(f"{where}: source.subdir must be a relative path inside the repository")
    return {**source, "subdir": subdir}


def _read_seed(path: Path, label: str, owned: dict[str, str], target: dict) -> dict[str, str]:
    values = kconfig.parse(path.read_text(), label)
    t, st = re.escape(target["target"]), re.escape(target["subtarget"])
    reserved = re.compile(rf"TARGET_({t}|{t}_{st}|{t}_{st}_DEVICE_.+|MULTI_PROFILE"
                          rf"|PER_DEVICE_ROOTFS|ALL_PROFILES|DEVICE_.+)")
    for symbol in values:
        if symbol.startswith("PACKAGE_") and symbol[8:] in owned:
            raise OpError(f"{label}: CONFIG_{symbol} belongs to feature {owned[symbol[8:]]}; "
                          "enable or disable the feature in the profile instead")
        if reserved.fullmatch(symbol):
            raise OpError(f"{label}: CONFIG_{symbol} selects the target, which base/base.toml fixes")
    return values


def resolve(root: Path, profile: str, today: datetime.date | None = None) -> Resolved:
    if not PROFILE_NAME.fullmatch(profile):
        raise OpError(f"invalid profile name: {profile}")
    profile_dir = root / "profiles" / profile
    manifest = profile_dir / "profile.toml"
    if not manifest.is_file():
        raise OpError(f"unknown profile: {profile} (missing profiles/{profile}/profile.toml)")
    where = f"profiles/{profile}/profile.toml"
    base = load_base(root)
    catalog = load_catalog(root)
    data = _load_toml(manifest, where)
    warnings: list[str] = []

    unknown = sorted(set(data) - {"features", "system", "source", "build"} - set(catalog))
    if unknown:
        raise OpError(f"{where}: unknown key(s) or feature(s): {', '.join(unknown)}")
    features = data.get("features")
    if not isinstance(features, list) or not all(isinstance(name, str) for name in features):
        raise OpError(f"{where}: features must be a list of feature names")
    duplicates = sorted({name for name in features if features.count(name) > 1})
    if duplicates:
        raise OpError(f"{where}: duplicate feature(s): {', '.join(duplicates)}")
    missing = sorted(set(features) - set(catalog))
    if missing:
        raise OpError(f"{where}: unknown feature(s): {', '.join(missing)} "
                      f"(available: {', '.join(sorted(catalog))})")
    enabled = sorted(features)

    system = {**base["system"], **schema.apply(SYSTEM_SPEC, data.get("system", {}), "system")}
    system.setdefault("version", (today or datetime.date.today()).strftime("%y.%m"))
    host = int(ipaddress.IPv4Address(system["lan_ip"])) & 0xFF
    if host in (0, 255):
        raise OpError(f"{where}: system.lan_ip must be a host address in its /24")
    source = {**base["source"], **schema.apply(SOURCE_SPEC, data.get("source", {}), "source")}
    build = {**base["build"], **schema.apply(BUILD_SPEC, data.get("build", {}), "build")}

    params: dict[str, dict] = {}
    for name, feature in catalog.items():
        table = data.get(name)
        if name not in enabled:
            if table is not None:
                warnings.append(f"{where}: [{name}] is ignored because the feature is not enabled")
            continue
        if not feature.params:
            if table is not None:
                raise OpError(f"{where}: feature {name} has no parameters")
            continue
        params[name] = schema.apply(feature.params, {} if table is None else table, name)
        if name in CHECKS:
            CHECKS[name](params[name], system["lan_ip"])

    target = base["target"]
    owned = {pkg: feature.name for feature in catalog.values() for pkg in feature.packages}
    layers: list[tuple[str, dict[str, str]]] = [
        ("base/config.seed", _read_seed(root / "base" / "config.seed", "base/config.seed", owned, target)),
        ("target (base/base.toml)", {
            f"TARGET_{target['target']}": "y",
            f"TARGET_{target['target']}_{target['subtarget']}": "y",
            f"TARGET_{target['target']}_{target['subtarget']}_DEVICE_{target['device']}": "y",
        }),
    ]
    for name, feature in catalog.items():
        seed = feature.path / "config.seed"
        seed_values = _read_seed(seed, f"features/{name}/config.seed", owned, target) if seed.is_file() else {}
        if name in enabled:
            layers.append((f"feature {name}", {f"PACKAGE_{pkg}": "y" for pkg in feature.packages}))
            if seed_values:
                layers.append((f"features/{name}/config.seed", seed_values))
        elif feature.packages:
            layers.append((f"feature {name} (disabled)",
                           {f"PACKAGE_{pkg}": kconfig.NOT_SET for pkg in feature.packages}))
    profile_seed = profile_dir / "config.seed"
    if profile_seed.is_file():
        label = f"profiles/{profile}/config.seed"
        layers.append((label, _read_seed(profile_seed, label, owned, target)))
    layers.append(("generated", {
        "IMAGEOPT": "y",
        "VERSIONOPT": "y",
        "VERSION_DIST": f'"{system["dist"]}"',
        "VERSION_NUMBER": f'"{system["version"]}"',
    }))

    return Resolved(root, profile, profile_dir, source, target, system, build, enabled, catalog,
                    params, layers, kconfig.merge(values for _, values in layers), warnings)
