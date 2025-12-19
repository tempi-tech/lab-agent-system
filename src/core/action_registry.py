from typing import Awaitable, Callable, Dict, List

ActionFn = Callable[[object, List[str]], Awaitable[None]]


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: Dict[str, ActionFn] = {}

    def register(self, namespace: str, actions: Dict[str, ActionFn]) -> None:
        for name, fn in actions.items():
            key = f"{namespace}.{name}"
            self._actions[key] = fn

    def get(self, key: str) -> ActionFn | None:
        return self._actions.get(key)

    def list(self) -> List[str]:
        return sorted(self._actions.keys())
