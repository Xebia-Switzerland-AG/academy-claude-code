# Campaign Anomaly Detector — Agent Plan

## Goal

Detect advertising campaigns whose ROAS, CTR, or spend deviates significantly
from peers using z-score analysis. The agent accepts natural-language questions
and delegates all computation to four MCP tools — it never reads files directly.

Z-score = (value − mean) / std_dev. Campaigns where |z-score| > threshold are
flagged as anomalies. Default threshold is 2.0 (~top/bottom 2.3% of a normal
distribution).

---

## Module layout

New module `anomaly_agent/` is a peer of the existing `campaign_agent/`.
Detection logic lives in `campaign_analyzer/anomaly.py` (no SDK dependency).

Files to create:

```
campaign_analyzer/anomaly.py          ← pure detection logic; no SDK
anomaly_agent/__init__.py             ← empty package marker
anomaly_agent/__main__.py             ← asyncio.run(run_agent())
anomaly_agent/agent.py                ← SYSTEM_PROMPT, run_agent(), ClaudeAgentOptions
anomaly_agent/tools.py                ← @tool definitions with ToolAnnotations
anomaly_agent/server.py               ← create_sdk_mcp_server() factory
anomaly_agent/AGENT.md                ← goal / tools / loop / edge-cases docs
tests/test_campaign_anomaly.py        ← unit tests for anomaly.py
tests/test_anomaly_agent.py           ← integration tests for tools + server
```

Do NOT modify: `campaign_agent/`, `campaign_analyzer/__init__.py`, existing tests.

---

## Core library: `campaign_analyzer/anomaly.py`

### `AnomalyResult` dataclass

```python
@dataclass
class AnomalyResult:
    campaign_name: str
    source_file: str
    metric: str        # "roas" | "ctr" | "spend"
    value: float       # raw metric value for this campaign
    mean: float        # group mean
    std_dev: float     # sample std dev (n-1)
    z_score: float     # (value - mean) / std_dev
    threshold: float   # threshold that was exceeded
```

### `detect_metric_anomalies()`

```python
def detect_metric_anomalies(
    campaigns: list[Campaign],
    metric: Literal["roas", "ctr", "spend"],
    threshold: float = 2.0,
) -> tuple[list[AnomalyResult], dict]:
```

**Algorithm:**

1. Extract metric values; skip campaigns where value is `None` (zero-spend → no ROAS,
   zero-impressions → no CTR). `spend` is always a float, never `None`.
2. Guard: if fewer than 3 valid values → return `([], {"skipped_reason": "fewer_than_3_valid_campaigns", ...})`
3. Compute `mean` and `statistics.stdev` (sample std dev, n-1 denominator).
4. Guard: if `std_dev == 0.0` → return `([], {"skipped_reason": "zero_standard_deviation", ...})`
5. For each campaign, compute `z = (value − mean) / std_dev`. If `|z| > threshold`, append `AnomalyResult`.
6. Return `(anomalies, metadata_dict)`.

Metadata dict always includes: `metric`, `threshold`, `campaigns_analyzed`, and on success:
`campaigns_skipped`, `mean`, `std_dev`, `anomaly_count`.

### `detect_all_anomalies()`

```python
def detect_all_anomalies(
    campaigns: list[Campaign],
    threshold: float = 2.0,
) -> dict[str, tuple[list[AnomalyResult], dict]]:
    """Run all three metrics in one call; used by save_anomaly_report tool."""
    return {
        "roas":  detect_metric_anomalies(campaigns, "roas",  threshold),
        "ctr":   detect_metric_anomalies(campaigns, "ctr",   threshold),
        "spend": detect_metric_anomalies(campaigns, "spend", threshold),
    }
```

---

## Tools (`anomaly_agent/tools.py`)

MCP server name: `"campaign_anomaly"` → tool prefix: `mcp__campaign_anomaly__`

All tools follow the exact same `@tool` / `ToolAnnotations` pattern as `campaign_agent/tools.py`.

| Tool | Required args | Optional args | Hints |
|---|---|---|---|
| `detect_roas_anomalies` | `directory` | `threshold=2.0` | `readOnlyHint=True, openWorldHint=False` |
| `detect_ctr_anomalies` | `directory` | `threshold=2.0` | `readOnlyHint=True, openWorldHint=False` |
| `detect_spend_anomalies` | `directory` | `threshold=2.0` | `readOnlyHint=True, openWorldHint=False` |
| `save_anomaly_report` | `directory`, `output_path` | `threshold=2.0` | `readOnlyHint=False, destructiveHint=False` |

**Each `detect_*` tool:**
1. Resolve and validate directory path.
2. Call `load_campaigns(directory)` — reuse existing library.
3. Call `detect_metric_anomalies(campaigns, metric, threshold)`.
4. Return JSON: `{anomalies: [...], metadata, warning_count}`.

