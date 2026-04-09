# Campaign Analyzer

Reads campaign CSV files from a directory and reports top performers by ROAS (Return on Ad Spend).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python -m campaign_analyzer <directory> [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--top N` | 10 | Show top N performers |
| `--output text\|csv` | text | Output format |
| `--save-csv PATH` | — | Write results to a CSV file |
| `--min-spend FLOAT` | 0 | Exclude campaigns below this spend threshold |
| `--no-warnings` | off | Suppress validation warnings |

```bash
python -m campaign_analyzer sample_data/ --top 5
python -m campaign_analyzer sample_data/ --output csv --save-csv report.csv
python -m campaign_analyzer sample_data/ --min-spend 500 --no-warnings
```

## Input Format

Each CSV file must have these columns (case-insensitive):

| Column | Type | Required |
|---|---|---|
| `campaign_name` | string | yes |
| `impressions` | integer | yes |
| `clicks` | integer | yes |
| `spend` | float | yes |
| `revenue` | float | yes |
| `ctr` | float (%) | no |

If `ctr` is present it is validated against `clicks / impressions` within a 1% tolerance.

## Sample Data

`sample_data/` has three files for testing:

- `q1_search.csv` — clean search campaigns
- `q1_social.csv` — clean social campaigns
- `q1_display_with_issues.csv` — intentionally includes zero-spend rows and CTR mismatches to exercise validation warnings
