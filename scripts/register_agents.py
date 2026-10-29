#!/usr/bin/env python3
"""Register custom workers and save one-time credentials without printing keys."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from switchcrew.switch import register_agent


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Reserve the path before registration; never overwrite an existing key.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        result = await register_agent(
            args.url, os.environ["SWITCH_REGISTRATION_TOKEN"], args.name, args.description
        )
        with os.fdopen(fd, "w") as output:
            fd = None
            json.dump(result, output)
            output.write("\n")
    finally:
        if fd is not None:
            os.close(fd)
            args.output.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
