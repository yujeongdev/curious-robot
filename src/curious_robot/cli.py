"""Command-line interface for Curious Robot."""

from __future__ import annotations

import argparse
import threading
import webbrowser


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Discover Physics. Simulate. Act. Learn.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one autonomous discovery episode")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--device", default="cuda:0")
    run.add_argument("--output", default="artifacts/demo")
    run.add_argument("--no-robot", action="store_true", help="CI-only reduced scene")
    friction = subparsers.add_parser(
        "lab-friction",
        help="run the isolated Reality-to-Twin friction capability slice",
    )
    friction.add_argument("--seed", type=int, default=7)
    friction.add_argument("--device", default="cuda:0")
    friction.add_argument("--output", default="artifacts/lab-friction")
    distill = subparsers.add_parser(
        "lab-distill",
        help="distill a successful friction run into a deterministic capability",
    )
    distill.add_argument("--run", required=True, help="terminal lab-friction artifact directory")
    execute_skill = subparsers.add_parser(
        "lab-execute-skill",
        help="execute a distilled capability through simulated Reality",
    )
    execute_skill.add_argument("--run", required=True)
    execute_skill.add_argument("--seed", type=int, default=7)
    execute_skill.add_argument("--device", default="cuda:0")
    web = subparsers.add_parser("web", help="launch the local Agent Lab")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--open", action=argparse.BooleanOptionalAction, default=True)
    web.add_argument("--autorun", action="store_true", help="start the known-good demo from the browser")
    web.add_argument("--seed", type=int, default=7)
    web.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    if args.command == "run":
        from .runner import run_episode

        result = run_episode(
            seed=args.seed,
            output_directory=args.output,
            device=args.device,
            include_robot=not args.no_robot,
        )
        print(result.model_dump_json(indent=2))
        if not result.success:
            raise SystemExit(2)
        return
    if args.command == "lab-friction":
        from pathlib import Path

        from .lab.friction_runner import run_newton_friction_slice

        run_newton_friction_slice(
            seed=args.seed,
            output_directory=args.output,
            device=args.device,
        )
        manifest = Path(args.output) / "friction_capability_manifest.json"
        print(manifest.read_text(), end="")
        return
    if args.command == "lab-distill":
        from .lab.capability_artifact import distill_from_manifest

        reference = distill_from_manifest(args.run)
        print(reference.artifact_id)
        return
    if args.command == "lab-execute-skill":
        from .lab.capability_skill import execute_from_manifest

        decision = execute_from_manifest(args.run, device=args.device, seed=args.seed)
        print(decision.status.value)
        if decision.status.value == "return_to_agent":
            raise SystemExit(2)
        return

    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("Agent Lab binds to loopback by default; non-loopback hosting is not supported yet")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install web dependencies with: uv sync --extra web") from exc
    from .web_api import create_app

    url = f"http://{args.host}:{args.port}/"
    if args.autorun:
        url += f"?autorun=1&seed={args.seed}&device={args.device}"
    if args.open:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
