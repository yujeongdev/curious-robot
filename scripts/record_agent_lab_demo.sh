#!/usr/bin/env bash
# Capture and validate a deterministic Agent Lab replay.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_dir="${repo_root}/artifacts/demo"
output="${repo_root}/docs/media/curious-robot-demo.mp4"
web_dir="${repo_root}/src/web"
width=1920
height=1080
fps=30
expected_task=""

usage() {
  cat <<'EOF'
Usage: scripts/record_agent_lab_demo.sh [options]

Capture a terminal Agent Lab run through the frontend's deterministic replay
clock, validate the encoded video, and write a SHA-256 sidecar.

Options:
  --run DIR          Terminal run directory (default: artifacts/demo)
  --output FILE      Final MP4 path (default: docs/media/curious-robot-demo.mp4)
  --web-dir DIR      Frontend workspace (default: src/web)
  --width PX         Capture width (default: 1920)
  --height PX        Capture height (default: 1080)
  --fps N            Capture frame rate (default: 30)
  --expect-task ID   Require run.started task ID and matching public geometry
  -h, --help         Show this help

The frontend command is expected to implement this contract:
  pnpm --dir src/web record:demo -- --run DIR --output FILE \
    --width 1920 --height 1080 --fps 30

It must use replay's fixed clock, wait for the reducer/chart/R3F render barrier,
and reject a blank WebGL viewport. This wrapper independently verifies the
container, dimensions, frame rate, duration, wholly blank output, and checksum.
EOF
}

die() {
  printf 'record-agent-lab-demo: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing '$1'. $2"
}

