"""Tests for campaign_analyzer.py"""

import csv
import pytest
from pathlib import Path
from click.testing import CliRunner
from click import ClickException

from campaign_analyzer import (
    Campaign,
    ValidationWarning,
    CTR_TOLERANCE,
    load_campaigns,
    _validate_ctr,
    _parse_float,
    _parse_int,
    _roas_str,
    _fmt_currency,
    main,
)


# ──────────────────────────────────────────────
# Campaign dataclass
# ──────────────────────────────────────────────

class TestCampaignRoas:
    def test_normal(self):
        c = Campaign("Test", 10000, 100, spend=50.0, revenue=200.0)
        assert c.roas == pytest.approx(4.0)

    def test_zero_spend_returns_none(self):
        c = Campaign("Test", 10000, 100, spend=0.0, revenue=0.0)
        assert c.roas is None

    def test_zero_revenue(self):
        c = Campaign("Test", 10000, 100, spend=100.0, revenue=0.0)
        assert c.roas == pytest.approx(0.0)


class TestCampaignComputedCtr:
    def test_normal(self):
        c = Campaign("Test", impressions=10000, clicks=200, spend=100.0, revenue=500.0)
        assert c.computed_ctr == pytest.approx(2.0)

    def test_zero_impressions_returns_none(self):
        c = Campaign("Test", impressions=0, clicks=0, spend=100.0, revenue=500.0)
        assert c.computed_ctr is None

    def test_high_ctr(self):
        c = Campaign("Test", impressions=100, clicks=50, spend=10.0, revenue=50.0)
        assert c.computed_ctr == pytest.approx(50.0)


# ──────────────────────────────────────────────
# Parsing helpers
# ──────────────────────────────────────────────

class TestParseFloat:
    def test_valid(self):
        assert _parse_float("3.14", "spend", 2, "test.csv") == pytest.approx(3.14)

    def test_integer_string(self):
        assert _parse_float("100", "spend", 2, "test.csv") == pytest.approx(100.0)

    def test_invalid_raises(self):
        with pytest.raises(ClickException):
            _parse_float("abc", "spend", 2, "test.csv")

    def test_empty_raises(self):
        with pytest.raises(ClickException):
            _parse_float("", "spend", 2, "test.csv")


class TestParseInt:
    def test_valid_integer(self):
        assert _parse_int("1000", "impressions", 2, "test.csv") == 1000

    def test_float_style_integer(self):
        assert _parse_int("1000.0", "impressions", 2, "test.csv") == 1000

    def test_invalid_raises(self):
        with pytest.raises(ClickException):
            _parse_int("abc", "impressions", 2, "test.csv")


# ──────────────────────────────────────────────
# _validate_ctr
# ──────────────────────────────────────────────

def make_campaign(**kwargs):
    defaults = dict(
        campaign_name="Test Campaign",
        impressions=10000,
        clicks=100,
        spend=500.0,
        revenue=2000.0,
        reported_ctr=None,
        source_file="test.csv",
    )
    defaults.update(kwargs)
    return Campaign(**defaults)


