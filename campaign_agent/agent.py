"""Agent configuration and interactive runner."""

import asyncio
import sys

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

from campaign_agent.server import create_campaign_server

SYSTEM_PROMPT = """\
You are a campaign performance analyst. You help users analyze advertising \
campaign data from CSV files.

You have tools that can:
- Load campaign data from a directory of CSV files
- Rank campaigns by ROAS (Return on Ad Spend) and show top performers
- Generate aggregate summary statistics
- Save reports to CSV files

When the user asks you to analyze campaigns, start by loading the data to \
understand what's available, then answer their questions with the appropriate tools.

Formatting guidelines:
- Currency: $ with commas (e.g. $1,234.56)
- ROAS: multiplier format (e.g. 4.50x)
- Percentages: 2 decimal places (e.g. 2.35%)
- Note any validation warnings that may affect data quality
- Campaigns with zero spend are excluded from ROAS rankings

The default data directory is ./sample_data/ unless the user specifies otherwise.
"""

TOOL_PREFIX = "mcp__campaign__"

ALLOWED_TOOLS = [
    f"{TOOL_PREFIX}load_campaigns",
    f"{TOOL_PREFIX}get_top_campaigns",
    f"{TOOL_PREFIX}get_summary",
    f"{TOOL_PREFIX}save_report",
]


def _build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"campaign": create_campaign_server()},
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="acceptEdits",
    )


async def run_agent() -> None:
    """Interactive REPL that forwards user prompts to the campaign agent."""
    options = _build_options()
    print("Campaign Analyzer Agent ready.")

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        async for message in query(prompt=user_input, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        print(block.text)
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    print(f"Agent error: {message.error}", file=sys.stderr)
