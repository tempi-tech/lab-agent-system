from abc import ABC, abstractmethod
import discord

class BaseAgent(ABC):
    """
    Base class for all Discord agents in the Lab Agent System.
    All agents must inherit from this class and implement the required methods.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the display name of the agent."""
        pass

    async def on_ready(self, client: discord.Client) -> None:
        """
        Called when the bot has successfully logged in.
        Override this method to perform initialization tasks.
        """
        pass

    async def on_message(self, message: discord.Message) -> None:
        """
        Called when a message is received.
        Override this method to handle messages.
        """
        pass
