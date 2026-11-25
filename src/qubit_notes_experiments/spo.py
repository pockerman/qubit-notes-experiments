"""Self-Supervised Prompt Optimization (SPO; Xiang et al., 2025, arXiv:2502.06855) of one prompt of the app.

The starting version is run on a few samples, and is the best so far. Each round, the optimizer writes a new version
from the best so far, its outputs and the requirements; the new version is run on the same samples; and the judge
compares its outputs with the best's, sample by sample, several times in both orders. The version preferred in more
than half of the verdicts is the best so far; on a tie, the best stays. After the last round, the best version is
the result, which is checked once more against the starting version on held-out samples, and recommended for the app
only if the judge prefers it there too."""

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from qubit_notes_experiments import ROOT
from qubit_notes_experiments.agents import quiz
from qubit_notes_experiments.models import Budget, Model, Models, OverBudget
from qubit_notes_experiments.tracking import Item, ItemResult

PROMPTS = Path(__file__).parent / "spo_prompts"
OPTIMIZER_MAX_TOKENS = 16000
JUDGE_MAX_TOKENS = 8000

OPTIMIZER_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string", "description": "where the outputs fall short of the requirements"},
        "modification": {"type": "string", "description": "what the new version changes, and why"},
        "prompt": {"type": "string", "description": "the new version of the prompt, whole"},
    },
    "required": ["analysis", "modification", "prompt"],
    "additionalProperties": False,
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "preferred": {"type": "string", "enum": ["A", "B"]},
    },
    "required": ["reason", "preferred"],
    "additionalProperties": False,
}


class ExperimentError(Exception):
    """An experiment can't be run as asked."""


def sha256(text: str) -> str:
    """as the app's: of the text without its last newlines"""
    return hashlib.sha256(text.rstrip("\n").encode()).hexdigest()


# the agent


@dataclass(frozen=True)
class Agent:
    """an agent of the app, how its prompt is optimized, and where its experiments are kept"""

    name: str
    directory: Path
    prompt: str
    start: int
    start_text: str
    placeholders: dict
    models: dict[str, Model]
    defaults: dict

    @classmethod
    def load(cls, name: str, root: Path = ROOT) -> "Agent":
        directory = root / "experiments" / "agents" / name / "spo"
        if not (directory / "agent.toml").is_file():
            raise ExperimentError(f"there is no agent {name!r}: no {directory / 'agent.toml'}")
        with (directory / "agent.toml").open("rb") as file:
            config = tomllib.load(file)
        start = config["start"]
        path = directory / "prompts" / f"v{start}.md"
        text = path.read_text(encoding="utf-8").rstrip("\n")
        if sha256(text) != config["sha256"]:
            raise ExperimentError(f"{path} isn't the app's version {start} of {config['prompt']!r}: its hash differs")
        return cls(
            name=name,
            directory=directory,
            prompt=config["prompt"],
            start=start,
            start_text=text,
            placeholders=config.get("placeholders", {}),
            models={role: Model.parse(spec) for role, spec in config["models"].items()},
            defaults=config.get("spo", {}),
        )

    @property
    def requirements_path(self) -> Path:
        return self.directory / "requirements.md"

    def requirements(self) -> str:
        if not self.requirements_path.is_file():
            raise ExperimentError(f"write {self.requirements_path.relative_to(ROOT)} first: what a good output is")
        return self.requirements_path.read_text(encoding="utf-8")

    @property
    def ref(self) -> str:
        """the starting version as the app names it, e.g. quiz@1"""
        return f"{self.prompt}@{self.start}"


# what an experiment records


@dataclass
class Version:
    # v1 for the app's version, r<n> for the one written in round n
    id: str
    text: str
    sha256: str
    round: int
    # the version it was written from
    parent: str | None = None
    analysis: str | None = None
    modification: str | None = None
    langfuse_version: int | None = None


@dataclass
class Verdict:
    sample_id: str
    # which was shown as A: "challenger" (the new version, or the result) or "incumbent" (the best, or the start)
    first: str
    preferred: str
    reason: str


@dataclass
class Comparison:
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def challenger_wins(self) -> int:
        return sum(v.preferred == "challenger" for v in self.verdicts)

    @property
    def challenger_preferred(self) -> bool:
        """the challenger is preferred in more than half the verdicts; a tie keeps the incumbent"""
        return self.challenger_wins * 2 > len(self.verdicts)


@dataclass
class Round:
    number: int
    version: str
    # the best version before the round
    against: str
    outputs: dict[str, dict] = field(default_factory=dict)
    comparison: Comparison | None = None
    # why the round was lost without being judged, if it was
    lost: str | None = None
    # the best version after the round
    best: str = ""
    cost: float = 0.0
    run_url: str | None = None


