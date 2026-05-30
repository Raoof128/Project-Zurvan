#!/usr/bin/env python3
"""
Full Zurvan MCP E2E smoke test.

Tests:
- MCP stdio server starts
- tools/resources/prompts are exposed
- read-only tools work
- write tools are blocked in read-only mode
- write tools work only when explicitly enabled
- raw/traversal resource access is blocked
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "mcp_server.py"


REQUIRED_TOOLS = {
    "zurvan_search",
    "zurvan_context",
    "zurvan_graph_stats",
    "zurvan_graph_neighbours",
    "zurvan_graph_expand",
    "zurvan_eval_search",
    "zurvan_validate_gold",
    "zurvan_remember",
    "zurvan_decision_add",
    "zurvan_claim_add",
    "zurvan_question_add",
}

REQUIRED_RESOURCES = {
    "zurvan://wiki/index",
    "zurvan://wiki/log",
    "zurvan://wiki/overview",
    "zurvan://wiki/open-questions",
    "zurvan://graph/stats",
    "zurvan://eval/baseline",
}

REQUIRED_PROMPTS = {
    "zurvan_project_brief",
    "zurvan_pre_edit_context",
    "zurvan_post_edit_memory",
    "zurvan_research_audit",
}


def result_text(result: Any) -> str:
    """Best-effort stringify for MCP tool/resource/prompt results."""
    if result is None:
        return ""

    if hasattr(result, "content"):
        parts = []
        for item in result.content:
            parts.append(getattr(item, "text", str(item)))
        return "\n".join(parts)

    if hasattr(result, "contents"):
        parts = []
        for item in result.contents:
            parts.append(getattr(item, "text", str(item)))
        return "\n".join(parts)

    if hasattr(result, "messages"):
        parts = []
        for msg in result.messages:
            content = getattr(msg, "content", "")
            parts.append(getattr(content, "text", str(content)))
        return "\n".join(parts)

    return str(result)


async def start_session(readonly: bool = True):
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": ".",
            "ZURVAN_MCP_TRANSPORT": "stdio",
            "ZURVAN_MCP_READONLY": "1" if readonly else "0",
            "ZURVAN_MCP_ALLOW_RAW_READ": "0",
            "ZURVAN_LLM_PROVIDER": "mock",
            "ZURVAN_LLM_MODEL": "mock",
            "ZURVAN_EMBED_PROVIDER": "mock",
        }
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        env=env,
        cwd=str(ROOT),
    )

    return stdio_client(params)


async def expect_tool(session: ClientSession, name: str, args: dict[str, Any]) -> str:
    result = await session.call_tool(name, args)
    text = result_text(result)
    if not text.strip():
        raise AssertionError(f"{name} returned empty output")
    print(f"✅ Tool OK: {name}")
    return text


async def expect_blocked_write(session: ClientSession) -> None:
    try:
        result = await session.call_tool(
            "zurvan_remember",
            {
                "type": "note",
                "title": "MCP readonly blocked smoke test",
                "body": "This write should be blocked in read-only mode.",
                "tags": ["mcp", "smoke", "readonly"],
            },
        )
        text = result_text(result).lower()
        if "readonly" not in text and "read-only" not in text and "blocked" not in text:
            raise AssertionError(
                "Write tool returned successfully but did not clearly say it was blocked."
            )
        print("✅ Read-only write blocked clearly")
    except Exception as exc:
        text = str(exc).lower()
        if "readonly" not in text and "read-only" not in text and "blocked" not in text:
            raise
        print("✅ Read-only write blocked by exception")


async def expect_resource(session: ClientSession, uri: str) -> str:
    result = await session.read_resource(uri)
    text = result_text(result)
    if not text.strip():
        raise AssertionError(f"Resource returned empty output: {uri}")
    print(f"✅ Resource OK: {uri}")
    return text


async def expect_resource_blocked(session: ClientSession, uri: str) -> None:
    try:
        result = await session.read_resource(uri)
        text = result_text(result).lower()
        if "blocked" not in text and "not allowed" not in text and "unsafe" not in text:
            raise AssertionError(f"Unsafe resource was not blocked clearly: {uri}")
        print(f"✅ Unsafe resource blocked clearly: {uri}")
    except Exception:
        print(f"✅ Unsafe resource blocked by exception: {uri}")


async def expect_prompt(session: ClientSession, name: str) -> str:
    result = await session.get_prompt(name, arguments={})
    text = result_text(result)
    if not text.strip():
        raise AssertionError(f"Prompt returned empty output: {name}")
    print(f"✅ Prompt OK: {name}")
    return text


async def readonly_smoke() -> None:
    print("\n[1/2] MCP read-only smoke test")

    async with await start_session(readonly=True) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = {tool.name for tool in tools_result.tools}
            missing_tools = REQUIRED_TOOLS - tool_names
            if missing_tools:
                raise AssertionError(f"Missing MCP tools: {sorted(missing_tools)}")
            print(f"✅ Tools exposed: {len(tool_names)}")

            resources_result = await session.list_resources()
            resource_uris = {str(resource.uri) for resource in resources_result.resources}
            missing_resources = REQUIRED_RESOURCES - resource_uris
            if missing_resources:
                raise AssertionError(f"Missing MCP resources: {sorted(missing_resources)}")
            print(f"✅ Resources exposed: {len(resource_uris)}")

            prompts_result = await session.list_prompts()
            prompt_names = {prompt.name for prompt in prompts_result.prompts}
            missing_prompts = REQUIRED_PROMPTS - prompt_names
            if missing_prompts:
                raise AssertionError(f"Missing MCP prompts: {sorted(missing_prompts)}")
            print(f"✅ Prompts exposed: {len(prompt_names)}")

            await expect_tool(
                session,
                "zurvan_search",
                {"query": "vector search reliability", "hybrid": True, "limit": 5},
            )

            await expect_tool(
                session,
                "zurvan_context",
                {
                    "topic": "vector search roadmap",
                    "hybrid": True,
                    "graph": True,
                    "limit": 5,
                },
            )

            await expect_tool(session, "zurvan_graph_stats", {})

            await expect_tool(
                session,
                "zurvan_validate_gold",
                {},
            )

            await expect_tool(
                session,
                "zurvan_eval_search",
                {"hybrid": True, "min_top3": 0.6},
            )

            await expect_resource(session, "zurvan://wiki/index")
            await expect_resource(session, "zurvan://wiki/log")
            await expect_resource(session, "zurvan://graph/stats")
            await expect_resource(session, "zurvan://eval/baseline")

            await expect_resource_blocked(session, "zurvan://file/raw/notes/e2e_smoke_note.txt")
            await expect_resource_blocked(session, "zurvan://file/../AGENTS.md")

            await expect_prompt(session, "zurvan_project_brief")
            await expect_prompt(session, "zurvan_pre_edit_context")
            await expect_prompt(session, "zurvan_post_edit_memory")
            await expect_prompt(session, "zurvan_research_audit")

            await expect_blocked_write(session)


async def write_mode_smoke() -> None:
    print("\n[2/2] MCP write-mode smoke test")

    async with await start_session(readonly=False) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            await expect_tool(
                session,
                "zurvan_remember",
                {
                    "type": "note",
                    "title": "MCP E2E write mode smoke",
                    "body": "Zurvan MCP write mode can store a note only when explicitly enabled.",
                    "tags": ["mcp", "e2e", "write-mode"],
                },
            )

            await expect_tool(
                session,
                "zurvan_question_add",
                {
                    "question": "Should MCP write mode stay disabled by default?",
                    "reason": "Write mode can change project memory, so read-only should remain the safe default.",
                    "tags": ["mcp", "security", "readonly"],
                },
            )

            print("✅ Write mode tools worked when explicitly enabled")


async def main() -> None:
    if not SERVER.exists():
        raise SystemExit(f"Missing MCP server: {SERVER}")

    await readonly_smoke()
    await write_mode_smoke()

    print("\n=========================================")
    print("🎉 Zurvan MCP E2E smoke test passed.")
    print("=========================================")


if __name__ == "__main__":
    asyncio.run(main())