**`save_anomaly_report` tool:**
1. Load campaigns once.
2. Call `detect_all_anomalies(campaigns, threshold)`.
3. Flatten all `AnomalyResult` rows across all 3 metrics.
4. Write CSV with columns: `campaign_name, source_file, metric, value, mean, std_dev, z_score, threshold`.
5. Return: `{status, output_path, total_anomalies, by_metric, warning_count}`.

Error responses (`is_error: True`): directory not found, no CSV files, `OSError` on write.
Success responses: always structurally consistent JSON even when anomaly list is empty.

---

## Agent loop (`anomaly_agent/agent.py`)

```
run_agent()
 └─ options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"campaign_anomaly": create_anomaly_server()},
        allowed_tools=["mcp__campaign_anomaly__detect_roas_anomalies", ...],
        permission_mode="acceptEdits",
    )
 └─ while True
     ├─ input("You: ")
     ├─ query(prompt, options)
     │   ├─ AssistantMessage → print text blocks
     │   └─ ResultMessage(is_error) → print to stderr
     └─ break on quit / exit / EOF / KeyboardInterrupt
```

**System prompt instructs the agent to:**
- Run all three detection tools when user asks to "analyze" a directory
- Format: ROAS as `4.50x`, spend as `$1,234.56`, CTR as `2.35%`, z-scores as `+2.83`
- Explain "no anomalies" results clearly (state the reason from metadata)
- Default directory is `./sample_data/` unless specified

---

## Edge cases

| Situation | Behaviour |
|---|---|
| Directory not found | `is_error: true`, "Directory not found: \<path\>" |
| No CSV files in directory | `load_campaigns` raises `ClickException` → `is_error: true` |
| Fewer than 3 campaigns with valid metric | Empty anomalies list, `skipped_reason: "fewer_than_3_valid_campaigns"` in metadata |
| All zero-spend (ROAS analysis) | 0 valid ROAS values → fewer-than-3 guard triggers |
| All zero-impressions (CTR analysis) | Same pattern for CTR |
| Zero standard deviation (all values identical) | Empty anomalies list, `skipped_reason: "zero_standard_deviation"` |
| Single campaign | Fewer than 3 valid → guard triggers |
| `output_path` parent dir missing | `OSError` caught → `is_error: true` |
| `threshold=0.0` or negative | Every campaign flagged; agent should note this to the user |
| `ResultMessage.is_error` from SDK | Printed to stderr; REPL continues to next prompt |
| EOF / KeyboardInterrupt on input | Clean exit, no error message |

---

## Implementation order

1. `campaign_analyzer/anomaly.py` — core logic, no SDK dependency
2. `anomaly_agent/__init__.py` — empty
3. `anomaly_agent/tools.py` — imports from `campaign_analyzer.anomaly` + `claude_agent_sdk`
4. `anomaly_agent/server.py` — imports from `anomaly_agent.tools`
5. `anomaly_agent/agent.py` — imports from `anomaly_agent.server`
6. `anomaly_agent/__main__.py` — imports from `anomaly_agent.agent`
7. `anomaly_agent/AGENT.md` — mirrors `campaign_agent/AGENT.md` structure
8. `tests/test_campaign_anomaly.py`
9. `tests/test_anomaly_agent.py`

---

## Verification

**1. Unit tests:**
```bash
pytest tests/test_campaign_anomaly.py -v
```
Covers: `AnomalyResult` fields, ROAS/CTR/spend outlier detection, all edge cases
(`<3` campaigns, zero std dev, zero-spend, single campaign, etc.).

**2. Tool integration tests:**
```bash
pytest tests/test_anomaly_agent.py -v
```
Covers: each tool with `tmp_path` CSV fixtures, error paths (missing dir, empty dir,
bad output path), `save_anomaly_report` CSV columns and row contents.

**3. Manual REPL:**
```bash
python -m anomaly_agent
You: check ./sample_data/ for anomalies
```
Expected: `q1_display_with_issues.csv` contains a zero-spend row and CTR mismatches —
those should surface as ROAS and CTR anomalies.

**4. Report save:**
```
You: save an anomaly report to /tmp/anomaly_report.csv
```
Then verify: `cat /tmp/anomaly_report.csv` shows correct 8-column CSV with at least
one row for the `q1_display_with_issues.csv` outlier campaigns.

---

## Key files to reference during implementation

| File | Why |
|---|---|
| [campaign_agent/tools.py](campaign_agent/tools.py) | Exact `@tool` / `ToolAnnotations` pattern to replicate |
| [campaign_agent/server.py](campaign_agent/server.py) | `create_sdk_mcp_server()` pattern |
| [campaign_agent/agent.py](campaign_agent/agent.py) | `ClaudeAgentOptions` + `query()` loop |
| [campaign_analyzer/__init__.py](campaign_analyzer/__init__.py) | `Campaign` dataclass + `load_campaigns()` signature |
| [tests/test_campaign_agent.py](tests/test_campaign_agent.py) | Test fixture patterns (`write_csv`, `_run` helper, `asyncio.run`) |
