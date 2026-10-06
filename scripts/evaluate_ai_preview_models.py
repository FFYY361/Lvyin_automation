"""Generate comparable AI-preview samples for selected model profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from ai_preview import load_prompt_config
from ai_preview.source import build_live_prompt_bundle
from ai_service import AIChatService, AIServiceError, ChatMessage
from ai_service.config import load_ai_service_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_name")
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--config", type=Path, help="temporary model config")
    parser.add_argument("--env", type=Path, help="credential env file")
    parser.add_argument(
        "--match-id",
        action="append",
        type=int,
        dest="match_ids",
        default=[],
    )
    return parser


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _run(args: argparse.Namespace) -> int:
    match_ids = args.match_ids or [4049, 4076, 4011]
    run_directory = PROJECT_ROOT / "data" / "ai_preview" / "runs" / args.run_name
    run_directory.mkdir(parents=True, exist_ok=False)
    prompts_directory = run_directory / "prompts"
    prompts_directory.mkdir()

    prompt_config = load_prompt_config()
    service_config = load_ai_service_config(args.config) if args.config else load_ai_service_config()
    candidates = []
    for name in args.profile:
        profile = service_config.get_profile(name)
        candidates.append(
            {
                "name": name,
                "base_url": profile.base_url,
                "model": profile.model,
                "temperature": profile.temperature,
                "max_tokens": profile.max_tokens,
                "timeout_seconds": profile.timeout_seconds,
                "request_options": profile.request_options,
            }
        )

    bundles = {}
    for match_id in match_ids:
        bundle = await build_live_prompt_bundle(match_id, config=prompt_config)
        bundles[match_id] = bundle
        (prompts_directory / f"{match_id}.system.md").write_text(
            bundle.system_message, encoding="utf-8"
        )
        (prompts_directory / f"{match_id}.user.md").write_text(
            bundle.render_user_message(), encoding="utf-8"
        )

    manifest = {
        "run_name": args.run_name,
        "created_at": datetime.now().astimezone().isoformat(),
        "git_commit": _git_commit(),
        "match_ids": match_ids,
        "prompt_config": {
            "recent_matches_with_events": prompt_config.recent_matches_with_events,
            "history_seasons": prompt_config.history_seasons,
        },
        "candidates": candidates,
        "results": [],
    }
    manifest_path = run_directory / "manifest.json"
    _write_json(manifest_path, manifest)
    run_started = time.perf_counter()

    for profile_name in args.profile:
        output_directory = run_directory / profile_name
        output_directory.mkdir()
        for match_id in match_ids:
            bundle = bundles[match_id]
            started = time.perf_counter()
            try:
                service_kwargs = {}
                if args.config:
                    service_kwargs["config_path"] = args.config
                if args.env:
                    service_kwargs["env_path"] = args.env
                async with AIChatService.from_config(profile_name, **service_kwargs) as service:
                    result = await service.chat(
                        [
                            ChatMessage("system", bundle.system_message),
                            ChatMessage("user", bundle.render_user_message()),
                        ]
                    )
                elapsed = round(time.perf_counter() - started, 3)
                output_file = output_directory / f"{match_id}.md"
                output_file.write_text(result.content.strip() + "\n", encoding="utf-8")
                record = {
                    "candidate": profile_name,
                    "match_id": match_id,
                    "status": "ok",
                    "model": result.model,
                    "finish_reason": result.finish_reason,
                    "usage": result.usage.__dict__,
                    "output_file": output_file.relative_to(run_directory).as_posix(),
                    "elapsed_seconds": elapsed,
                }
            except AIServiceError as exc:
                elapsed = round(time.perf_counter() - started, 3)
                record = {
                    "candidate": profile_name,
                    "match_id": match_id,
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "status_code": exc.status_code,
                    "elapsed_seconds": elapsed,
                }
            manifest["results"].append(record)
            manifest["elapsed_seconds"] = round(time.perf_counter() - run_started, 3)
            _write_json(manifest_path, manifest)
            print(
                f"{profile_name} match={match_id} status={record['status']} "
                f"elapsed={elapsed:.3f}s",
                flush=True,
            )
    return 0


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
