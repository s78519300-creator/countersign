"""Gym-style wrapper: reset() -> observation, step(action) -> (observation, reward, done, info)."""
from __future__ import annotations

from typing import Callable, Optional

from .generate import generate
from .scoring import score
from .tools import TOOLS
from .world import World, brief


class Env:
    """One episode = one company period.

    >>> env = Env(level="easy", seed=3)
    >>> obs = env.reset()
    >>> obs, reward, done, info = env.step({"tool": "get_policy", "args": {}})
    """

    def __init__(self, level: str = "medium", seed: int = 0):
        self.level, self.seed = level, seed
        self.world: Optional[World] = None

    def reset(self, seed: Optional[int] = None, level: Optional[str] = None) -> dict:
        if seed is not None:
            self.seed = seed
        if level is not None:
            self.level = level
        self.world = World(generate(self.seed, self.level))
        return {"brief": brief(self.world.s), "tools": TOOLS}

    def step(self, action: dict) -> tuple[dict, float, bool, dict]:
        if self.world is None:
            raise RuntimeError("call reset() first")
        if not isinstance(action, dict):
            action = {}
        obs = self.world.call(str(action.get("tool", "")), action.get("args"))
        done = self.world.done
        info = self.result() if done else {}
        return obs, (info["reward"] if done else 0.0), done, info

    def call(self, tool: str, **args) -> dict:
        """Convenience for scripted policies: env.call('get_invoice', invoice_id='D-0001')."""
        return self.step({"tool": tool, "args": args})[0]

    @property
    def done(self) -> bool:
        return bool(self.world and self.world.done)

    def result(self) -> dict:
        if self.world is None:
            raise RuntimeError("call reset() first")
        return score(self.world)

    def trajectory(self) -> list[dict]:
        return list(self.world.log) if self.world else []


def run_episode(policy: Callable[[Env], None], level: str = "medium", seed: int = 0) -> tuple[dict, Env]:
    """Run a scripted policy to the end of the period and return (result, env)."""
    env = Env(level, seed)
    env.reset()
    policy(env)
    while not env.done:                      # a policy that stops early still gets a closed period
        env.call("end_day")
    return env.result(), env
