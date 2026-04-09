"""MCP server factory for the campaign analyzer agent."""

from claude_agent_sdk import create_sdk_mcp_server

from campaign_agent.tools import (
    load_campaigns_tool,
    get_top_campaigns_tool,
    get_summary_tool,
    save_report_tool,
)

SERVER_NAME = "campaign_analyzer"
SERVER_VERSION = "1.0.0"


def create_campaign_server():
    """Create an in-process MCP server with the campaign analysis tools."""
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        tools=[
            load_campaigns_tool,
            get_top_campaigns_tool,
            get_summary_tool,
            save_report_tool,
        ],
    )
