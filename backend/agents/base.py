"""Shared pieces for the five agents: the model, prompt files, per-agent MCP
permissions, and a runner that records every agent loop step to the audit trail."""

import json
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastmcp import Client
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from ..models import Deps, Policy

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = ROOT / "backend" / "prompts"

MODEL_NAME = "gpt-6-luna"  # the only model the team uses
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")

# Safety limits per ticket.
TICKET_COST_LIMIT = Decimal("0.50")
TICKET_TIME_LIMIT = 60.0
# Stop early when a ticket is "looking like" it will hit a limit.
COST_WARNING = Decimal("0.40")
TIME_WARNING = 50.0
# Upper bound on model requests per ticket, as a guard against loops.
REQUEST_LIMIT = 60

# The action each MCP tool runs when none is given.
DEFAULT_ACTION = {
    "order_tool": "check",
    "rent_tool": "check",
    "override_tool": None,
    "account_tool": "read",
    "payment_invoice_tool": "read",
    "customer_service_tool": "search",
    "ticket_tool": "list",
    "approval_tool": "list",
    "flag_tool": "list",
    "desk_tool": "read",
    "memo_tool": "guidelines",
}

def build_model() -> OpenAIChatModel:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("PORTKEY_API_KEY is not set.")
    return OpenAIChatModel(
        MODEL_NAME,
        provider=OpenAIProvider(api_key=key, base_url=PORTKEY_BASE_URL),
        # Portkey routes gpt-6-luna to Azure, which only allows tool calls with reasoning off.
        settings=OpenAIChatModelSettings(openai_reasoning_effort="none"),
    )


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def mcp_config() -> dict:
    """.mcp.json with its paths resolved against the project folder, so the server
    launches correctly wherever the repo is cloned and whichever folder the
    backend is started from."""
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    for server in config["mcpServers"].values():
        server["args"] = [str(ROOT / a) if (ROOT / a).exists() else a for a in server.get("args", [])]
        command = ROOT / server["command"]
        # The Windows venv path won't exist on macOS/Linux; fall back to the Python running the backend.
        server["command"] = str(command) if command.exists() else sys.executable
        server["cwd"] = str(ROOT)
    return config


def connect_mcp() -> MCPToolset:
    """The Campus Customs MCP server, launched as configured in .mcp.json."""
    config = mcp_config()
    return MCPToolset(Client(config), id=next(iter(config["mcpServers"])))


@dataclass
class PolicyToolset(WrapperToolset[Deps]):
    """Exposes only the MCP tools and tool actions an agent may use in this
    step, and records every call and its result to the audit trail."""

    agent_name: str = ""
    policy: Policy = field(default_factory=dict)

    async def get_tools(self, ctx: RunContext[Deps]) -> dict:
        tools = await super().get_tools(ctx)
        return {name: tool for name, tool in tools.items() if name in self.policy}

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext[Deps], tool: Any) -> Any:
        deps = ctx.deps
        action = tool_args.get("action", DEFAULT_ACTION.get(name))
        allowed = self.policy.get(name)
        entry = {"agent": self.agent_name, "tool": name, "args": tool_args}
        if name not in self.policy or (allowed is not None and action not in allowed):
            deps.audit.log("tool_call_blocked", self.agent_name, deps.ticket_id, tool=name, args=tool_args)
            deps.tool_log.append({**entry, "blocked": True})
            raise ModelRetry(
                f"{self.agent_name} may not run {name} action '{action}' in this step. "
                f"Allowed here: {sorted(allowed) if allowed else name}."
            )
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except ModelRetry as error:
            deps.audit.log("tool_error", self.agent_name, deps.ticket_id, tool=name, args=tool_args, error=str(error))
            deps.tool_log.append({**entry, "error": str(error)})
            raise
        deps.audit.log("tool_call", self.agent_name, deps.ticket_id, tool=name, args=tool_args, result=result)
        deps.tool_log.append({**entry, "result": result})
        return result


def build_agent(
    name: str,
    mcp: MCPToolset,
    policy: Policy,
    output_type: Any,
    step_instructions: str,
    system_prompt: str | None = None,
    tools: tuple = (),
) -> Agent[Deps, Any]:
    return Agent(
        build_model(),
        name=name,
        deps_type=Deps,
        output_type=output_type,
        system_prompt=system_prompt or load_prompt(name),
        instructions=step_instructions,
        toolsets=[PolicyToolset(mcp, agent_name=name, policy=policy)],
        tools=tools,
    )


def _dump(value: Any) -> Any:
    return value.model_dump() if isinstance(value, BaseModel) else value


def _describe(node: Any) -> dict:
    """One audit entry per agent loop step."""
    if Agent.is_user_prompt_node(node):
        return {"step": "user_prompt"}
    if Agent.is_model_request_node(node):
        parts = []
        for p in node.request.parts:
            kind = p.part_kind
            if kind == "system-prompt":
                parts.append({"kind": kind})
            elif kind == "tool-return":
                parts.append({"kind": kind, "tool": p.tool_name, "content": _dump(p.content)})
            else:
                parts.append({"kind": kind, "content": str(getattr(p, "content", ""))})
        return {"step": "model_request", "parts": parts}
    if Agent.is_call_tools_node(node):
        parts = []
        for p in node.model_response.parts:
            if p.part_kind == "tool-call":
                parts.append({"kind": "tool-call", "tool": p.tool_name, "args": p.args})
            elif p.part_kind == "text":
                parts.append({"kind": "text", "content": p.content})
        return {"step": "model_response", "parts": parts}
    if Agent.is_end_node(node):
        return {"step": "end"}
    return {"step": type(node).__name__}


async def run_agent(
    agent: Agent[Deps, Any],
    prompt: str,
    deps: Deps,
    usage: RunUsage,
    label: str,
    message_history: list | None = None,
) -> Any:
    """Run an agent, appending every loop step to the audit trail. The shared
    usage object carries the whole ticket's cost, so the $0.50 cap is per ticket."""
    deps.audit.log("agent_start", label, deps.ticket_id, prompt=prompt)
    limits = UsageLimits(cost_limit=TICKET_COST_LIMIT, request_limit=REQUEST_LIMIT)
    async with agent.iter(
        prompt, deps=deps, usage=usage, usage_limits=limits, message_history=message_history
    ) as run:
        async for node in run:
            deps.audit.log("loop_step", label, deps.ticket_id, **_describe(node))
    result = run.result
    deps.audit.log(
        "agent_end", label, deps.ticket_id,
        output=_dump(result.output), ticket_cost_so_far=str(usage.cost), requests_so_far=usage.requests,
    )
    return result


def as_json(value: Any) -> str:
    return json.dumps(_dump(value), indent=1, default=str)
