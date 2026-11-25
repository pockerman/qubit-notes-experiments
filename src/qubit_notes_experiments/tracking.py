"""Where an experiment is tracked: in Langfuse, in a project of the experiments' own, if its keys are given; otherwise
only here, in its records. Either way a round runs the same: the task on each sample, then the judge's verdicts on the
outputs, as scores of their traces."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qubit_notes_experiments.models import Model, Usage
from qubit_notes_experiments.settings import Settings


class SameProjectAsApp(Exception):
    """The experiments' Langfuse project is the app's."""


@dataclass
class Item:
    """one sample of a round: its id and the sample itself"""

    sample_id: str
    input: dict


@dataclass
class ItemResult:
    sample_id: str
    output: Any
    trace_id: str | None = None


@dataclass
class RunResult:
    name: str
    items: list[ItemResult]
    url: str | None = None


# the task: what a version writes for a sample
Task = Callable[[Item], Any]


class LocalTracker:
    """Tracks nothing outside the experiment's records."""

    langfuse = False

    def generation(self, role: str, model: Model, system: str, user: str):
        return None

    def dataset(self, name: str, items: list[Item]) -> str:
        return name

    def run(self, experiment: str, dataset: str, run_name: str, items: list[Item], task: Task) -> RunResult:
        return RunResult(run_name, [ItemResult(item.sample_id, task(item)) for item in items])

    def score(self, result: ItemResult, name: str, value: float, comment: str) -> None:
        pass

    def prompt(self, name: str, text: str, commit: str) -> int | None:
        return None

    def label_best(self, name: str, version: int | None) -> None:
        pass

    def flush(self) -> None:
        pass


class _Generation:
    def __init__(self, observation):
        self._observation = observation

    def done(self, text: str, usage: Usage) -> None:
        self._observation.update(
            output=text,
            usage_details={"input": usage.input_tokens, "output": usage.output_tokens},
            cost_details={"total": usage.cost},
        )
        self._observation.end()

    def failed(self, message: str) -> None:
        self._observation.update(level="ERROR", status_message=message)
        self._observation.end()


class LangfuseTracker:
    """Tracks the experiment in Langfuse: its samples as datasets, each round as a run of one, every call of a model,
    and each version of the prompt in prompt management."""

    langfuse = True

    def __init__(self, settings: Settings):
        if settings.app_langfuse_public_key and settings.langfuse_public_key == settings.app_langfuse_public_key:
            raise SameProjectAsApp(
                "LANGFUSE_PUBLIC_KEY is the app's project's: give the experiments a project of their own"
            )
        from langfuse import Langfuse

        self.public_key = settings.langfuse_public_key
        self._client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment="experiments",
        )

    def generation(self, role: str, model: Model, system: str, user: str):
        observation = self._client.start_observation(
            name=role, as_type="generation", model=model.name, input={"system": system, "user": user}
        )
        return _Generation(observation)

    def dataset(self, name: str, items: list[Item]) -> str:
        self._client.create_dataset(name=name)
        for item in items:
            # the sample's id is the item's, so a sample drawn again is the same item
            self._client.create_dataset_item(
                dataset_name=name,
                id=f"{name}-{item.sample_id}",
                input=item.input,
                metadata={"sample_id": item.sample_id},
            )
        return name

    def run(self, experiment: str, dataset: str, run_name: str, items: list[Item], task: Task) -> RunResult:
        from qubit_notes_experiments.models import OverBudget

        by_id = {item.sample_id: item for item in items}
        outputs: dict[str, Any] = {}
        # Langfuse only logs what a task raises: a stop at the budget is carried out of the run here
        stopped: list[OverBudget] = []

        def langfuse_task(*, item, **_):
            ours = by_id[item.metadata["sample_id"]]
            if stopped:
                return {"stopped": str(stopped[0])}
            try:
                outputs[ours.sample_id] = task(ours)
            except OverBudget as e:
                stopped.append(e)
                return {"stopped": str(e)}
            return _summary(outputs[ours.sample_id])

        wanted = set(by_id)
        data = [i for i in self._client.get_dataset(dataset).items if (i.metadata or {}).get("sample_id") in wanted]
        result = self._client.run_experiment(
            name=experiment,
            run_name=run_name,
            data=data,
            task=langfuse_task,
            # one sample at a time, so the budget is checked before every call
            max_concurrency=1,
        )
        if stopped:
            raise stopped[0]
        results = [
            ItemResult(r.item.metadata["sample_id"], outputs.get(r.item.metadata["sample_id"]), trace_id=r.trace_id)
            for r in result.item_results
        ]
        return RunResult(run_name, results, result.dataset_run_url)

    def score(self, result: ItemResult, name: str, value: float, comment: str) -> None:
        """the judge's verdict on a sample's output, as a score of its trace in the run"""
        if result.trace_id:
            self._client.create_score(
                name=name, value=value, trace_id=result.trace_id, data_type="NUMERIC", comment=comment
            )

    def prompt(self, name: str, text: str, commit: str) -> int | None:
        return self._client.create_prompt(name=name, prompt=text, labels=[], type="text", commit_message=commit).version

    def label_best(self, name: str, version: int | None) -> None:
        if version is not None:
            self._client.update_prompt(name=name, version=version, new_labels=["best"])

    def flush(self) -> None:
        self._client.flush()


def _summary(output) -> Any:
    """what Langfuse shows of a task's output"""
    questions = getattr(output, "questions", None)
    if questions is not None:
        return {"questions": questions}
    return {"unusable": getattr(output, "unreadable", None)}


def tracker(settings: Settings):
    return LangfuseTracker(settings) if settings.langfuse else LocalTracker()
