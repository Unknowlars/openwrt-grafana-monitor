#!/usr/bin/env python3
"""Render a JSON file as a Lua table literal.

Used by the Lua collector harnesses so they can parse real `nft -j` fixtures
without needing lua-cjson installed on the development machine.
"""

import json
import sys


def render(value) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"[{json.dumps(str(k))}]={render(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(render(v) for v in value) + "}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nil"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(render(json.load(handle)))
