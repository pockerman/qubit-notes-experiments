"""The models the experiments call: always real ones, each named with its provider, e.g. anthropic:claude-opus-5-5.
Every call is counted against the experiment's budget, before it is made, at the most it could cost."""

import json
import threading
from dataclasses import dataclass, field

import anthropic

# dollars per million tokens, input and output, from Anthropic's prices of November 2026; Claude Haiku 5.5's are for
# prompts of up to 100K tokens, which the experiments' are
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# a generous estimate of the tokens of a prompt from its characters, so a call's cost is never underestimated
CHARACTERS_PER_TOKEN = 3


class ModelError(Exception):
    """A model didn't answer what was asked."""


class OverBudget(Exception):
    """A call could take the experiment over its budget."""


@dataclass(frozen=True)
class Model:
    provider: str
    name: str

    @classmethod
    def parse(cls, spec: str) -> "Model":
        provider, _, name = spec.partition(":")
        if not name:
            raise ValueError(f"name a model with its provider, e.g. anthropic:claude-opus-5-5, not {spec!r}")
        if provider != "anthropic":
            raise ValueError(f"the experiments call Anthropic's models only, for now, not {provider!r}'s")
        if name not in PRICES:
            raise ValueError(f"the experiments don't know the price of {name!r}: add it to PRICES")
        return cls(provider, name)

    def __str__(self) -> str:
        return f"{self.provider}:{self.name}"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        per_input, per_output = PRICES[self.name]
        return (input_tokens * per_input + output_tokens * per_output) / 1_000_000


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost += other.cost


@dataclass
class Budget:
    """what the experiment may spend, in dollars; None for no limit"""

    limit: float | None = None
    # by role: agent, optimizer, judge
    spent: dict[str, Usage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total(self) -> float:
        return sum(usage.cost for usage in self.spent.values())

    def reserve(self, model: Model, system: str, user: str, max_tokens: int) -> None:
        """raises OverBudget if the call, at the most it could cost, could take the experiment over its budget"""
        if self.limit is None:
            return
        most = model.cost((len(system) + len(user)) // CHARACTERS_PER_TOKEN, max_tokens)
        with self._lock:
            if self.total + most > self.limit:
                raise OverBudget(f"a call of {model} could cost up to ${most:.4f}, and ${self.total:.4f} is spent")

    def record(self, role: str, usage: Usage) -> None:
        with self._lock:
            self.spent.setdefault(role, Usage()).add(usage)


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


class Models:
    """Calls models for the experiment, within its budget, and traces each call if a tracer is given."""

    def __init__(self, budget: Budget, tracer=None, client: anthropic.Anthropic | None = None):
        self.budget = budget
        self.tracer = tracer
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def json(
        self,
        role: str,
        model: Model,
        system: str,
        user: str,
        schema: dict,
        max_tokens: int,
        effort: str | None = None,
    ) -> Completion:
        """the model's answer, as JSON of the schema; ``role`` is what it answers as: agent, optimizer or judge"""
        self.budget.reserve(model, system, user, max_tokens)
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        # no fallback to another model on a refusal: an experiment's models must stay the same throughout
        arguments = {
            "model": model.name,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        trace = self.tracer.generation(role, model, system, user) if self.tracer else None
        try:
            response = self.client.messages.create(**arguments)
        except anthropic.APIStatusError as e:
            if trace:
                trace.failed(str(e))
            raise ModelError(f"{model} refused the call: {e}") from e
        except anthropic.APIConnectionError as e:
            if trace:
                trace.failed(str(e))
            raise ModelError(f"{model} couldn't be reached: {e}") from e
        usage = Usage(
            response.usage.input_tokens,
            response.usage.output_tokens,
            model.cost(response.usage.input_tokens, response.usage.output_tokens),
        )
        self.budget.record(role, usage)
        text = next((block.text for block in response.content if block.type == "text"), "")
        if trace:
            trace.done(text, usage)
        if response.stop_reason == "refusal":
            raise ModelError(f"{model} declined to answer ({_category(response)})")
        if response.stop_reason == "max_tokens":
            raise ModelError(f"{model} ran out of its {max_tokens} tokens before it finished")
        return Completion(text=text, usage=usage)

    def parsed(self, *args, **kwargs) -> tuple[dict, Usage]:
        completion = self.json(*args, **kwargs)
        try:
            return json.loads(completion.text), completion.usage
        except json.JSONDecodeError as e:
            raise ModelError(f"the answer isn't JSON: {e}") from e


def _category(response) -> str:
    details = getattr(response, "stop_details", None)
    return getattr(details, "category", None) or "no reason given"
