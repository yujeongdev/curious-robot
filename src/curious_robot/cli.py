"""Command-line interface for Curious Robot."""

from __future__ import annotations

import argparse

from .runner import run_episode


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Discover Physics. Simulate. Act. Learn.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one autonomous discovery episode")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--device", default="cuda:0")
    run.add_argument("--output", default="artifacts/demo")
    run.add_argument("--no-robot", action="store_true", help="CI-only reduced scene")
    args = parser.parse_args(argv)
    result = run_episode(
        seed=args.seed,
        output_directory=args.output,
        device=args.device,
        include_robot=not args.no_robot,
    )
    print(result.model_dump_json(indent=2))
    if not result.success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
