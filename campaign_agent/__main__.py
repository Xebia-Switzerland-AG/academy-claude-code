"""Entry point for python -m campaign_agent."""

import asyncio

from campaign_agent.agent import run_agent

asyncio.run(run_agent())