class TestValidateCtr:
    def test_clean_campaign_no_warnings(self):
        c = make_campaign()
        assert _validate_ctr(c, 2, "test.csv") == []

    def test_negative_clicks(self):
        c = make_campaign(clicks=-1)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("negative clicks" in w.message for w in warnings)

    def test_negative_impressions(self):
        c = make_campaign(impressions=-1)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("negative impressions" in w.message for w in warnings)

    def test_clicks_exceed_impressions(self):
        c = make_campaign(impressions=100, clicks=200)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("exceed impressions" in w.message for w in warnings)

    def test_clicks_equal_impressions_no_warning(self):
        c = make_campaign(impressions=100, clicks=100)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert not any("exceed impressions" in w.message for w in warnings)

    def test_negative_spend(self):
        c = make_campaign(spend=-10.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("negative spend" in w.message for w in warnings)

    def test_negative_revenue(self):
        c = make_campaign(revenue=-10.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("negative revenue" in w.message for w in warnings)

    def test_ctr_out_of_range_high(self):
        c = make_campaign(reported_ctr=101.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("outside [0, 100]" in w.message for w in warnings)

    def test_ctr_out_of_range_negative(self):
        c = make_campaign(reported_ctr=-1.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("outside [0, 100]" in w.message for w in warnings)

    def test_ctr_mismatch_beyond_tolerance(self):
        # computed_ctr = 100/10000 * 100 = 1.0%; reported = 5.0%; diff = 4pp > 1pp
        c = make_campaign(impressions=10000, clicks=100, reported_ctr=5.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("differs from computed" in w.message for w in warnings)

    def test_ctr_within_tolerance_no_warning(self):
        # computed_ctr = 100/10000 * 100 = 1.0%; reported = 1.5%; diff = 0.5pp < 1pp
        c = make_campaign(impressions=10000, clicks=100, reported_ctr=1.5)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert not any("differs from computed" in w.message for w in warnings)

    def test_ctr_exact_match_no_warning(self):
        # computed_ctr = 100/10000 * 100 = 1.0%; reported = 1.0%
        c = make_campaign(impressions=10000, clicks=100, reported_ctr=1.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert not any("differs from computed" in w.message for w in warnings)

    def test_ctr_none_reported_no_mismatch_warning(self):
        c = make_campaign(reported_ctr=None)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert not any("differs from computed" in w.message for w in warnings)

    def test_ctr_out_of_range_does_not_also_check_mismatch(self):
        # When CTR is out of range, only the out-of-range warning should appear
        c = make_campaign(impressions=10000, clicks=100, reported_ctr=150.0)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert any("outside [0, 100]" in w.message for w in warnings)
        assert not any("differs from computed" in w.message for w in warnings)

    def test_warning_campaign_name_populated(self):
        c = make_campaign(clicks=-1)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert warnings[0].campaign == "Test Campaign"

    def test_warning_file_populated(self):
        c = make_campaign(clicks=-1)
        warnings = _validate_ctr(c, 2, "test.csv")
        assert warnings[0].file == "test.csv"


# ──────────────────────────────────────────────
# load_campaigns
# ──────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None):
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class TestLoadCampaigns:
    def test_no_csv_files_raises(self, tmp_path):
        with pytest.raises(ClickException):
            load_campaigns(tmp_path)

    def test_missing_required_column_raises(self, tmp_path):
        # Missing 'revenue'
        write_csv(tmp_path / "bad.csv", [
            {"campaign_name": "X", "impressions": 100, "clicks": 5, "spend": 10.0}
        ])
        with pytest.raises(ClickException, match="missing required columns"):
            load_campaigns(tmp_path)

    def test_empty_file_produces_warning(self, tmp_path):
        (tmp_path / "empty.csv").write_text("")
        _, warnings = load_campaigns(tmp_path)
        assert any("empty" in w.message.lower() or "no header" in w.message.lower() for w in warnings)

    def test_empty_campaign_name_skipped_with_warning(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "", "impressions": 1000, "clicks": 10, "spend": 50.0, "revenue": 200.0},
            {"campaign_name": "Valid", "impressions": 1000, "clicks": 10, "spend": 50.0, "revenue": 200.0},
        ])
        campaigns, warnings = load_campaigns(tmp_path)
        assert len(campaigns) == 1
        assert campaigns[0].campaign_name == "Valid"
        assert any("skipped" in w.message for w in warnings)

    def test_clean_data_loads_correctly(self, tmp_path):
        write_csv(tmp_path / "clean.csv", [
            {"campaign_name": "Alpha", "impressions": 10000, "clicks": 200, "spend": 500.0, "revenue": 2000.0},
            {"campaign_name": "Beta",  "impressions": 20000, "clicks": 400, "spend": 800.0, "revenue": 2400.0},
        ])
        campaigns, warnings = load_campaigns(tmp_path)
        assert len(campaigns) == 2
        assert warnings == []
        names = {c.campaign_name for c in campaigns}
        assert names == {"Alpha", "Beta"}

    def test_roas_computed_correctly(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Test", "impressions": 10000, "clicks": 100, "spend": 500.0, "revenue": 2500.0},
        ])
        campaigns, _ = load_campaigns(tmp_path)
        assert campaigns[0].roas == pytest.approx(5.0)

    def test_zero_spend_campaign_loaded(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Zero", "impressions": 5000, "clicks": 50, "spend": 0.0, "revenue": 0.0},
        ])
        campaigns, _ = load_campaigns(tmp_path)
        assert len(campaigns) == 1
        assert campaigns[0].roas is None

    def test_optional_ctr_column_parsed(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Test", "impressions": 10000, "clicks": 100, "spend": 500.0, "revenue": 2000.0, "ctr": "1.0"},
        ])
        campaigns, _ = load_campaigns(tmp_path)
        assert campaigns[0].reported_ctr == pytest.approx(1.0)

    def test_ctr_mismatch_produces_warning(self, tmp_path):
        # computed CTR = 100/10000 * 100 = 1.0%; reported = 5.0% → diff 4pp > 1pp
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Bad CTR", "impressions": 10000, "clicks": 100, "spend": 500.0, "revenue": 2000.0, "ctr": "5.0"},
        ])
        campaigns, warnings = load_campaigns(tmp_path)
        assert len(campaigns) == 1
        assert any("differs from computed" in w.message for w in warnings)

    def test_multiple_csv_files_loaded(self, tmp_path):
        write_csv(tmp_path / "file1.csv", [
            {"campaign_name": "A", "impressions": 1000, "clicks": 10, "spend": 50.0, "revenue": 200.0},
        ])
        write_csv(tmp_path / "file2.csv", [
            {"campaign_name": "B", "impressions": 2000, "clicks": 20, "spend": 100.0, "revenue": 300.0},
        ])
        campaigns, _ = load_campaigns(tmp_path)
        assert len(campaigns) == 2

    def test_source_file_set_on_campaign(self, tmp_path):
        write_csv(tmp_path / "myfile.csv", [
            {"campaign_name": "Test", "impressions": 1000, "clicks": 10, "spend": 50.0, "revenue": 200.0},
        ])
        campaigns, _ = load_campaigns(tmp_path)
        assert campaigns[0].source_file == "myfile.csv"

    def test_negative_clicks_produces_warning(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Bad", "impressions": 1000, "clicks": -5, "spend": 50.0, "revenue": 200.0},
        ])
        _, warnings = load_campaigns(tmp_path)
        assert any("negative clicks" in w.message for w in warnings)

    def test_sample_data_directory(self):
        """Integration: sample_data loads with expected campaigns and some warnings."""
        sample_dir = Path(__file__).parent.parent / "sample_data"
        campaigns, warnings = load_campaigns(sample_dir)
        names = {c.campaign_name for c in campaigns}
        assert "Brand Keywords - Exact" in names
        assert "Facebook - Lookalike Audience" in names
        assert "Display - Prospecting" in names
        # display file has intentional issues
        assert len(warnings) > 0


