#!/usr/bin/env python3
"""Run a cadgenbench fixture through Claude Code (claude -p) with build123d-mcp.

Third comparison point alongside the baseline and mcp-agent strategies.
Claude Code's own agent loop handles the task autonomously with build123d-mcp
tools available via MCP — no artificial turn limits or token budgets imposed
by cadgenbench.

Requirements:
  - `claude` CLI in PATH (Claude Code)
  - `uvx` in PATH (to launch build123d-mcp)

Usage:
  uv run python scripts/run_claude_code.py 101
  uv run python scripts/run_claude_code.py 101 --output-dir results/claude_code
  uv run python scripts/run_claude_code.py 101 --mcp-server build123d-mcp  # if installed directly
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# MCP server name used in tool references (mcp__<name>__execute etc.)
_MCP_SERVER_NAME = "build123d"
_ARTIFACT_FILENAME = "output.step"


def _mcp_config(mcp_command: str, mcp_args: list[str]) -> dict:
    return {
        "mcpServers": {
            _MCP_SERVER_NAME: {
                "command": mcp_command,
                "args": mcp_args,
            }
        }
    }


def _build_prompt(description: str, output_step: Path, input_files: list[Path]) -> str:
    image_note = ""
    if any(f.suffix.lower() in {".png", ".jpg", ".jpeg"} for f in input_files):
        names = ", ".join(f.name for f in input_files if f.suffix.lower() in {".png", ".jpg", ".jpeg"})
        image_note = f"\nReference image(s) available: {names} (in the working directory).\n"

    step_note = ""
    if any(f.suffix.lower() in {".step", ".stp"} for f in input_files):
        names = ", ".join(f.name for f in input_files if f.suffix.lower() in {".step", ".stp"})
        step_note = (
            f"\nStarting STEP file(s) {names} are in the working directory. "
            f"Load with import_step('<filename>') in an execute() call.\n"
        )

    return f"""\
You are an expert CAD engineer. Use the build123d MCP tools to build a 3D model.

## Task
{description}
{image_note}{step_note}
## Instructions

1. Use execute() to write build123d Python code and build the geometry.
2. Use measure() to verify topology after boolean operations.
3. Use render_view() to visually inspect.
4. When satisfied, export the final model:
   export("{output_step}", format="step")
5. Do not stop until you have successfully exported the file.

