"""Produce a minimal Secret patch enabling Forgejo's per-job Docker network.

Input/output are piped Secret data and must never be printed to CI logs.
Only container.network is changed; runner credentials remain byte-for-byte.
"""
from __future__ import annotations

import base64
import json
import re
import sys


def automatic_network(config: str) -> str:
    lines = config.splitlines(keepends=True)
    sections = [index for index, line in enumerate(lines)
                if re.fullmatch(r"container:\s*(?:#[^\n]*)?\n?", line)]
    if len(sections) != 1:
        raise ValueError("Runner config must have one block-style container section")
    start = sections[0] + 1
    end = next((index for index in range(start, len(lines))
                if re.match(r"^[^\s#][^:]*:", lines[index])), len(lines))
    network = [index for index in range(start, end)
               if re.match(r"^  network:", lines[index])]
    if len(network) > 1:
        raise ValueError("Runner config contains duplicate container.network entries")
    if network:
        line = lines[network[0]]
        if re.fullmatch(r"  network:\s*(?:\"\"|'')\s*(?:#[^\n]*)?\n?", line):
            return config
        lines[network[0]] = '  network: ""\n'
    else:
        lines.insert(start, '  network: ""\n')
    return "".join(lines)


def secret_patch(secret: dict) -> dict | None:
    config = base64.b64decode(secret["data"]["config.yml"], validate=True).decode()
    updated = automatic_network(config)
    if updated == config:
        return None
    return {"data": {"config.yml": base64.b64encode(updated.encode()).decode()}}


if __name__ == "__main__":
    try:
        patch = secret_patch(json.load(sys.stdin))
    except (KeyError, ValueError, UnicodeDecodeError):
        print("Invalid runner connection config; refusing to alter credentials.", file=sys.stderr)
        raise SystemExit(1) from None
    if patch is not None:
        json.dump(patch, sys.stdout)
