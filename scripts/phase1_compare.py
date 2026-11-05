"""Record actual vulnerable/patched subprocess evidence in restricted containers."""

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

from switchcrew.engineering import git
from switchcrew.execution import capture_command
from switchcrew.green_check import REPRODUCTION
from switchcrew.isolation import DockerIsolation


async def compare(args):
    config = json.loads(args.container_config.read_text())
    isolation = DockerIsolation.from_config(config)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for label, revision in (("vulnerable", args.vulnerable), ("patched", args.patched)):
        revision = git(args.source, "rev-parse", revision + "^{commit}")
        checkout = args.output / label
        subprocess.run(
            ["git", "clone", "--no-hardlinks", "--no-checkout", str(args.source), str(checkout)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        git(checkout, "checkout", "--detach", revision)
        revision = git(checkout, "rev-parse", "HEAD")
        commands = [["python", "-c", REPRODUCTION]]
        if label == "patched":
            commands.append(
                [
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "tests/test_phase1_trivy.py",
                    "tests/test_security.py",
                ]
            )
        records = []
        for index, command in enumerate(commands):
            with isolation.launch(command, checkout) as argv:
                receipt = await capture_command(
                    argv,
                    cwd=checkout,
                    env={"PATH": os.defpath, "HOME": str(args.output), "LANG": "C.UTF-8"},
                    timeout=120,
                    artifact=args.output / f"{label}-{index}.log",
                    max_bytes=128000,
                )
            records.append(
                {**receipt, "command": command, "revision": revision, "isolation": "docker"}
            )
        results.append({"label": label, "revision": revision, "commands": records})
    (args.output / "comparison.json").write_text(json.dumps(results, indent=2))
    for result in results:
        print(result["label"], result["revision"], [r["exit_code"] for r in result["commands"]])
    expected = [1, 0, 0]
    codes = [r["exit_code"] for result in results for r in result["commands"]]
    return codes == expected and all(
        not r["timed_out"] and not r["output_limit_exceeded"]
        for result in results
        for r in result["commands"]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--vulnerable", required=True)
    parser.add_argument("--patched", required=True)
    parser.add_argument("--container-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(compare(args)) else 1)


if __name__ == "__main__":
    main()
