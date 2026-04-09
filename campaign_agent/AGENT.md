# Campaign Analyzer Agent

## Goal

Provide a conversational interface over campaign CSV data. The agent accepts natural-language questions and delegates all data access to four MCP tools — it never reads files directly. Responses are formatted for human readability (currency with commas, ROAS as a multiplier, CTR to two decimal places).

## Tools

All tools are exposed via an in-process MCP server (`campaign_analyzer` v1.0.0) and called with the prefix `mcp__campaign__`.

| Tool | Description | Required args | Optional args |
|---|---|---|---|
| `load_campaigns` | Load and validate all CSVs in a directory; returns campaigns + warnings | `directory` | — |
| `get_top_campaigns` | Rank campaigns by ROAS, return top N; excludes zero-spend | `directory` | `top` (default 10), `min_spend` (default 0) |
| `get_summary` | Aggregate stats: total spend/revenue, overall ROAS, blended CTR | `directory` | — |
| `save_report` | Write top-N ROAS ranking to a CSV file | `directory`, `output_path` | `top` (default 10) |

`load_campaigns` and `get_summary` are read-only (`readOnlyHint=True`). `save_report` is write-only and non-destructive (`destructiveHint=False`).

## Conversation loop

```
run_agent()
 └─ while True
     ├─ input("You: ")          # blocking stdin read
     ├─ query(prompt, options)  # streams messages from the Agent SDK
     │   ├─ AssistantMessage → print text blocks
     │   └─ ResultMessage(is_error) → print to stderr
     └─ break on "quit" / "exit" / EOF / KeyboardInterrupt
```

Each iteration is a single-turn exchange — the agent does not retain memory between prompts. If the user asks a follow-up that requires data, the agent will call the relevant tool again.

## Edge cases

| Situation | Behaviour |
|---|---|
| Directory does not exist | Tool returns `is_error: true` with "not found" message; agent reports it to the user |
| Directory has no CSV files | `load_campaigns` / `get_top_campaigns` return `is_error: true` with "no csv" message |
| All campaigns have zero spend | `get_top_campaigns` returns an empty `ranked` list and all campaigns appear in `excluded_zero_spend` |
| `min_spend` filters out all campaigns | Same as above — empty ranked list |
| Output directory for `save_report` does not exist | `OSError` is caught and returned as `is_error: true` |
| CTR mismatch in source data | Loaded as a validation warning; campaign is still included in results |
| `ResultMessage.is_error` from the SDK | Printed to stderr; the REPL continues to the next prompt |
| EOF / KeyboardInterrupt on input | Loop exits cleanly with no error message |
