"""Tests for campaign_agent tools and server."""

import asyncio
import csv
import json
from pathlib import Path

import pytest

from campaign_agent.tools import (
    _campaign_to_dict,
    load_campaigns_tool,
    get_top_campaigns_tool,
    get_summary_tool,
    save_report_tool,
)
from campaign_agent.server import create_campaign_server, SERVER_NAME, SERVER_VERSION
from campaign_analyzer import Campaign


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None):
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


CLEAN_ROWS = [
    {"campaign_name": "Alpha", "impressions": 10000, "clicks": 200, "spend": 500.0, "revenue": 2000.0},
    {"campaign_name": "Beta", "impressions": 20000, "clicks": 400, "spend": 800.0, "revenue": 2400.0},
    {"campaign_name": "Gamma", "impressions": 5000, "clicks": 50, "spend": 300.0, "revenue": 900.0},
]


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _parse_result(result: dict) -> dict:
    """Extract and parse JSON from a tool success response."""
    text = result["content"][0]["text"]
    return json.loads(text)


# ──────────────────────────────────────────────
# _campaign_to_dict
# ──────────────────────────────────────────────

class TestCampaignToDict:
    def test_includes_computed_fields(self):
        c = Campaign("Test", impressions=10000, clicks=200, spend=500.0, revenue=2000.0)
        d = _campaign_to_dict(c)
        assert d["roas"] == pytest.approx(4.0)
        assert d["computed_ctr"] == pytest.approx(2.0)

    def test_zero_spend(self):
        c = Campaign("Zero", impressions=5000, clicks=50, spend=0.0, revenue=0.0)
        d = _campaign_to_dict(c)
        assert d["roas"] is None

    def test_all_fields_present(self):
        c = Campaign("X", 1000, 10, 50.0, 200.0, reported_ctr=1.0, source_file="f.csv")
        d = _campaign_to_dict(c)
        expected_keys = {
            "campaign_name", "impressions", "clicks", "spend", "revenue",
            "roas", "computed_ctr", "reported_ctr", "source_file",
        }
        assert set(d.keys()) == expected_keys


# ──────────────────────────────────────────────
# load_campaigns tool
# ──────────────────────────────────────────────

class TestLoadCampaignsTool:
    def test_valid_directory(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        result = _run(load_campaigns_tool.handler({"directory": str(tmp_path)}))
        assert "is_error" not in result
        data = _parse_result(result)
        assert data["campaign_count"] == 3
        assert len(data["campaigns"]) == 3
        assert data["warning_count"] == 0

    def test_nonexistent_directory(self):
        result = _run(load_campaigns_tool.handler({"directory": "/nonexistent/path"}))
        assert result["is_error"] is True
        assert "not found" in result["content"][0]["text"].lower()

    def test_no_csv_files(self, tmp_path):
        result = _run(load_campaigns_tool.handler({"directory": str(tmp_path)}))
        assert result["is_error"] is True
        assert "no csv" in result["content"][0]["text"].lower()

    def test_warnings_included(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Bad CTR", "impressions": 10000, "clicks": 100,
             "spend": 500.0, "revenue": 2000.0, "ctr": "50.0"},
        ])
        result = _run(load_campaigns_tool.handler({"directory": str(tmp_path)}))
        data = _parse_result(result)
        assert data["warning_count"] > 0
        assert len(data["warnings"]) > 0


# ──────────────────────────────────────────────
# get_top_campaigns tool
# ──────────────────────────────────────────────

class TestGetTopCampaignsTool:
    def test_default_top(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        result = _run(get_top_campaigns_tool.handler({"directory": str(tmp_path)}))
        data = _parse_result(result)
        assert len(data["ranked"]) == 3
        # Alpha has highest ROAS (2000/500 = 4.0x)
        assert data["ranked"][0]["campaign_name"] == "Alpha"
        assert data["ranked"][0]["rank"] == 1

    def test_custom_top(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        result = _run(get_top_campaigns_tool.handler({"directory": str(tmp_path), "top": 1}))
        data = _parse_result(result)
        assert len(data["ranked"]) == 1

    def test_min_spend_filter(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        # Gamma has spend=300, should be excluded with min_spend=400
        result = _run(get_top_campaigns_tool.handler({
            "directory": str(tmp_path), "min_spend": 400.0,
        }))
        data = _parse_result(result)
        names = [r["campaign_name"] for r in data["ranked"]]
        assert "Gamma" not in names

    def test_zero_spend_excluded(self, tmp_path):
        rows = CLEAN_ROWS + [
            {"campaign_name": "Zero", "impressions": 5000, "clicks": 50, "spend": 0.0, "revenue": 0.0},
        ]
        write_csv(tmp_path / "data.csv", rows)
        result = _run(get_top_campaigns_tool.handler({"directory": str(tmp_path)}))
        data = _parse_result(result)
        ranked_names = [r["campaign_name"] for r in data["ranked"]]
        assert "Zero" not in ranked_names
        assert len(data["excluded_zero_spend"]) == 1
        assert data["excluded_zero_spend"][0]["campaign_name"] == "Zero"

    def test_nonexistent_directory(self):
        result = _run(get_top_campaigns_tool.handler({"directory": "/nonexistent"}))
        assert result["is_error"] is True


# ──────────────────────────────────────────────
# get_summary tool
# ──────────────────────────────────────────────

class TestGetSummaryTool:
    def test_aggregates(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        result = _run(get_summary_tool.handler({"directory": str(tmp_path)}))
        data = _parse_result(result)
        assert data["campaign_count"] == 3
        assert data["total_spend"] == pytest.approx(1600.0)
        assert data["total_revenue"] == pytest.approx(5300.0)
        assert data["overall_roas"] == pytest.approx(5300.0 / 1600.0)
        assert data["total_clicks"] == 650
        assert data["total_impressions"] == 35000
        expected_ctr = 650 / 35000 * 100
        assert data["blended_ctr"] == pytest.approx(expected_ctr)

    def test_nonexistent_directory(self):
        result = _run(get_summary_tool.handler({"directory": "/nonexistent"}))
        assert result["is_error"] is True


# ──────────────────────────────────────────────
# save_report tool
# ──────────────────────────────────────────────

class TestSaveReportTool:
    def test_creates_csv(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        output = tmp_path / "report.csv"
        result = _run(save_report_tool.handler({
            "directory": str(tmp_path),
            "output_path": str(output),
        }))
        data = _parse_result(result)
        assert data["status"] == "ok"
        assert output.exists()

    def test_csv_content(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        output = tmp_path / "report.csv"
        _run(save_report_tool.handler({
            "directory": str(tmp_path),
            "output_path": str(output),
            "top": 2,
        }))
        with output.open() as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["campaign_name"] == "Alpha"

    def test_invalid_output_path(self, tmp_path):
        write_csv(tmp_path / "data.csv", CLEAN_ROWS)
        result = _run(save_report_tool.handler({
            "directory": str(tmp_path),
            "output_path": "/nonexistent/dir/report.csv",
        }))
        assert result["is_error"] is True

    def test_nonexistent_directory(self, tmp_path):
        result = _run(save_report_tool.handler({
            "directory": "/nonexistent",
            "output_path": str(tmp_path / "report.csv"),
        }))
        assert result["is_error"] is True


# ──────────────────────────────────────────────
# Server factory
# ──────────────────────────────────────────────

class TestCreateCampaignServer:
    def test_server_created(self):
        server = create_campaign_server()
        assert server is not None

    def test_server_name_and_version(self):
        assert SERVER_NAME == "campaign_analyzer"
        assert SERVER_VERSION == "1.0.0"
