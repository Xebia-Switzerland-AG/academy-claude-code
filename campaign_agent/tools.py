"""MCP tools that wrap campaign_analyzer functionality."""

import json
from pathlib import Path
from typing import Any

import click

from claude_agent_sdk import tool, ToolAnnotations
from campaign_analyzer import Campaign, load_campaigns, _write_csv_report


def _campaign_to_dict(c: Campaign) -> dict[str, Any]:
    """Serialize a Campaign to a plain dict with computed fields."""
    return {
        "campaign_name": c.campaign_name,
        "impressions": c.impressions,
        "clicks": c.clicks,
        "spend": c.spend,
        "revenue": c.revenue,
        "roas": c.roas,
        "computed_ctr": c.computed_ctr,
        "reported_ctr": c.reported_ctr,
        "source_file": c.source_file,
    }


def _success_response(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error_response(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "is_error": True}


@tool(
    "load_campaigns",
    "Load and validate campaign data from all CSV files in a directory. "
    "Returns the full list of campaigns with computed metrics and any data quality warnings.",
    {"directory": str},
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def load_campaigns_tool(args: dict[str, Any]) -> dict[str, Any]:
    directory = Path(args["directory"]).resolve()
    if not directory.is_dir():
        return _error_response(f"Directory not found: {directory}")
    try:
        campaigns, warnings = load_campaigns(directory)
    except click.ClickException as e:
        return _error_response(e.format_message())

    return _success_response({
        "campaign_count": len(campaigns),
        "campaigns": [_campaign_to_dict(c) for c in campaigns],
        "warning_count": len(warnings),
        "warnings": [
            {"campaign": w.campaign, "file": w.file, "message": w.message}
            for w in warnings
        ],
    })


@tool(
    "get_top_campaigns",
    "Rank campaigns by ROAS (Return on Ad Spend) and return the top N performers. "
    "Campaigns with zero spend are excluded from the ranking.",
    {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "Path to directory containing campaign CSV files"},
            "top": {"type": "integer", "description": "Number of top performers to return (default 10)"},
            "min_spend": {"type": "number", "description": "Exclude campaigns with spend below this threshold (default 0)"},
        },
        "required": ["directory"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_top_campaigns_tool(args: dict[str, Any]) -> dict[str, Any]:
    directory = Path(args["directory"]).resolve()
    top = args.get("top", 10)
    min_spend = args.get("min_spend", 0.0)

    if not directory.is_dir():
        return _error_response(f"Directory not found: {directory}")
    try:
        campaigns, warnings = load_campaigns(directory)
    except click.ClickException as e:
        return _error_response(e.format_message())

    if min_spend > 0:
        campaigns = [c for c in campaigns if c.spend >= min_spend]

    ranked = sorted(
        (c for c in campaigns if c.roas is not None),
        key=lambda c: c.roas,
        reverse=True,
    )
    zero_spend = [c for c in campaigns if c.roas is None]

    return _success_response({
        "ranked": [
            {"rank": i, **_campaign_to_dict(c)}
            for i, c in enumerate(ranked[:top], start=1)
        ],
        "total_with_spend": len(ranked),
        "excluded_zero_spend": [
            {"campaign_name": c.campaign_name, "source_file": c.source_file}
            for c in zero_spend
        ],
        "warning_count": len(warnings),
    })


@tool(
    "get_summary",
    "Return aggregate statistics for all campaigns in a directory: "
    "total spend, total revenue, overall ROAS, total clicks, total impressions, and blended CTR.",
    {"directory": str},
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_summary_tool(args: dict[str, Any]) -> dict[str, Any]:
    directory = Path(args["directory"]).resolve()
    if not directory.is_dir():
        return _error_response(f"Directory not found: {directory}")
    try:
        campaigns, warnings = load_campaigns(directory)
    except click.ClickException as e:
        return _error_response(e.format_message())

    total_spend = sum(c.spend for c in campaigns)
    total_revenue = sum(c.revenue for c in campaigns)
    total_clicks = sum(c.clicks for c in campaigns)
    total_impressions = sum(c.impressions for c in campaigns)
    overall_roas = total_revenue / total_spend if total_spend else None
    overall_ctr = (total_clicks / total_impressions * 100) if total_impressions else None

    return _success_response({
        "campaign_count": len(campaigns),
        "total_spend": total_spend,
        "total_revenue": total_revenue,
        "overall_roas": overall_roas,
        "total_clicks": total_clicks,
        "total_impressions": total_impressions,
        "blended_ctr": overall_ctr,
        "warning_count": len(warnings),
    })


@tool(
    "save_report",
    "Write a CSV report of the top N campaigns (ranked by ROAS) to a file.",
    {
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "Path to directory containing campaign CSV files"},
            "output_path": {"type": "string", "description": "Path for the output CSV file"},
            "top": {"type": "integer", "description": "Number of top performers to include (default 10)"},
        },
        "required": ["directory", "output_path"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
)
async def save_report_tool(args: dict[str, Any]) -> dict[str, Any]:
    directory = Path(args["directory"]).resolve()
    output_path = Path(args["output_path"]).resolve()
    top = args.get("top", 10)

    if not directory.is_dir():
        return _error_response(f"Directory not found: {directory}")
    try:
        campaigns, _ = load_campaigns(directory)
        _write_csv_report(campaigns, top, output_path)
    except click.ClickException as e:
        return _error_response(e.format_message())
    except OSError as e:
        return _error_response(f"Cannot write report: {e}")

    return _success_response({"status": "ok", "output_path": str(output_path)})
