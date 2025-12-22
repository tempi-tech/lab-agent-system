"""Quiz Master Agent

This agent runs an AI-assisted quiz inside a Discord channel/forum.

Drop-in agent for the tempi-tech/lab-agent-system repository.
"""

from .agent import QuizMasterAgent


def get_agent() -> QuizMasterAgent:
    return QuizMasterAgent()
