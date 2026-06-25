"""Helpers for unwrapping liberty-parser value types into plain Python values."""
from liberty.types import EscapedString, WithUnit


def unwrap(value):
    if value is None:
        return None
    if isinstance(value, EscapedString):
        return value.value
    if isinstance(value, WithUnit):
        return value.value
    return value


def unwrap_str(group, key, default=None):
    v = unwrap(group.get(key))
    return default if v is None else str(v)


def unwrap_float(group, key, default=None):
    v = unwrap(group.get(key))
    if v is None:
        return default
    return float(v)


def unwrap_bool(group, key, default=None):
    v = unwrap(group.get(key))
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def array_1d(group, key):
    """Read a complex attribute that holds a single row of numbers (e.g. index_1)."""
    if key not in group:
        return None
    arr = group.get_array(key)
    if arr.size == 0:
        return None
    return [float(x) for x in arr.flatten()]


def array_2d(group, key):
    """Read a complex attribute that holds a matrix of numbers (e.g. values)."""
    if key not in group:
        return None
    arr = group.get_array(key)
    if arr.size == 0:
        return None
    return [[float(x) for x in row] for row in arr]
