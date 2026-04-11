"""LangGraph multi-node research agent instrumented with OtelMind.

Nodes:
  research → draft → review → (loop back to draft OR finalize)

The agent accepts a query, researches it with GPT-4o, drafts a response,
reviews it for quality, and optionally loops back for revision (max 2 times).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

# ── State ───────────────────────────────────────────────────────────────


class AgentState(TypedDict):
    query: str
    research_output: str
    draft_output: str
    review_feedback: str
    review_passed: bool
    revision_count: int
    final_output: str
    # Token tracking for OtelMind
    total_prompt_tokens: int
    total_completion_tokens: int
    model_name: str


# ── LLM ─────────────────────────────────────────────────────────────────


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o", temperature=0.3)


# ── Node Functions ──────────────────────────────────────────────────────


def research(state: AgentState) -> dict[str, Any]:
    """Research node — gathers information about the query using GPT-4o."""
    llm = get_llm()

    messages = [
        SystemMessage(
            content=(
                "You are a research assistant. Given a query, provide a comprehensive "
                "research summary with key facts, data points, and recent developments. "
                "Be thorough but concise. Include specific numbers and sources where possible."
            )
        ),
        HumanMessage(content=f"Research the following topic:\n\n{state['query']}"),
    ]

    response = llm.invoke(messages)
    usage = response.usage_metadata or {}

    return {
        "research_output": response.content,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + usage.get("input_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0)
        + usage.get("output_tokens", 0),
        "model_name": "gpt-4o",
    }


def draft(state: AgentState) -> dict[str, Any]:
    """Draft node — writes a polished response based on research."""
    llm = get_llm()

    revision_note = ""
    if state.get("review_feedback") and state.get("revision_count", 0) > 0:
        revision_note = (
            f"\n\nPREVIOUS REVIEW FEEDBACK (address these issues):\n"
            f"{state['review_feedback']}\n\n"
            f"PREVIOUS DRAFT:\n{state['draft_output']}"
        )

    messages = [
        SystemMessage(
            content=(
                "You are a skilled writer. Using the research provided, write a clear, "
                "well-structured response to the user's query. Include an introduction, "
                "key points with supporting evidence, and a brief conclusion."
            )
        ),
        HumanMessage(
            content=(
                f"Query: {state['query']}\n\n"
                f"Research:\n{state['research_output']}"
                f"{revision_note}"
            )
        ),
    ]

    response = llm.invoke(messages)
    usage = response.usage_metadata or {}

    return {
        "draft_output": response.content,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + usage.get("input_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0)
        + usage.get("output_tokens", 0),
    }


def review(state: AgentState) -> dict[str, Any]:
    """Review node — checks draft quality and decides if revision is needed."""
    llm = get_llm()

    messages = [
        SystemMessage(
            content=(
                "You are a quality reviewer. Evaluate the draft response for:\n"
                "1. Accuracy — does it match the research?\n"
                "2. Completeness — are key points covered?\n"
                "3. Clarity — is it well-written and easy to understand?\n"
                "4. Structure — does it have intro, body, conclusion?\n\n"
                "Respond with EXACTLY this format:\n"
                "VERDICT: PASS or FAIL\n"
                "FEEDBACK: your specific feedback here"
            )
        ),
        HumanMessage(
            content=(
                f"Query: {state['query']}\n\n"
                f"Research:\n{state['research_output']}\n\n"
                f"Draft Response:\n{state['draft_output']}"
            )
        ),
    ]

    response = llm.invoke(messages)
    usage = response.usage_metadata or {}
    content = response.content

    passed = "VERDICT: PASS" in content.upper()
    feedback = content.split("FEEDBACK:", 1)[-1].strip() if "FEEDBACK:" in content else content

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "revision_count": state.get("revision_count", 0) + 1,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + usage.get("input_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0)
        + usage.get("output_tokens", 0),
    }


def finalize(state: AgentState) -> dict[str, Any]:
    """Finalize node — produces the final output."""
    return {
        "final_output": state["draft_output"],
    }


# ── Routing ─────────────────────────────────────────────────────────────


def should_revise(state: AgentState) -> str:
    """Conditional edge: loop back to draft if review failed and under revision limit."""
    if not state.get("review_passed", False) and state.get("revision_count", 0) < 2:
        return "draft"
    return "finalize"


# ── Graph Builder ───────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    """Build the research agent graph (uncompiled)."""
    graph = StateGraph(AgentState)

    graph.add_node("research", research)
    graph.add_node("draft", draft)
    graph.add_node("review", review)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("research")
    graph.add_edge("research", "draft")
    graph.add_edge("draft", "review")
    graph.add_conditional_edges("review", should_revise, {"draft": "draft", "finalize": "finalize"})
    graph.add_edge("finalize", END)

    return graph
