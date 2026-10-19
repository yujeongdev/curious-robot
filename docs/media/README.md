# Agent Lab media

This directory holds lightweight, reviewable portfolio media. The authoritative
scientific evidence remains the terminal run directory under `artifacts/`:
`events.ndjson`, measurements, the Newton visual recording, environment
fingerprints, and the deterministic re-execution report.

## Generate the canonical demo

First create and finish a recorded run through Agent Lab. Then capture its
deterministic replay:

```bash
scripts/record_agent_lab_demo.sh \
  --run artifacts/<run_id> \
  --output docs/media/curious-robot-demo.mp4
```

The wrapper calls the frontend's `record:demo` script, which owns browser
replay and frame rendering. It then independently checks that the MP4:

- contains a decodable video stream;
- is exactly 1920×1080 at 30 fps by default;
- has a positive duration;
- is not a wholly blank capture;
- receives a SHA-256 sidecar only after validation.

The frontend additionally checks the R3F viewport crop. A bright interface
around an empty WebGL canvas can pass a whole-frame luminance test, so viewport
readiness and edge/luminance checks must remain part of `record:demo`.

The canonical 45-second edit makes five evidence checkpoints legible without
narration:

1. the unknown surface and broad initial belief;
2. selection of a friction probe from competing experiments;
3. measured breakaway force synchronized with contact;
4. posterior friction estimates and visibly reduced uncertainty;
5. push/pick prediction, execution, residual, and verification or Learn loop.

Capture uses the dark Precision Lab Instrument theme, the canonical 65/35
viewport/evidence split, a 220 px telemetry dock, local fonts/assets, and a
fixed replay clock. It must not make network requests or interpolate Newton
physics. See [`DESIGN.md`](../../DESIGN.md) and the
[Agent Lab design](../designs/agent-lab-ui.md) for the full contract.

## Publishing policy

Commit a reasonably sized optimized GIF preview and a labeled screenshot.
Publish the MP4 and full run artifacts as a versioned GitHub Release asset or
workflow artifact; GitHub README rendering does not reliably inline repository
MP4 files. Release notes must name the source run ID and environment
fingerprint, and should include the generated `.sha256` file.

Generated video files are intentionally absent until a real terminal run has
passed capture validation. Do not add a fabricated placeholder that could be
mistaken for executed evidence.
