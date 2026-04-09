#!/usr/bin/env python3
"""Campaign Analyzer - Reads campaign CSVs and reports top performers by ROAS."""

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click


REQUIRED_COLUMNS = {"campaign_name", "impressions", "clicks", "spend", "revenue"}
CTR_TOLERANCE = 0.01  # 1% absolute tolerance when validating reported vs. computed CTR


@dataclass
class Campaign:
    campaign_name: str
    impressions: int
    clicks: int
    spend: float
    revenue: float
    reported_ctr: Optional[float] = None  # percent, e.g. 2.5 means 2.5%
    source_file: str = ""

    @property
    def roas(self) -> Optional[float]:
        """Return on Ad Spend = revenue / spend. None when spend is zero."""
        if self.spend == 0:
            return None
        return self.revenue / self.spend

    @property
    def computed_ctr(self) -> Optional[float]:
        """Click-through rate derived from raw counts, as a percentage."""
        if self.impressions == 0:
            return None
        return (self.clicks / self.impressions) * 100


@dataclass
class ValidationWarning:
    campaign: str
    file: str
    message: str


def _parse_float(value: str, field_name: str, row_num: int, file: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        raise click.ClickException(
            f"{file} row {row_num}: cannot parse '{field_name}' as a number: {value!r}"
        )


def _parse_int(value: str, field_name: str, row_num: int, file: str) -> int:
    try:
        return int(float(value))  # handle "1000.0" style integers
    except (ValueError, TypeError):
        raise click.ClickException(
            f"{file} row {row_num}: cannot parse '{field_name}' as an integer: {value!r}"
        )


def load_campaigns(directory: Path) -> tuple[list[Campaign], list[ValidationWarning]]:
    """Load all CSV files from *directory* and return parsed campaigns + warnings."""
    csv_files = sorted(directory.glob("*.csv"))
    if not csv_files:
        raise click.ClickException(f"No CSV files found in {directory}")

    campaigns: list[Campaign] = []
    warnings: list[ValidationWarning] = []

    for csv_path in csv_files:
        file_label = csv_path.name
        try:
            with csv_path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    warnings.append(
                        ValidationWarning(
                            campaign="(unknown)", file=file_label, message="File is empty or has no header"
                        )
                    )
                    continue

                headers = {h.strip().lower() for h in reader.fieldnames}
                missing = REQUIRED_COLUMNS - headers
                if missing:
                    raise click.ClickException(
                        f"{file_label}: missing required columns: {', '.join(sorted(missing))}"
                    )

                for row_num, raw_row in enumerate(reader, start=2):
                    row = {k.strip().lower(): v.strip() for k, v in raw_row.items()}
                    name = row.get("campaign_name", "").strip()
                    if not name:
                        warnings.append(
                            ValidationWarning(
                                campaign=f"row {row_num}",
                                file=file_label,
                                message="Empty campaign_name — row skipped",
                            )
                        )
                        continue

                    impressions = _parse_int(row["impressions"], "impressions", row_num, file_label)
                    clicks = _parse_int(row["clicks"], "clicks", row_num, file_label)
                    spend = _parse_float(row["spend"], "spend", row_num, file_label)
                    revenue = _parse_float(row["revenue"], "revenue", row_num, file_label)
                    reported_ctr_raw = row.get("ctr", "").strip()
                    reported_ctr = (
                        _parse_float(reported_ctr_raw, "ctr", row_num, file_label)
                        if reported_ctr_raw
                        else None
                    )

                    campaign = Campaign(
                        campaign_name=name,
                        impressions=impressions,
                        clicks=clicks,
                        spend=spend,
                        revenue=revenue,
                        reported_ctr=reported_ctr,
                        source_file=file_label,
                    )

                    # --- Click-through validation ---
                    ctr_warnings = _validate_ctr(campaign, row_num, file_label)
                    warnings.extend(ctr_warnings)

                    campaigns.append(campaign)

        except (OSError, csv.Error) as exc:
            raise click.ClickException(f"Cannot read {file_label}: {exc}") from exc

    return campaigns, warnings


def _validate_ctr(
    c: Campaign, row_num: int, file_label: str
) -> list[ValidationWarning]:
    issues: list[ValidationWarning] = []

    if c.clicks < 0:
        issues.append(
            ValidationWarning(c.campaign_name, file_label, f"Row {row_num}: negative clicks ({c.clicks})")
        )
    if c.impressions < 0:
        issues.append(
            ValidationWarning(c.campaign_name, file_label, f"Row {row_num}: negative impressions ({c.impressions})")
        )
    if c.clicks > c.impressions and c.impressions > 0:
        issues.append(
            ValidationWarning(
                c.campaign_name,
                file_label,
                f"Row {row_num}: clicks ({c.clicks}) exceed impressions ({c.impressions})",
            )
        )
    if c.spend < 0:
        issues.append(
            ValidationWarning(c.campaign_name, file_label, f"Row {row_num}: negative spend ({c.spend})")
        )
    if c.revenue < 0:
        issues.append(
            ValidationWarning(c.campaign_name, file_label, f"Row {row_num}: negative revenue ({c.revenue})")
        )

    if c.reported_ctr is not None:
        if not (0 <= c.reported_ctr <= 100):
            issues.append(
                ValidationWarning(
                    c.campaign_name,
                    file_label,
                    f"Row {row_num}: CTR {c.reported_ctr:.4f}% is outside [0, 100]",
                )
            )
        elif c.computed_ctr is not None:
            diff = abs(c.reported_ctr - c.computed_ctr)
            if diff > CTR_TOLERANCE * 100:
                issues.append(
                    ValidationWarning(
                        c.campaign_name,
                        file_label,
                        (
                            f"Row {row_num}: reported CTR {c.reported_ctr:.4f}% differs from "
                            f"computed {c.computed_ctr:.4f}% by {diff:.4f}pp"
                        ),
                    )
                )

    return issues


# ──────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────

def _roas_str(r: Optional[float]) -> str:
    return f"{r:.2f}x" if r is not None else "N/A (zero spend)"


def _fmt_currency(value: float) -> str:
    return f"${value:,.2f}"


def _print_table(campaigns: list[Campaign], top: int) -> None:
    """Print a formatted table of top performers."""
    ranked = sorted(
        (c for c in campaigns if c.roas is not None),
        key=lambda c: c.roas,  # type: ignore[return-value]
        reverse=True,
    )
    zero_spend = [c for c in campaigns if c.roas is None]

    click.echo()
    click.echo(f"  {'#':<4} {'Campaign':<35} {'ROAS':>8}  {'Revenue':>12}  {'Spend':>12}  {'Clicks':>8}  {'CTR':>7}  {'File'}")
    click.echo("  " + "-" * 105)

    for rank, c in enumerate(ranked[:top], start=1):
        ctr_display = f"{c.computed_ctr:.2f}%" if c.computed_ctr is not None else "—"
        click.echo(
            f"  {rank:<4} {c.campaign_name:<35} {_roas_str(c.roas):>8}  "
            f"{_fmt_currency(c.revenue):>12}  {_fmt_currency(c.spend):>12}  "
            f"{c.clicks:>8,}  {ctr_display:>7}  {c.source_file}"
        )

    if zero_spend:
        click.echo()
        click.echo(f"  Campaigns excluded (zero spend): {len(zero_spend)}")
        for c in zero_spend:
            click.echo(f"    - {c.campaign_name} ({c.source_file})")


def _print_summary(campaigns: list[Campaign]) -> None:
    """Print aggregate summary statistics."""
    total_spend = sum(c.spend for c in campaigns)
    total_revenue = sum(c.revenue for c in campaigns)
    total_clicks = sum(c.clicks for c in campaigns)
    total_impressions = sum(c.impressions for c in campaigns)
    overall_roas = total_revenue / total_spend if total_spend else None
    overall_ctr = (total_clicks / total_impressions * 100) if total_impressions else None

    click.echo()
    click.echo("  Aggregate")
    click.echo("  " + "-" * 40)
    click.echo(f"  Campaigns loaded : {len(campaigns)}")
    click.echo(f"  Total spend      : {_fmt_currency(total_spend)}")
    click.echo(f"  Total revenue    : {_fmt_currency(total_revenue)}")
    click.echo(f"  Overall ROAS     : {_roas_str(overall_roas)}")
    click.echo(f"  Total clicks     : {total_clicks:,}")
    click.echo(f"  Total impressions: {total_impressions:,}")
    click.echo(f"  Blended CTR      : {f'{overall_ctr:.2f}%' if overall_ctr is not None else 'N/A'}")


def _write_csv_report(campaigns: list[Campaign], top: int, output_path: Path) -> None:
    """Write top-N performers to a CSV file."""
    ranked = sorted(
        (c for c in campaigns if c.roas is not None),
        key=lambda c: c.roas,  # type: ignore[return-value]
        reverse=True,
    )[:top]

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "campaign_name", "roas", "revenue", "spend", "clicks", "impressions", "ctr_pct", "source_file"])
        for rank, c in enumerate(ranked, start=1):
            writer.writerow([
                rank,
                c.campaign_name,
                f"{c.roas:.4f}" if c.roas is not None else "",
                f"{c.revenue:.2f}",
                f"{c.spend:.2f}",
                c.clicks,
                c.impressions,
                f"{c.computed_ctr:.4f}" if c.computed_ctr is not None else "",
                c.source_file,
            ])
    click.echo(f"  Report written to: {output_path}")


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--top", default=10, show_default=True, help="Number of top performers to display.")
@click.option(
    "--output",
    type=click.Choice(["text", "csv"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--save-csv",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Path to save a CSV report (implies --output csv for the saved file).",
)
@click.option(
    "--min-spend",
    default=0.0,
    show_default=True,
    help="Exclude campaigns with spend below this threshold.",
)
@click.option("--no-warnings", is_flag=True, default=False, help="Suppress validation warnings.")
def main(
    directory: Path,
    top: int,
    output: str,
    save_csv: Optional[Path],
    min_spend: float,
    no_warnings: bool,
) -> None:
    """Analyze campaign CSVs in DIRECTORY and report top performers by ROAS.

    \b
    Expected CSV columns (case-insensitive):
      campaign_name, impressions, clicks, spend, revenue
    Optional column:
      ctr  (click-through rate as a percentage; validated against clicks/impressions)
    """
    campaigns, warnings = load_campaigns(directory)

    if min_spend > 0:
        before = len(campaigns)
        campaigns = [c for c in campaigns if c.spend >= min_spend]
        filtered = before - len(campaigns)
        if filtered:
            click.echo(f"  Filtered {filtered} campaign(s) with spend < {_fmt_currency(min_spend)}")

    if not campaigns:
        raise click.ClickException("No campaigns remain after filtering.")

    # ── Validation warnings ──────────────────────────────────────────────────
    if warnings and not no_warnings:
        click.echo()
        click.secho(f"  Validation warnings ({len(warnings)}):", fg="yellow", bold=True)
        for w in warnings:
            click.secho(f"  [WARN] {w.file} / {w.campaign}: {w.message}", fg="yellow")

    # ── Report ───────────────────────────────────────────────────────────────
    click.echo()
    click.secho(f"  Campaign Performance Report — Top {top} by ROAS", bold=True)
    click.echo(f"  Source directory: {directory.resolve()}")

    if output == "text":
        _print_table(campaigns, top)
        _print_summary(campaigns)
    else:
        # CSV output to stdout
        import io
        buf = io.StringIO()
        ranked = sorted(
            (c for c in campaigns if c.roas is not None),
            key=lambda c: c.roas,  # type: ignore[return-value]
            reverse=True,
        )[:top]
        writer = csv.writer(buf)
        writer.writerow(["rank", "campaign_name", "roas", "revenue", "spend", "clicks", "impressions", "ctr_pct", "source_file"])
        for rank, c in enumerate(ranked, start=1):
            writer.writerow([rank, c.campaign_name, f"{c.roas:.4f}", f"{c.revenue:.2f}",
                              f"{c.spend:.2f}", c.clicks, c.impressions,
                              f"{c.computed_ctr:.4f}" if c.computed_ctr is not None else "", c.source_file])
        click.echo(buf.getvalue())

    if save_csv:
        _write_csv_report(campaigns, top, save_csv)

    click.echo()
