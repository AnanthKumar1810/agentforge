"""Optional CrewAI planning and BeeAI risk-review integrations."""

from __future__ import annotations
import asyncio
import os


async def run_crewai_planning(request: str, model: str) -> str:
    def kickoff() -> str:
        try:
            from crewai import Agent, Crew, Process, Task
        except ImportError as exc:
            raise RuntimeError("CrewAI is optional; install with pip install -e '.[all]'") from exc
        planner = Agent(
            role="Software Architecture Planner",
            goal="Produce a concise implementation plan, risks, and acceptance criteria",
            backstory="A careful staff engineer who plans testable changes and does not edit files.",
            llm=os.getenv("AGENTFORGE_CREWAI_MODEL", model), verbose=False, allow_delegation=False,
        )
        task = Task(
            description=f"Plan the task. Include assumptions, file-level design, acceptance criteria, and tests. Do not claim to edit files.\n\nTask: {request}",
            expected_output="Concise engineering plan with acceptance criteria and risks", agent=planner,
        )
        result = Crew(agents=[planner], tasks=[task], process=Process.sequential, verbose=False).kickoff()
        return str(getattr(result, "raw", result))
    return await asyncio.to_thread(kickoff)


async def run_beeai_risk_review(request: str, plan: str, model_name: str) -> str:
    try:
        from beeai_framework.backend import ChatModel, UserMessage
    except ImportError as exc:
        raise RuntimeError("BeeAI is optional; install with pip install -e '.[all]'") from exc
    model = ChatModel.from_name(model_name)
    prompt = (
        "Review risks and edge cases. Do not make file changes; be specific and concise.\n\n"
        f"User task: {request}\n\nPlan: {plan}"
    )
    result = await model.run([UserMessage(prompt)])
    return result.get_text_content()