positive_integer() {
  [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "$1 must be a positive integer (got '$2')"
}

while (($#)); do
  case "$1" in
    --run)
      (($# >= 2)) || die "--run requires a directory"
      run_dir="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die "--output requires a file"
      output="$2"
      shift 2
      ;;
    --web-dir)
      (($# >= 2)) || die "--web-dir requires a directory"
      web_dir="$2"
      shift 2
      ;;
    --width)
      (($# >= 2)) || die "--width requires a value"
      width="$2"
      shift 2
      ;;
    --height)
      (($# >= 2)) || die "--height requires a value"
      height="$2"
      shift 2
      ;;
    --fps)
      (($# >= 2)) || die "--fps requires a value"
      fps="$2"
      shift 2
      ;;
    --expect-task)
      (($# >= 2)) || die "--expect-task requires a task ID"
      expected_task="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument '$1' (use --help)"
      ;;
  esac
done

positive_integer "--width" "$width"
positive_integer "--height" "$height"
positive_integer "--fps" "$fps"

[[ -d "$run_dir" ]] || die "run directory not found: $run_dir"
run_dir="$(cd "$run_dir" && pwd)"
web_dir="$(cd "$web_dir" && pwd)"
output_parent="$(dirname "$output")"
mkdir -p "$output_parent"
output="$(cd "$output_parent" && pwd)/$(basename "$output")"
[[ -s "$run_dir/events.ndjson" ]] || die \
  "missing or empty $run_dir/events.ndjson; record a terminal Agent Lab run first"
[[ -f "$web_dir/package.json" ]] || die \
  "frontend workspace not found: $web_dir (build the Agent Lab frontend first)"

if [[ -n "$expected_task" ]]; then
  uv run python - "$run_dir/events.ndjson" "$expected_task" <<'PY' || die \
    "event log does not match --expect-task '$expected_task'"
import json
import sys

events_path, expected_task = sys.argv[1:]
task_ids = set()
archetypes = set()
with open(events_path, encoding="utf-8") as handle:
    for line in handle:
        event = json.loads(line)
        payload = event.get("payload", {})
        if event.get("type") == "run.started" and isinstance(payload.get("task_id"), str):
            task_ids.add(payload["task_id"])
        if event.get("type") == "simulation.visual_frame":
            geometry = payload.get("object_geometry")
            if isinstance(geometry, dict) and isinstance(geometry.get("archetype_id"), str):
                archetypes.add(geometry["archetype_id"])
if task_ids != {expected_task}:
    raise SystemExit(f"task IDs {sorted(task_ids)} do not equal {expected_task!r}")
expected_archetype = {"push-cube-v1": "cube", "push-t-bar-v1": "t-bar"}.get(expected_task)
if expected_archetype is not None and archetypes != {expected_archetype}:
    raise SystemExit(
        f"visual archetypes {sorted(archetypes)} do not equal {expected_archetype!r}"
    )
PY
fi

if command -v pnpm >/dev/null 2>&1; then
  pnpm_command=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  pnpm_command=(corepack pnpm)
else
  die "missing pnpm/corepack. Install Node.js with Corepack, then run 'corepack pnpm --dir src/web install --frozen-lockfile'."
fi
require_command ffmpeg "Install ffmpeg with your system package manager."
require_command ffprobe "Install ffprobe (normally included with ffmpeg)."

uv run python "${repo_root}/scripts/check_visual_clearance.py" \
  --events "${run_dir}/events.ndjson" \
  --model "${repo_root}/src/web/public/models/franka/fr3.glb" \
  --maximum-penetration-mm 1.1

if command -v sha256sum >/dev/null 2>&1; then
  checksum_command=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
  checksum_command=(shasum -a 256)
else
  die "missing SHA-256 utility; install coreutils (sha256sum) or Perl shasum"
fi

output_dir="$(dirname "$output")"
pending="$(mktemp "${output_dir}/.agent-lab-capture.XXXXXX.mp4")"
rm -f "$pending"

printf 'Capturing Agent Lab replay\n'
printf '  run:        %s\n' "$run_dir"
printf '  resolution: %sx%s @ %s fps\n' "$width" "$height" "$fps"
printf '  pending:    %s\n' "$pending"

set +e
"${pnpm_command[@]}" --dir "$web_dir" record:demo -- \
  --run "$run_dir" \
  --output "$pending" \
  --width "$width" \
  --height "$height" \
  --fps "$fps"
capture_status=$?
set -e

if ((capture_status != 0)); then
  printf 'Capture failed (exit %s). Any completed frames are intentionally preserved by the frontend.\n' \
    "$capture_status" >&2
  printf 'Pending output, if encoded: %s\n' "$pending" >&2
  exit "$capture_status"
fi

[[ -s "$pending" ]] || die "capture command succeeded but produced no MP4: $pending"

actual_width="$(ffprobe -v error -select_streams v:0 -show_entries stream=width \
  -of default=noprint_wrappers=1:nokey=1 "$pending")"
actual_height="$(ffprobe -v error -select_streams v:0 -show_entries stream=height \
  -of default=noprint_wrappers=1:nokey=1 "$pending")"
actual_rate="$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate \
  -of default=noprint_wrappers=1:nokey=1 "$pending")"
duration="$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$pending")"

[[ "$actual_width" == "$width" && "$actual_height" == "$height" ]] || die \
  "unexpected video dimensions ${actual_width}x${actual_height}; expected ${width}x${height}. Pending file: $pending"

awk -v rate="$actual_rate" -v expected="$fps" 'BEGIN {
  split(rate, value, "/");
  actual = (value[2] == 0 || value[2] == "") ? 0 : value[1] / value[2];
  if (actual < expected - 0.01 || actual > expected + 0.01) exit 1;
}' || die "unexpected frame rate '$actual_rate'; expected $fps fps. Pending file: $pending"

awk -v seconds="$duration" 'BEGIN { if (seconds <= 0.1) exit 1; }' || die \
  "video duration is invalid ($duration seconds). Pending file: $pending"

# A full-composition check catches a totally black/empty capture. The frontend's
# viewport-crop check remains authoritative for the subtler case where UI chrome
# rendered but WebGL did not.
black_log="$(mktemp "${output_dir}/.agent-lab-blackdetect.XXXXXX.log")"
if ! ffmpeg -hide_banner -nostdin -i "$pending" \
  -vf "blackdetect=d=0.1:pic_th=0.98:pix_th=0.10" -an -f null - \
  > /dev/null 2>"$black_log"; then
  die "ffmpeg could not decode the captured MP4. Diagnostic: $black_log"
fi

black_duration="$(sed -n 's/.*black_duration:\([0-9.][0-9.]*\).*/\1/p' "$black_log" | \
  awk '{ total += $1 } END { printf "%.6f", total + 0 }')"

if awk -v black="$black_duration" -v total="$duration" \
  'BEGIN { exit !(total > 0 && black / total >= 0.95) }'; then
  die "capture is at least 95% blank (${black_duration}s/${duration}s). Pending file: $pending"
fi
rm -f "$black_log"

checksum="$("${checksum_command[@]}" "$pending" | awk '{print $1}')"
pending_gif="${pending%.mp4}.gif"
pending_metadata="${pending}.json"
output_gif="${output%.mp4}.gif"
output_metadata="${output}.json"
mv -f "$pending" "$output"
[[ -s "$pending_gif" ]] || die "capture produced no GIF companion: $pending_gif"
[[ -s "$pending_metadata" ]] || die "capture produced no metadata sidecar: $pending_metadata"
mv -f "$pending_gif" "$output_gif"
mv -f "$pending_metadata" "$output_metadata"
printf '%s  %s\n' "$checksum" "$(basename "$output")" >"${output}.sha256"

printf 'Validated Agent Lab media\n'
printf '  output:     %s\n' "$output"
printf '  gif:        %s\n' "$output_gif"
printf '  duration:   %.3f s\n' "$duration"
printf '  resolution: %sx%s @ %s fps\n' "$actual_width" "$actual_height" "$fps"
printf '  sha256:     %s\n' "$checksum"
printf '  sidecar:    %s\n' "${output}.sha256"