@dataclass
class HeldOut:
    samples: list[str]
    start_outputs: dict[str, dict] = field(default_factory=dict)
    result_outputs: dict[str, dict] = field(default_factory=dict)
    comparison: Comparison | None = None
    recommended: bool = False
    skipped: str | None = None


@dataclass
class Experiment:
    id: str
    agent: str
    prompt: str
    start: str
    start_sha256: str
    models: dict[str, str]
    seed: int
    comparisons: int
    samples: list[str]
    held_out_samples: list[str]
    # the versions of the prompt the app's database records the samples' quizzes were written with
    app_versions: list[str]
    budget: float | None
    started_at: str
    versions: dict[str, Version] = field(default_factory=dict)
    rounds: list[Round] = field(default_factory=list)
    best: str = ""
    result: str | None = None
    held_out: HeldOut | None = None
    stopped: str | None = None
    usage: dict[str, dict] = field(default_factory=dict)
    langfuse: bool = False

    @property
    def recommended(self) -> bool:
        return bool(self.held_out and self.held_out.recommended)


# running an experiment


def _output_record(output: quiz.Output) -> dict:
    return {
        "questions": output.questions,
        "unreadable": output.unreadable,
        "usage": asdict(output.usage),
    }


class Optimization:
    """one experiment of SPO on an agent's prompt"""

    def __init__(
        self,
        agent: Agent,
        models: Models,
        tracker,
        samples: list[quiz.Sample],
        held_out: list[quiz.Sample],
        seed: int,
        comparisons: int,
        experiment_id: str,
        model_overrides: dict[str, Model] | None = None,
    ):
        if comparisons < 2 or comparisons % 2:
            raise ExperimentError("the judge compares each sample an even number of times, half in each order")
        self.agent = agent
        self.models = models
        self.tracker = tracker
        self.samples = samples
        self.held_out_samples = held_out
        self.comparisons = comparisons
        self.roles = {**agent.models, **(model_overrides or {})}
        self.requirements = agent.requirements()
        app_versions = sorted({p for s in samples + held_out for p in s.prompts})
        self.record = Experiment(
            id=experiment_id,
            agent=agent.name,
            prompt=agent.prompt,
            start=agent.ref,
            start_sha256=sha256(agent.start_text),
            models={role: str(model) for role, model in self.roles.items()},
            seed=seed,
            comparisons=comparisons,
            samples=[s.id for s in samples],
            held_out_samples=[s.id for s in held_out],
            app_versions=app_versions,
            budget=models.budget.limit,
            started_at=datetime.now(UTC).isoformat(),
            langfuse=tracker.langfuse,
        )
        self.dataset = f"{agent.name}/{experiment_id}/samples"
        self.held_out_dataset = f"{agent.name}/{experiment_id}/held-out"
        self.name = f"{agent.name} SPO {experiment_id}"
        self._outputs: dict[str, dict[str, quiz.Output]] = {}

    # the steps

    def _add_version(self, version: Version) -> None:
        version.langfuse_version = self.tracker.prompt(
            self.agent.prompt, version.text, f"{self.record.id} {version.id}"
        )
        self.record.versions[version.id] = version

    def _items(self, samples: list[quiz.Sample]) -> list[Item]:
        return [Item(s.id, {"count": s.count, "material": quiz.material(s)}) for s in samples]

    def _run(self, version: Version, samples: list[quiz.Sample], dataset: str, run_name: str):
        """the version's outputs on the samples, as a run of their dataset"""
        system = quiz.system_prompt(version.text, self.agent.placeholders)
        by_id = {s.id: s for s in samples}
        choices = int(self.agent.placeholders.get("choices", 4))

        def task(item: Item) -> quiz.Output:
            return quiz.run(self.models, self.roles["agent"], system, by_id[item.sample_id], choices)

        result = self.tracker.run(self.name, dataset, run_name, self._items(samples), task)
        self._outputs[version.id] = {r.sample_id: r.output for r in result.items}
        return result

    def _judge(
        self,
        samples: list[quiz.Sample],
        challenger: dict[str, quiz.Output],
        incumbent: dict[str, quiz.Output],
        results: list[ItemResult],
    ) -> Comparison:
        """the judge's verdicts on each sample's two outputs, half with each shown first"""
        comparison = Comparison()
        by_result = {r.sample_id: r for r in results}
        for sample in samples:
            mine, theirs = challenger[sample.id], incumbent[sample.id]
            wins = 0
            for n in range(self.comparisons):
                first = "challenger" if n % 2 == 0 else "incumbent"
                if not theirs.usable:
                    # an output the app couldn't use is worse than one it can, without asking the judge
                    verdict = Verdict(sample.id, first, "challenger", "the other output couldn't be used by the app")
                else:
                    verdict = self._verdict(sample, mine, theirs, first)
                comparison.verdicts.append(verdict)
                wins += verdict.preferred == "challenger"
            if sample.id in by_result:
                reasons = " | ".join(v.reason for v in comparison.verdicts if v.sample_id == sample.id)
                self.tracker.score(by_result[sample.id], "preferred", wins / self.comparisons, reasons)
        return comparison

    def _verdict(self, sample: quiz.Sample, mine: quiz.Output, theirs: quiz.Output, first: str) -> Verdict:
        a, b = (mine, theirs) if first == "challenger" else (theirs, mine)
        user = (
            f"# Requirements\n\n{self.requirements}\n\n# The request\n\nA quiz of {sample.count} questions on:\n\n"
            f"{quiz.material(sample)}\n\n# Output A\n\n{quiz.show(a, sample)}\n\n# Output B\n\n{quiz.show(b, sample)}"
        )
        answer, _ = self.models.parsed(
            "judge", self.roles["judge"], (PROMPTS / "judge.md").read_text(), user, JUDGE_SCHEMA, JUDGE_MAX_TOKENS
        )
        shown_first = answer["preferred"] == "A"
        preferred = first if shown_first else ("incumbent" if first == "challenger" else "challenger")
        return Verdict(sample.id, first, preferred, answer["reason"])

    def _optimize(self, best: Version, number: int) -> Version:
        outputs = self._outputs[best.id]
        shown = "\n\n".join(
            f"## Request {n}: a quiz of {s.count} questions\n\n### Its material\n\n{quiz.material(s)}\n\n"
            f"### What the agent wrote\n\n{quiz.show(outputs[s.id], s)}"
            for n, s in enumerate(self.samples, start=1)
        )
        placeholders = ", ".join("{" + key + "}" for key in self.agent.placeholders) or "none"
        user = (
            f"# Requirements\n\n{self.requirements}\n\n# The prompt as it is\n\n{best.text}\n\n"
            f"# Its placeholders, to keep\n\n{placeholders}\n\n# The agent's outputs with it\n\n{shown}"
        )
        answer, _ = self.models.parsed(
            "optimizer",
            self.roles["optimizer"],
            (PROMPTS / "optimizer.md").read_text(),
            user,
            OPTIMIZER_SCHEMA,
            OPTIMIZER_MAX_TOKENS,
            effort="high",
        )
        text = answer["prompt"].rstrip("\n")
        return Version(f"r{number}", text, sha256(text), number, best.id, answer["analysis"], answer["modification"])

    # the experiment

    def run(self, rounds: int, store) -> Experiment:
        start = self._start()
        try:
            self.tracker.dataset(self.dataset, self._items(self.samples))
            if self.held_out_samples:
                self.tracker.dataset(self.held_out_dataset, self._items(self.held_out_samples))
            self._add_version(start)
            first = self._run(start, self.samples, self.dataset, f"round 0 · {start.id}")
            self.record.rounds.append(
                Round(
                    0,
                    start.id,
                    start.id,
                    {k: _output_record(v) for k, v in self._outputs[start.id].items()},
                    best=start.id,
                    cost=self.models.budget.total,
                    run_url=first.url,
                )
            )
            store.save(self.record)
            for number in range(1, rounds + 1):
                self._round(number)
                store.save(self.record)
            self.record.result = self.record.best
            self._check_held_out()
        except OverBudget as e:
            self.record.stopped = f"stopped at its budget: {e}"
            self.record.result = self.record.best
        finally:
            self.record.usage = {role: asdict(usage) for role, usage in self.models.budget.spent.items()}
            if self.record.result:
                best = self.record.versions[self.record.result]
                self.tracker.label_best(self.agent.prompt, best.langfuse_version)
            self.tracker.flush()
            store.save(self.record)
        return self.record

    def play(self, text: str, store) -> Round:
        """a round with a version given, not written by the optimizer, against the starting version"""
        start = self._start()
        self.tracker.dataset(self.dataset, self._items(self.samples))
        self._add_version(start)
        self._run(start, self.samples, self.dataset, f"round 0 · {start.id}")
        text = text.rstrip("\n")
        self._round(1, Version("r1", text, sha256(text), 1, start.id, None, "given, not written by the optimizer"))
        self.tracker.flush()
        store.save(self.record)
        return self.record.rounds[-1]

    def _start(self) -> Version:
        start = Version(f"v{self.agent.start}", self.agent.start_text, sha256(self.agent.start_text), 0)
        self.record.best = start.id
        return start

    def _round(self, number: int, new: Version | None = None) -> None:
        before = self.models.budget.total
        best = self.record.versions[self.record.best]
        new = new or self._optimize(best, number)
        self._add_version(new)
        played = Round(number, new.id, best.id)
        self.record.rounds.append(played)
        try:
            quiz.system_prompt(new.text, self.agent.placeholders)
        except quiz.MissingPlaceholder as e:
            played.lost = str(e)
        else:
            result = self._run(new, self.samples, self.dataset, f"round {number} · {new.id}")
            played.run_url = result.url
            outputs = self._outputs[new.id]
            played.outputs = {k: _output_record(v) for k, v in outputs.items()}
            unusable = [o for o in outputs.values() if not o.usable]
            if unusable:
                played.lost = f"the app couldn't use its quiz of {len(unusable)} sample(s): {unusable[0].unreadable}"
            else:
                played.comparison = self._judge(self.samples, outputs, self._outputs[best.id], result.items)
                if played.comparison.challenger_preferred:
                    self.record.best = new.id
        played.best = self.record.best
        played.cost = self.models.budget.total - before

    def _check_held_out(self) -> None:
        result = self.record.versions[self.record.result]
        held = HeldOut([s.id for s in self.held_out_samples])
        self.record.held_out = held
        if result.round == 0:
            held.skipped = f"the experiment found nothing better than {self.record.start}"
            return
        if not self.held_out_samples:
            held.skipped = "no samples were held out"
            return
        start = self.record.versions[self.record.rounds[0].version]
        self._run(start, self.held_out_samples, self.held_out_dataset, f"held out · {start.id}")
        checked = self._run(result, self.held_out_samples, self.held_out_dataset, f"held out · {result.id}")
        held.start_outputs = {k: _output_record(v) for k, v in self._outputs[start.id].items()}
        held.result_outputs = {k: _output_record(v) for k, v in self._outputs[result.id].items()}
        held.comparison = self._judge(
            self.held_out_samples, self._outputs[result.id], self._outputs[start.id], checked.items
        )
        held.recommended = held.comparison.challenger_preferred


