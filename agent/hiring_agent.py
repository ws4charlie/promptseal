"""LangChain hiring agent assembly.

Wires Claude Haiku + the three tools (resume_parse → score_candidate → decide)
into an AgentExecutor. The PromptSealCallbackHandler is attached at executor
construction so every LLM and tool call streams a signed receipt into SQLite.
"""
from __future__ import annotations

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.prompts import ChatPromptTemplate

from .llm import make_chat_llm
from .tools import TOOLS

CallbackList = list[BaseCallbackHandler]

AGENT_MODEL = "claude-sonnet-4-6"
AGENT_TEMPERATURE = 0.0

SYSTEM_PROMPT = (
    "You are a senior tech recruiter screening for a Senior Full-Stack Engineer role.\n"
    "\n"
    "You MUST follow these steps in order, no shortcuts:\n"
    "1. Call resume_parse to retrieve the candidate's data\n"
    "2. Call score_candidate to evaluate technical fit, culture fit, and ambiguity\n"
    "3. Call decide with the scores from step 2\n"
    "\n"
    "Do not skip steps. Even if a candidate seems obvious, run the full evaluation. "
    "This is required for audit compliance."
)


def build_agent_executor() -> AgentExecutor:
    """Assemble the hiring AgentExecutor.

    Callbacks are NOT attached here — pass them at invoke time via
    `screen_resume(..., callbacks=[handler])` so they propagate into the
    inner Runnable composition that `create_tool_calling_agent` builds.
    Constructor-level callbacks on AgentExecutor don't reliably reach those
    child Runnables in LangChain 0.3.x.
    """
    llm = make_chat_llm(model=AGENT_MODEL, temperature=AGENT_TEMPERATURE)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent = create_tool_calling_agent(llm, TOOLS, prompt)
    return AgentExecutor(agent=agent, tools=TOOLS, verbose=False, max_iterations=8)


def screen_resume(
    resume_id: str,
    executor: AgentExecutor,
    callbacks: CallbackList | None = None,
) -> dict:
    """Run the agent on a single resume id. Returns the executor result dict."""
    user_msg = (
        f"Please screen candidate resume {resume_id}. "
        "Use the tools to parse, score, and decide."
    )
    config = {"callbacks": callbacks} if callbacks else None
    return executor.invoke({"input": user_msg}, config=config)
