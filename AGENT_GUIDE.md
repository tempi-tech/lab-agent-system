# Lab Agent System: Agent Development Guide

This document is designed to help developers (and AI assistants) understand how to create new agents for the **Lab Agent System**.

## 🏗 System Architecture

The project follows a **Platform + Plugin** architecture:

*   **Platform (`src/core/`)**: Handles Discord connection, event dispatching, and global configuration.
*   **Plugins (`src/agents/`)**: Independent agent logic. Each agent is a self-contained module.

### Key Files to Reference
1.  **`src/core/agent_base.py`**: Defines the `BaseAgent` abstract base class. **All agents must inherit from this.**
2.  **`main.py`**: The entry point where agents are registered.
3.  **`src/agents/daily_reporter/`**: A reference implementation of a working agent.

---

## 🧩 How to Create a New Agent

### 1. Create the Directory
Create a new directory in `src/agents/` (e.g., `src/agents/my_new_agent/`).

### 2. Implement the Logic
Create `logic.py` and inherit from `BaseAgent`.

```python
from src.core.agent_base import BaseAgent
import discord

class MyNewAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "MyNewAgent"

    async def on_ready(self, client: discord.Client):
        print(f"{self.name} is ready!")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if "hello" in message.content:
            await message.channel.send("World!")
```

### 3. Register the Agent
In `main.py`, import and register your new agent:

```python
from src.agents.my_new_agent.logic import MyNewAgent

# ... inside main() ...
client.register_agent(MyNewAgent())
```

---

## 🧠 Design Philosophy (Important)

1.  **Independence**: Agents should not depend on each other. They should communicate only via Discord events or shared state in `src/core` if absolutely necessary.
2.  **Configuration**:
    *   Store secrets (API Keys, Tokens) in `.env` and load them via `os.environ`.
    *   Store non-secret settings (Prompts, Model names) in a local `config.py` within the agent's folder.
3.  **Hybrid Execution**:
    *   The system runs in two modes: **Long-running** (Docker) and **Run-once** (GitHub Actions).
    *   If your agent needs to run on a schedule (e.g., daily report), ensure it can be triggered explicitly or works within the `run_once` logic in `main.py`.

---

## 🤖 AI Prompt Template

If you want to ask an AI (like Gemini or ChatGPT) to create a new agent for you, copy and paste the following prompt. It provides all the necessary context.

```markdown
I want to add a new agent to the "Lab Agent System".
Please create a new agent based on the following specifications.

## System Context
- **Architecture**: Python-based Discord bot using `discord.py`.
- **Base Class**: All agents MUST inherit from `BaseAgent` defined in `src/core/agent_base.py`.
- **File Structure**: Agents live in `src/agents/[agent_name]/`.

## Existing Interfaces
class BaseAgent(ABC):
    @property
    @abstractmethod
    def name(self) -> str: pass
    async def on_ready(self, client: discord.Client) -> None: pass
    async def on_message(self, message: discord.Message) -> None: pass

## Request
Create an agent named "[Agent Name]" that does the following:
[Describe the feature here, e.g., "Replies with a joke when someone says !joke"]

Please provide:
1. The directory structure (e.g., `src/agents/joker/logic.py`).
2. The code for the agent class.
3. The code to register it in `main.py`.
```
