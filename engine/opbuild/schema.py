"""Parameter schemas declared in feature.toml and validation of profile values."""

from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

from .common import OpError

TYPES = {"string", "integer", "boolean", "ipv4", "cidr", "list", "table", "map"}
SCALARS = {"string", "integer", "boolean", "ipv4", "cidr"}
SPEC_KEYS = {"type", "required", "default", "secret", "enum", "pattern", "keys",
             "min", "max", "items", "fields"}
MASK = "******"


def check_spec(spec: Any, where: str, depth: int = 0) -> None:
    """Validate a parameter declaration. Nesting is limited to what UCI rendering supports."""
    if not isinstance(spec, dict):
        raise OpError(f"{where}: parameter declaration must be a table")
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        raise OpError(f"{where}: unknown declaration keys: {', '.join(unknown)}")
    kind = spec.get("type")
    if kind not in TYPES:
        raise OpError(f"{where}: type must be one of {', '.join(sorted(TYPES))}")
    for flag in ("required", "secret"):
        if not isinstance(spec.get(flag, False), bool):
            raise OpError(f"{where}: {flag} must be true or false")
    if spec.get("required") and "default" in spec:
        raise OpError(f"{where}: a required parameter cannot have a default")
    for key in ("pattern", "keys"):
        if key in spec:
            try:
                re.compile(spec[key])
            except (re.error, TypeError) as error:
                raise OpError(f"{where}: invalid {key}: {error}") from error
    if "pattern" in spec and kind != "string":
        raise OpError(f"{where}: pattern applies to strings only")
    if "enum" in spec and (kind not in ("string", "integer") or not isinstance(spec["enum"], list)):
        raise OpError(f"{where}: enum must be a list for a string or integer")
    for bound in ("min", "max"):
        if bound in spec and (kind not in ("string", "integer", "list")
                              or not isinstance(spec[bound], int) or isinstance(spec[bound], bool)):
            raise OpError(f"{where}: {bound} must be an integer for a string, integer or list")
    if ("items" in spec) != (kind in ("list", "map")):
        raise OpError(f"{where}: items is required for list and map, and only allowed there")
    if ("fields" in spec) != (kind == "table"):
        raise OpError(f"{where}: fields is required for table, and only allowed there")
    if ("keys" in spec) != (kind == "map"):
        raise OpError(f"{where}: keys is required for map, and only allowed there")
    if kind == "table":
        if depth != 1 or not isinstance(spec["fields"], dict):
            raise OpError(f"{where}: tables are only supported as list or map items")
        for name, field in spec["fields"].items():
            check_spec(field, f"{where}.fields.{name}", depth + 1)
            if field["type"] not in SCALARS and not (field["type"] == "list"
                                                     and field["items"]["type"] in SCALARS):
                raise OpError(f"{where}.fields.{name}: table fields must be scalars or scalar lists")
    elif kind in ("list", "map"):
        check_spec(spec["items"], f"{where}.items", depth + 1)
        item_type = spec["items"]["type"]
        if kind == "map" and item_type != "table":
            raise OpError(f"{where}: map items must be tables")
        if kind == "list" and item_type not in SCALARS and item_type != "table":
            raise OpError(f"{where}: list items must be scalars or tables")
    if "default" in spec:
        validate(spec, spec["default"], f"{where}.default")


def validate(spec: dict, value: Any, path: str, secret: bool = False) -> Any:
    """Return a normalized value or raise OpError without echoing secrets.
    Items of a secret list, table or map are secret as well."""
    kind = spec["type"]
    secret = secret or spec.get("secret", False)

    def fail(message: str):
        raise OpError(f"{path}: {message}")

    def shown(text: Any) -> str:
        return "" if secret else f" (got {text!r})"

    if kind in ("string", "ipv4", "cidr"):
        if not isinstance(value, str):
            fail("must be a string")
        if any(char in value for char in "\n\r\0"):
            fail("must not contain line breaks or NUL characters")
    if kind == "string":
        size = len(value.encode())
        if "min" in spec and size < spec["min"]:
            fail(f"must be at least {spec['min']} bytes")
        if "max" in spec and size > spec["max"]:
            fail(f"must be at most {spec['max']} bytes")
        if "enum" in spec and value not in spec["enum"]:
            fail(f"must be one of {', '.join(map(str, spec['enum']))}{shown(value)}")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            fail(f"must match {spec['pattern']}{shown(value)}")
        return value
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            fail("must be an integer")
        if "enum" in spec and value not in spec["enum"]:
            fail(f"must be one of {', '.join(map(str, spec['enum']))}{shown(value)}")
        if "min" in spec and value < spec["min"]:
            fail(f"must be at least {spec['min']}")
        if "max" in spec and value > spec["max"]:
            fail(f"must be at most {spec['max']}")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            fail("must be true or false")
        return value
    if kind == "ipv4":
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            fail(f"must be an IPv4 address{shown(value)}")
        if str(address) != value:
            fail(f"must be written as {address}")
        return value
    if kind == "cidr":
        if "/" not in value:
            fail(f"must include a prefix length, for example 172.16.1.1/24{shown(value)}")
        try:
            interface = ipaddress.IPv4Interface(value)
        except ValueError:
            fail(f"must be an IPv4 address with a prefix length{shown(value)}")
        if str(interface) != value:
            fail(f"must be written as {interface}")
        return value
    if kind == "list":
        if not isinstance(value, list):
            fail("must be a list")
        if "min" in spec and len(value) < spec["min"]:
            fail(f"must contain at least {spec['min']} items")
        if "max" in spec and len(value) > spec["max"]:
            fail(f"must contain at most {spec['max']} items")
        return [validate(spec["items"], item, f"{path}[{index}]", secret)
                for index, item in enumerate(value)]
    if kind == "table":
        if not isinstance(value, dict):
            fail("must be a table")
        return apply(spec["fields"], value, path, secret)
    # map
    if not isinstance(value, dict):
        fail("must be a table")
    result = {}
    for key in sorted(value):
        if not re.fullmatch(spec["keys"], key):
            fail(f"key {key!r} must match {spec['keys']}")
        result[key] = validate(spec["items"], value[key], f"{path}.{key}", secret)
    return result


def apply(specs: dict, values: Any, path: str, secret: bool = False) -> dict:
    """Validate a table against declarations, filling defaults and rejecting unknown keys."""
    if not isinstance(values, dict):
        raise OpError(f"{path}: must be a table")
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise OpError(f"{path}: unknown key(s): {', '.join(unknown)}")
    result = {}
    for name, spec in specs.items():
        where = f"{path}.{name}" if path else name
        if name in values:
            result[name] = validate(spec, values[name], where, secret)
        elif "default" in spec:
            result[name] = copy.deepcopy(spec["default"])
        elif spec.get("required"):
            raise OpError(f"{where}: required")
    return result


def mask(specs: dict, values: dict) -> dict:
    """Replace secret values for display."""
    return {name: _mask(specs[name], value) for name, value in values.items()}


def _mask(spec: dict, value: Any) -> Any:
    kind = spec["type"]
    if spec.get("secret"):
        return [MASK] * len(value) if kind == "list" else MASK
    if kind == "list":
        return [_mask(spec["items"], item) for item in value]
    if kind == "table":
        return mask(spec["fields"], value)
    if kind == "map":
        return {key: _mask(spec["items"], item) for key, item in value.items()}
    return value
