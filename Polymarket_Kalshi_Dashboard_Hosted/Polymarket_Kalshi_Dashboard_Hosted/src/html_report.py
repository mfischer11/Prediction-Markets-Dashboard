"""
Renders the dashboard as a self-contained HTML string: platform-tagged
cards, a static chart image for binary markets, a probability table for
multi-outcome markets, Bid x Ask, 24h/7d change - the same visual
language as the Excel version, mirrored in src/excel_report.py.

Every element in the report content itself carries inline styles
(style="...") rather than relying on a <style> block. This is
deliberate: it's what makes "select the report, copy, paste into
Outlook" reliable - a browser's copy-to-clipboard operation generally
preserves inline styles far more faithfully than rules from a separate
stylesheet, and Outlook's own HTML rendering (especially classic desktop
Outlook, which uses Word's rendering engine) has historically poor
support for anything beyond basic inline-styled tables. The dashboard
"chrome" around the report (page background, nav) can use a <style>
block freely since that part is never meant to be copied.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import List

from .chart_image import render_probability_chart_png, png_to_data_uri
from .models import DisplayType, MarketResult, MarketStatus
from .data_manager import RunStats

FONT_STACK = "Calibri, 'Segoe UI', Arial, sans-serif"

PLATFORM_ACCENT = {"Polymarket": "1652F0", "Kalshi": "00A67E"}
PLATFORM_ACCENT_LIGHT = {"Polymarket": "E8EDFF", "Kalshi": "E1F5EE"}


def _accent(platform: str) -> str:
    return PLATFORM_ACCENT.get(platform, "374151")


def _accent_light(platform: str) -> str:
    return PLATFORM_ACCENT_LIGHT.get(platform, "F3F4F6")


def _prob_color(prob) -> str:
    if prob is None:
        return "6B7280"
    if prob >= 0.66:
        return "0E8A3E"
    if prob >= 0.33:
        return "B45309"
    return "B42318"


def _change_text_and_color(value):
    """value is a probability-point delta (0..1 scale) or None. Returns
    (display_text, color_hex) - a plain signed number in percentage
    points ("+3.0" / "-3.0"), never a percent-of-a-percent ("-3.0%")."""
    if value is None:
        return "N/A", "9CA3AF"
    pct = round(value * 100, 1)
    if pct > 0.05:
        return f"+{pct:.1f}", "0E8A3E"
    if pct < -0.05:
        return f"{pct:.1f}", "B42318"
    return f"{pct:.1f}", "6B7280"


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def build_dashboard_html(results: List[MarketResult], stats: RunStats,
                          embed_page_chrome: bool = True) -> str:
    """Returns the full dashboard as an HTML string. If embed_page_chrome
    is False, returns just the report content fragment (used when the
    Flask app wraps it in its own page template)."""
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    cards = []
    for result in results:
        cards.append(_render_market_card(result))
    cards_html = "\n".join(cards)

    report_html = f"""
<div id="report-content" style="font-family:{FONT_STACK}; background:#FFFFFF; padding:24px; max-width:900px;">
  <div style="font-size:26px; font-weight:700; color:#14161A; margin-bottom:2px;">Prediction Market Daily Report</div>
  <div style="font-size:14px; font-weight:700; color:#44546A; margin-bottom:18px;">Generated {run_time} &bull; {len(results)} markets</div>
  {cards_html}
</div>
"""

    if not embed_page_chrome:
        return report_html

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Prediction Market Daily Report</title>
</head>
<body style="margin:0; padding:20px; background:#F3F4F6;">
{report_html}
</body>
</html>"""


def _status_line(mkt) -> str:
    parts = [f"Status: {mkt.status.value}"]
    if mkt.volume is not None:
        parts.append(f"Vol: ${mkt.volume:,.0f}")
    if mkt.volume_24hr is not None:
        parts.append(f"24h: ${mkt.volume_24hr:,.0f}")
    if mkt.end_time is not None:
        parts.append(f"Ends {mkt.end_time.strftime('%Y-%m-%d')}")
    parts.append(f"Range {mkt.time_range}")
    return "   &bull;   ".join(parts)


def _render_market_card(result: MarketResult) -> str:
    cfg = result.config_row
    accent = _accent(cfg.platform)
    accent_light = _accent_light(cfg.platform)
    title = cfg.title_override or (result.market.title if result.market else "") or cfg.url

    header = f"""
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 0 0; border-collapse:collapse;">
    <tr>
      <td style="background:#{accent}; color:#FFFFFF; font-family:{FONT_STACK}; font-weight:700; font-size:12px; text-align:center; padding:8px 14px; white-space:nowrap;">
        {_esc(cfg.platform.upper())}
      </td>
      <td style="background:#{accent_light}; color:#14161A; font-family:{FONT_STACK}; font-weight:700; font-size:16px; padding:8px 14px; width:100%;">
        {_esc(title)}
      </td>
    </tr>
  </table>
"""

    if result.market is None or result.status in (
        MarketStatus.NOT_FOUND, MarketStatus.INVALID_URL, MarketStatus.API_ERROR,
    ):
        body = f"""
  <div style="font-family:{FONT_STACK}; padding:10px 4px 0 4px;">
    <div style="font-weight:700; font-size:13px; color:#9C0006;">MARKET UNAVAILABLE ({_esc(result.status.value)})</div>
    <div style="font-style:italic; font-size:12px; color:#9C0006; margin-top:2px;">{_esc(result.status_detail or "")}</div>
    <div style="font-size:11px; color:#6B7280; margin-top:4px;">URL: {_esc(cfg.url)}</div>
  </div>
"""
        return f'<div style="margin-bottom:22px;">{header}{body}</div>'

    mkt = result.market
    meta = f"""
  <div style="font-family:{FONT_STACK}; font-size:12px; font-weight:700; color:#44546A; padding:8px 4px 10px 4px;">
    {_status_line(mkt)}
  </div>
"""

    if mkt.resolved_display_type() == DisplayType.CHART:
        body = _render_chart_block(mkt, accent)
    else:
        body = _render_table_block(mkt, accent)

    return f'<div style="margin-bottom:26px;">{header}{meta}{body}</div>'