def check_start(agent: Agent, samples: list[quiz.Sample]) -> None:
    """the app's quizzes of the samples were written with the version the experiment starts from"""
    others = sorted({p for s in samples for p in s.prompts} - {agent.ref})
    if others:
        raise ExperimentError(
            f"the app's quizzes of these samples were written with {', '.join(others)}, not {agent.ref}: copy that"
            f" version of the app's prompt into {agent.directory.relative_to(ROOT)}/prompts and start from it"
        )


def new_id(prefix: str = "") -> str:
    return f"{prefix}{datetime.now(UTC):%Y%m%dT%H%M%SZ}"


class Store:
    """keeps an experiment in runs/<id>/ of its agent: experiment.json, and each version of the prompt as a file"""

    def __init__(self, agent: Agent):
        self.agent = agent

    def directory(self, experiment_id: str) -> Path:
        return self.agent.directory / "runs" / experiment_id

    def save(self, experiment: Experiment) -> Path:
        directory = self.directory(experiment.id)
        (directory / "versions").mkdir(parents=True, exist_ok=True)
        for version in experiment.versions.values():
            (directory / "versions" / f"{version.id}.md").write_text(version.text + "\n", encoding="utf-8")
        record = asdict(experiment)
        record["recommended"] = experiment.recommended
        (directory / "experiment.json").write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
        return directory

    def load(self, experiment_id: str) -> dict:
        return json.loads((self.directory(experiment_id) / "experiment.json").read_text(encoding="utf-8"))


