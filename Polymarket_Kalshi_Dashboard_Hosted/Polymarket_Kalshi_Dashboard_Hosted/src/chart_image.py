"""
Renders a probability-over-time line chart to a PNG, base64-encoded for
inline embedding in the dashboard HTML (<img src="data:image/png;base64,...">).

A static image (not a JS-rendered chart) is deliberate: it's what makes
"select-all, copy, paste into Outlook" work reliably - a rendered <img>
becomes real pixel data on the clipboard when a browser copies it, the
same way copying any picture from a web page does. A live JS chart
(Canvas/SVG) would not survive that trip nearly as reliably, especially
into Outlook's notoriously quirky HTML rendering.

Uses matplotlib's AutoDateLocator + ConciseDateFormatter for the x-axis
instead of hand-rolled "label only at month transitions" logic - this is
well-tested, general-purpose date-axis spacing that handles a 6-hour
chart and a 90-day chart equally well without per-range special-casing
(the custom version of this logic caused real, hard-to-catch bugs in the
Excel/VBA builds of this project).
"""
from __future__ import annotations

import base64
import io
from typing import List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

MAX_CHART_POINTS = 80
AXIS_GRAY = "#44546A"
SPINE_GRAY = "#D0D3D8"


def _downsample(points: List, max_points: int = MAX_CHART_POINTS) -> List:
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    indices = sorted({round(i * step) for i in range(max_points)})
    return [points[i] for i in indices]


def render_probability_chart_png(points: List, accent_hex: str) -> Optional[bytes]:
    """points: list of HistoryPoint (with .timestamp / .probability).
    Returns PNG bytes, or None if there aren't enough points for a chart."""
    if len(points) < 2:
        return None
    points = _downsample(points)
    xs = [p.timestamp for p in points]
    ys = [p.probability * 100 for p in points]

    fig, ax = plt.subplots(figsize=(7.6, 3.0), dpi=160)
    ax.plot(xs, ys, color=f"#{accent_hex}", linewidth=2.8, solid_capstyle="round",
            solid_joinstyle="round")

    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 10))
    ax.set_yticklabels([f"{t}%" for t in range(0, 101, 10)])
    for label in ax.get_yticklabels():
        label.set_fontsize(11)
        label.set_fontweight("bold")
        label.set_color(AXIS_GRAY)

    locator = AutoDateLocator(minticks=4, maxticks=8)
    formatter = ConciseDateFormatter(locator, show_offset=False)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    for label in ax.get_xticklabels():
        label.set_fontsize(11)
        label.set_fontweight("bold")
        label.set_color(AXIS_GRAY)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(SPINE_GRAY)
    ax.spines["bottom"].set_color(SPINE_GRAY)
    ax.grid(False)
    ax.tick_params(axis="both", length=0)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    fig.tight_layout(pad=0.8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def png_to_data_uri(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