def _render_chart_block(mkt, accent: str) -> str:
    prob = mkt.current_probability()
    prob_str = f"{prob:.0%} Chance" if prob is not None else "N/A Chance"
    prob_color = _prob_color(prob)

    outcomes = mkt.outcomes
    primary = outcomes[0] if outcomes else None
    ch24_text, ch24_color = _change_text_and_color(primary.change_24h if primary else None)
    ch7d_text, ch7d_color = _change_text_and_color(primary.change_7d if primary else None)

    headline = f"""
  <div style="font-family:{FONT_STACK}; padding:0 4px;">
    <div style="font-size:30px; font-weight:700; color:#{prob_color}; margin-bottom:4px;">{_esc(prob_str)}</div>
    <div style="font-size:14px; font-weight:700; margin-bottom:10px;">
      <span style="color:#{ch24_color};">24h&nbsp;&nbsp;{_esc(ch24_text)}</span>
      <span style="display:inline-block; width:28px;"></span>
      <span style="color:#{ch7d_color};">7d&nbsp;&nbsp;{_esc(ch7d_text)}</span>
    </div>
  </div>
"""

    if len(mkt.historical_series) < 2:
        return headline + f'<div style="font-family:{FONT_STACK}; font-style:italic; font-size:12px; color:#6B7280; padding:0 4px;">(Not enough historical data for a chart yet)</div>'

    png_bytes = render_probability_chart_png(mkt.historical_series, accent)
    if png_bytes is None:
        return headline

    data_uri = png_to_data_uri(png_bytes)
    chart_html = f'<div style="padding:4px;"><img src="{data_uri}" alt="probability chart" style="max-width:100%; height:auto; display:block;"></div>'
    return headline + chart_html


def _render_table_block(mkt, accent: str) -> str:
    headers = ["Outcome", "Probability", "24h Chg", "7d Chg", "Price", "Bid x Ask", "Volume"]
    aligns = ["left", "right", "right", "right", "right", "right", "right"]

    header_cells = "".join(
        f'<th style="background:#{accent}; color:#FFFFFF; font-family:{FONT_STACK}; '
        f'font-weight:700; font-size:12px; text-align:{a}; padding:6px 10px; '
        f'border:1px solid #D0D3D8;">{_esc(h)}</th>'
        for h, a in zip(headers, aligns)
    )

    rows_html = []
    for outcome in mkt.sorted_outcomes():
        prob = outcome.probability
        prob_color = _prob_color(prob)
        prob_text = f"{prob:.1%}" if prob is not None else ""

        ch24_text, ch24_color = _change_text_and_color(outcome.change_24h)
        ch7d_text, ch7d_color = _change_text_and_color(outcome.change_7d)

        price_text = f"{prob * 100:.0f}&cent;" if prob is not None else ""

        if outcome.bid is not None and outcome.ask is not None:
            bidask_text = f"{outcome.bid:.2f}x{outcome.ask:.2f}"
        else:
            bidask_text = "&mdash;"

        vol_text = f"{outcome.volume:,.0f}" if outcome.volume is not None else ""

        # Simple bar simulated with a background gradient sized to
        # probability - kept as a plain inline background-color (not a
        # gradient) for maximum paste compatibility; the colored percent
        # text itself carries the at-a-glance signal.
        cell = (
            f'<td style="font-family:{FONT_STACK}; font-weight:700; font-size:13px; '
            f'color:#14161A; padding:6px 10px; border:1px solid #D0D3D8; text-align:left;">{_esc(outcome.name)}</td>'
            f'<td style="font-family:{FONT_STACK}; font-weight:700; font-size:13px; '
            f'color:#{prob_color}; padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{prob_text}</td>'
            f'<td style="font-family:{FONT_STACK}; font-weight:700; font-size:12px; '
            f'color:#{ch24_color}; padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{_esc(ch24_text)}</td>'
            f'<td style="font-family:{FONT_STACK}; font-weight:700; font-size:12px; '
            f'color:#{ch7d_color}; padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{_esc(ch7d_text)}</td>'
            f'<td style="font-family:{FONT_STACK}; font-size:12px; color:#44546A; '
            f'padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{price_text}</td>'
            f'<td style="font-family:{FONT_STACK}; font-size:12px; color:#44546A; '
            f'padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{bidask_text}</td>'
            f'<td style="font-family:{FONT_STACK}; font-size:12px; color:#44546A; '
            f'padding:6px 10px; border:1px solid #D0D3D8; text-align:right;">{vol_text}</td>'
        )
        rows_html.append(f"<tr>{cell}</tr>")

    table = f"""
  <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse; margin:0 4px 4px 4px; width:calc(100% - 8px);">
    <tr>{header_cells}</tr>
    {''.join(rows_html)}
  </table>
"""
    return table