def optimize(
    agent: Agent,
    engine,
    student: str,
    tracker,
    rounds: int,
    samples: int,
    held_out: int,
    seed: int,
    comparisons: int,
    budget: float | None = None,
    model_overrides: dict[str, Model] | None = None,
    experiment_id: str | None = None,
    rated_down: bool = False,
) -> Experiment:
    """an experiment of SPO on the agent's prompt, on samples of the student's drawn from the app's database"""
    optimization = prepare(
        agent,
        engine,
        student,
        tracker,
        samples,
        held_out,
        seed,
        comparisons,
        budget,
        model_overrides,
        experiment_id,
        rated_down,
    )
    return optimization.run(rounds, Store(agent))


def prepare(
    agent: Agent,
    engine,
    student: str,
    tracker,
    samples: int,
    held_out: int,
    seed: int,
    comparisons: int,
    budget: float | None = None,
    model_overrides: dict[str, Model] | None = None,
    experiment_id: str | None = None,
    rated_down: bool = False,
) -> Optimization:
    """an experiment ready to run: its samples drawn, and checked before anything is run"""
    agent.requirements()  # raises if the agent has no requirements
    found = quiz.samples(engine, student, rated_down)
    if not found:
        raise ExperimentError(f"the app has no samples of {agent.prompt!r} for the student {student!r}")
    drawn, kept_out = quiz.draw(found, samples, held_out, seed)
    check_start(agent, drawn + kept_out)
    models = Models(Budget(budget), tracker if tracker.langfuse else None)
    return Optimization(
        agent, models, tracker, drawn, kept_out, seed, comparisons, experiment_id or new_id(), model_overrides
    )