# ──────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────

class TestFormatHelpers:
    def test_roas_str_normal(self):
        assert _roas_str(4.5) == "4.50x"

    def test_roas_str_none(self):
        assert _roas_str(None) == "N/A (zero spend)"

    def test_fmt_currency(self):
        assert _fmt_currency(1234.5) == "$1,234.50"

    def test_fmt_currency_zero(self):
        assert _fmt_currency(0.0) == "$0.00"


# ──────────────────────────────────────────────
# CLI integration (CliRunner)
# ──────────────────────────────────────────────

class TestCli:
    @pytest.fixture
    def sample_dir(self):
        return str(Path(__file__).parent.parent / "sample_data")

    @pytest.fixture
    def simple_dir(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Alpha", "impressions": 10000, "clicks": 200, "spend": 500.0, "revenue": 3000.0},
            {"campaign_name": "Beta",  "impressions": 20000, "clicks": 300, "spend": 900.0, "revenue": 1800.0},
            {"campaign_name": "Gamma", "impressions": 5000,  "clicks": 50,  "spend": 200.0, "revenue": 400.0},
        ])
        return str(tmp_path)

    def test_basic_run_exits_zero(self, sample_dir):
        result = CliRunner().invoke(main, [sample_dir])
        assert result.exit_code == 0, result.output

    def test_output_contains_roas_header(self, simple_dir):
        result = CliRunner().invoke(main, [simple_dir])
        assert "ROAS" in result.output

    def test_top_flag_limits_rows(self, simple_dir):
        result = CliRunner().invoke(main, [simple_dir, "--top", "1"])
        assert result.exit_code == 0
        # Alpha has highest ROAS (3000/500=6x); Beta is 2x; Gamma is 2x
        assert "Alpha" in result.output
        assert "Beta" not in result.output

    def test_min_spend_filter(self, simple_dir):
        result = CliRunner().invoke(main, [simple_dir, "--min-spend", "600"])
        assert result.exit_code == 0
        # Only Beta (900) and Gamma (200 excluded) and Alpha (500 excluded)... wait:
        # Alpha spend=500 < 600 → excluded; Beta spend=900 ≥ 600 → included; Gamma spend=200 → excluded
        assert "Beta" in result.output
        assert "Alpha" not in result.output

    def test_no_warnings_suppresses_warnings(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "Bad CTR", "impressions": 10000, "clicks": 100,
             "spend": 500.0, "revenue": 2000.0, "ctr": "9.9"},
        ])
        result_with = CliRunner().invoke(main, [str(tmp_path)])
        result_without = CliRunner().invoke(main, [str(tmp_path), "--no-warnings"])
        assert "WARN" in result_with.output
        assert "WARN" not in result_without.output

    def test_output_csv_to_stdout(self, simple_dir):
        result = CliRunner().invoke(main, [simple_dir, "--output", "csv"])
        assert result.exit_code == 0
        assert "campaign_name" in result.output
        assert "roas" in result.output

    def test_save_csv_writes_file(self, simple_dir, tmp_path):
        out = tmp_path / "report.csv"
        result = CliRunner().invoke(main, [simple_dir, "--save-csv", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        rows = list(csv.DictReader(out.open()))
        assert len(rows) > 0
        assert "campaign_name" in rows[0]
        assert "roas" in rows[0]

    def test_empty_directory_exits_nonzero(self, tmp_path):
        result = CliRunner().invoke(main, [str(tmp_path)])
        assert result.exit_code != 0

    def test_all_filtered_exits_nonzero(self, simple_dir):
        result = CliRunner().invoke(main, [simple_dir, "--min-spend", "9999999"])
        assert result.exit_code != 0

    def test_warnings_shown_for_issues_file(self, sample_dir):
        result = CliRunner().invoke(main, [sample_dir])
        assert "WARN" in result.output

    def test_zero_spend_excluded_from_table(self, tmp_path):
        write_csv(tmp_path / "data.csv", [
            {"campaign_name": "NoSpend", "impressions": 5000, "clicks": 50, "spend": 0.0, "revenue": 0.0},
            {"campaign_name": "Good",    "impressions": 5000, "clicks": 50, "spend": 100.0, "revenue": 300.0},
        ])
        result = CliRunner().invoke(main, [str(tmp_path)])
        assert result.exit_code == 0
        assert "zero spend" in result.output.lower() or "excluded" in result.output.lower()