The file MUST be written to exactly: {output_step}
"""


def run_fixture(
    fixture_name: str,
    *,
    output_dir: Path,
    mcp_command: str = "uvx",
    mcp_args: list[str] | None = None,
    model: str | None = None,
) -> dict:
    if mcp_args is None:
        mcp_args = ["--python", "3.12", "build123d-mcp@latest"]

    # Resolve fixture inputs from HF dataset or local data/.
    from cadgenbench.common.paths import data_inputs_dir, data_gt_dir
    inputs_dir = data_inputs_dir() / fixture_name
    if not inputs_dir.is_dir():
        print(f"ERROR: fixture {fixture_name!r} not found in {data_inputs_dir()}", file=sys.stderr)
        sys.exit(1)

    try:
        gt_dir: Path | None = data_gt_dir() / fixture_name
        if not (gt_dir / "ground_truth.step").exists():  # type: ignore[operator]
            gt_dir = None
    except Exception:
        gt_dir = None

    import yaml
    desc_path = inputs_dir / "description.yaml"
    description = yaml.safe_load(desc_path.read_text()).get("description", "")

    input_files = [p for p in inputs_dir.iterdir()
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".step", ".stp"}]

    # Work dir for the MCP server (output.step lands here).
    work_dir = Path(tempfile.mkdtemp(prefix="cadgenbench_cc_"))
    output_step = work_dir / _ARTIFACT_FILENAME

    # Copy input files into work dir so the MCP server can access them.
    for src in input_files:
        shutil.copy2(src, work_dir / src.name)

    prompt = _build_prompt(description, output_step, input_files)

    # Write temporary MCP config.
    mcp_cfg_file = work_dir / "mcp_config.json"
    mcp_cfg_file.write_text(json.dumps(_mcp_config(mcp_command, mcp_args), indent=2))

    # Build claude command.
    cmd = [
        "claude", "-p", prompt,
        "--mcp-config", str(mcp_cfg_file),
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--allowedTools",
        f"mcp__{_MCP_SERVER_NAME}__execute,"
        f"mcp__{_MCP_SERVER_NAME}__render_view,"
        f"mcp__{_MCP_SERVER_NAME}__measure,"
        f"mcp__{_MCP_SERVER_NAME}__export,"
        f"mcp__{_MCP_SERVER_NAME}__import_step",
    ]
    if model:
        cmd += ["--model", model]

    print(f"Fixture:    {fixture_name}")
    print(f"Work dir:   {work_dir}")
    print(f"Output:     {output_step}")
    print(f"MCP:        {mcp_command} {' '.join(mcp_args)}")
    print(f"Running claude -p …")

    t0 = __import__("time").monotonic()
    result = subprocess.run(cmd, cwd=str(work_dir), capture_output=False)
    elapsed = __import__("time").monotonic() - t0

    success = output_step.exists()
    print(f"\nDone in {elapsed:.1f}s  —  output.step {'FOUND' if success else 'NOT FOUND'}")

    # Copy results to output_dir.
    out_fixture_dir = output_dir / fixture_name
    out_fixture_dir.mkdir(parents=True, exist_ok=True)
    candidate = None
    if success:
        dest = out_fixture_dir / _ARTIFACT_FILENAME
        shutil.copy2(output_step, dest)
        candidate = dest

    # Copy any rendered images.
    for f in work_dir.iterdir():
        if f.suffix.lower() in {".png", ".svg"} and f != output_step:
            shutil.copy2(f, out_fixture_dir / f.name)

    shutil.rmtree(work_dir, ignore_errors=True)

    # Evaluate.
    summary: dict = {"fixture": fixture_name, "elapsed_s": round(elapsed, 1), "completed": success}
    if candidate and candidate.exists():
        from cadgenbench.eval.evaluate import evaluate_result, evaluate_candidate_only
        if gt_dir and (gt_dir / "ground_truth.step").exists():
            evaluate_result(out_fixture_dir, gt_dir, candidate_step=candidate)
            result_data = json.loads((out_fixture_dir / "result.json").read_text())
            summary["scores"] = result_data.get("scores", {})
            _print_scores(result_data)
        else:
            evaluate_candidate_only(candidate, out_fixture_dir)
            print("  (no ground truth available — validity only)")
    else:
        print("  Evaluation skipped: no output.step produced")

    (out_fixture_dir / "run_info.json").write_text(json.dumps(summary, indent=2))
    return summary


def _print_scores(data: dict) -> None:
    scores = data.get("scores", {})
    for k, v in scores.items():
        print(f"  {k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a cadgenbench fixture through Claude Code + build123d-mcp."
    )
    parser.add_argument("fixture", help="Fixture name (e.g. 101)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Results directory (default: ./results/claude_code_<timestamp>/)")
    parser.add_argument("--mcp-server", default="uvx",
                        help="Command to launch build123d-mcp (default: uvx)")
    parser.add_argument("--mcp-args", nargs=argparse.REMAINDER,
                        default=["--python", "3.12", "build123d-mcp@latest"],
                        help="Args for the MCP server command (must be last)")
    parser.add_argument("--model", default=None,
                        help="Claude model (default: Claude Code's default)")

    args = parser.parse_args()

    if not shutil.which("claude"):
        print("ERROR: `claude` not found in PATH. Install Claude Code first.", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or Path.cwd() / "results" / f"{timestamp}_claude_code"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_fixture(
        args.fixture,
        output_dir=output_dir,
        mcp_command=args.mcp_server,
        mcp_args=args.mcp_args or [],
        model=args.model,
    )


if __name__ == "__main__":
    main()
