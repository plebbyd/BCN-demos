#!/usr/bin/env python3
"""
MCP server — Simulated PTZ camera (panorama + YOLO / BioCLIP / Gemma4).

Run with stdio (Cursor / Claude MCP client)::

    python3 msa_mcp/ptz_server.py

Configure Cursor: add an MCP server with command ``python3``, argument
``msa_mcp/ptz_server.py``, and ``cwd`` set to this repository root.

**Important:** This module lives in ``msa_mcp/`` so it does not shadow the PyPI
package named ``mcp``.

Requires: ``pip install mcp`` (Model Context Protocol Python SDK) and Pillow.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Project root (parent of msa_mcp/)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("ERROR: MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP(
    "MSA PTZ (sim or Reolink)",
    instructions=(
        "PTZ: default simulated 360° panorama (stitched.png). "
        "Set MSA_PTZ_BACKEND=reolink plus REOLINK_IP, REOLINK_USER, REOLINK_PASSWORD and tools/calibration.json for a real Reolink camera (pan/tilt degrees as in calibration). "
        "Use msa_ptz_get_position before and after moves. "
        "Detection: yolo (COCO), bioclip (species / taxon filter), gemma4 (VLM via Ollama). "
        "msa_ptz_run_mission sweeps pan (and grid tilt on hardware) and may take minutes. "
        "Watch-along: sim_ptz.watch_along in config or MSA_PTZ_WATCH_ALONG=1."
    ),
)


def _json(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


def _cam():
    from tools.sim_ptz_tool import HAS_PIL
    from tools.ptz_facade import get_ptz_camera

    if not HAS_PIL:
        raise RuntimeError("Pillow not installed")
    return get_ptz_camera()


@mcp.tool()
def msa_ptz_get_position() -> str:
    """Get current pan (0–360°), tilt, horizontal/vertical FOV, and tilt/pan limits."""
    try:
        return _json(_cam().get_position())
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_move_to(pan: float, tilt: float) -> str:
    """Move the simulated camera to absolute pan (0–360°) and tilt (degrees)."""
    try:
        return _json(_cam().move_to(pan, tilt))
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_pan_by(degrees: float) -> str:
    """Pan relative by degrees (positive = right, negative = left)."""
    try:
        return _json(_cam().pan_by(degrees))
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_tilt_by(degrees: float) -> str:
    """Tilt relative by degrees (positive = up, negative = down)."""
    try:
        return _json(_cam().tilt_by(degrees))
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_set_fov(fov_h_degrees: float) -> str:
    """Set horizontal field of view in degrees (10–120). Narrows/widens the viewport."""
    try:
        return _json(_cam().set_fov_h(fov_h_degrees))
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_detect(
    model: str = "yolo",
    targets: str = "*",
    target_taxon: str = "",
    target: str = "",
    max_soft_tokens: int | None = None,
) -> str:
    """
    Run object detection on the current viewport.
    model: yolo | bioclip | gemma4
    YOLO: targets comma-separated class names or * for all.
    BioCLIP: target_taxon optional lineage (e.g. Mammalia or Animalia Chordata Mammalia); matches any strong class, not only top-1.
    Gemma4 (Ollama): target (or targets) describes what to find; optional max_soft_tokens (70–1120).
    """
    try:
        from tools.detectors import detect

        viewport = _cam()._crop_viewport()
        kwargs: dict = {}
        if model == "yolo":
            kwargs["targets"] = targets
        elif model == "bioclip":
            kwargs["target_taxon"] = target_taxon
            kwargs["rank"] = "Class"
        elif model == "gemma4":
            hint = (target or "").strip() or (
                targets if targets.strip() not in ("", "*") else ""
            )
            kwargs["target"] = hint
            if max_soft_tokens is not None:
                kwargs["max_soft_tokens"] = int(max_soft_tokens)
        from tools.sim_ptz_watch import sleep_after_inference

        out = detect(viewport, model=model, **kwargs)
        sleep_after_inference()
        return _json(out)
    except Exception as e:
        return _json({"error": str(e), "detections": []})


@mcp.tool()
def msa_ptz_caption(
    model: str = "bioclip",
    prompt: str = "",
    max_soft_tokens: int | None = None,
) -> str:
    """Describe the current viewport (BioCLIP top-k species classification or Gemma4 VLM)."""
    try:
        from tools.detectors import caption

        from tools.sim_ptz_watch import sleep_after_inference

        viewport = _cam()._crop_viewport()
        ckw = {}
        if model == "gemma4":
            if prompt.strip():
                ckw["prompt"] = prompt.strip()
            if max_soft_tokens is not None:
                ckw["max_soft_tokens"] = int(max_soft_tokens)
        out = caption(viewport, model=model, **ckw)
        sleep_after_inference()
        return _json(out)
    except Exception as e:
        return _json({"error": str(e), "caption": ""})


@mcp.tool()
def msa_ptz_run_mission(
    mission: str,
    model: str = "",
    max_pan_stops: int = 48,
    random_views: int = 10,
    pan_step_ratio: float = 0.82,
    tilt_step_ratio: float = 0.82,
    max_tilt_rows: int = 64,
    max_total_stops: int = 512,
) -> str:
    """
    Run an agentic panorama mission (natural language). Sweeps pan or random views,
    full pan/tilt grid if the mission requests it, runs detection, deduplicates.
    Blocks until done — can be slow. Examples: 'scan for all animals', 'grid scan',
    'horizontal and vertical from top left', 'animals near water' (semantic scene).
    Model: empty for parsed default, or yolo | bioclip | gemma_scene.
    """
    try:
        from tools.ptz_mission import run_mission

        kw = dict(
            mission=mission,
            max_pan_stops=max_pan_stops,
            random_views=random_views,
            pan_step_ratio=pan_step_ratio,
            tilt_step_ratio=tilt_step_ratio,
            max_tilt_rows=max_tilt_rows,
            max_total_stops=max_total_stops,
        )
        if model.strip():
            kw["model"] = model.strip()
        return _json(run_mission(**kw))
    except Exception as e:
        return _json({"ok": False, "error": str(e)})


@mcp.tool()
def msa_ptz_snapshot(filename: str = "") -> str:
    """Save current viewport JPEG to project root. Returns file path."""
    try:
        cam = _cam()
        path = cam.snapshot(filename or None)
        return _json({"path": path, "ok": True})
    except Exception as e:
        return _json({"error": str(e), "ok": False})


@mcp.tool()
def msa_ptz_overview(filename: str = "") -> str:
    """Save panorama thumbnail with viewport rectangle. Returns path."""
    try:
        cam = _cam()
        path = cam.overview(filename or None)
        return _json({"path": path, "ok": True})
    except Exception as e:
        return _json({"error": str(e), "ok": False})


@mcp.tool()
def msa_ptz_detection_models() -> str:
    """Return which backends are importable (yolo, bioclip)."""
    try:
        from tools.detectors import available_models

        return _json(available_models())
    except Exception as e:
        return _json({"error": str(e)})


@mcp.tool()
def msa_ptz_server_info() -> str:
    """Project root, panorama paths, state file, and how to start the web viewer."""
    port = os.environ.get("MSA_PTZ_VIEWER_PORT", "8088")
    pano = _ROOT / "stitched.png"
    state = _ROOT / "scratchpads" / "sim_ptz_state.json"
    return _json(
        {
            "project_root": str(_ROOT),
            "default_panorama": str(pano),
            "state_file": str(state),
            "web_viewer_hint": (
                f"From project root: python3 tools/ptz_viewer.py --port {port} "
                f"then open http://127.0.0.1:{port}"
            ),
            "mcp_server": "msa_mcp/ptz_server.py (stdio)",
        }
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
