"""qn-experiment: the experiments on the app's agents, from the command line.

qn-experiment samples quiz-creation-agent
qn-experiment optimize quiz-creation-agent --rounds 10 --samples 3 --held-out 10 --budget 5
qn-experiment show quiz-creation-agent <experiment>
"""

import argparse
import sys

from qubit_notes_experiments import ROOT, appdb, spo
from qubit_notes_experiments.agents import quiz
from qubit_notes_experiments.models import Model
from qubit_notes_experiments.settings import Settings, SettingsError
from qubit_notes_experiments.tracking import tracker


def _agent(name: str) -> spo.Agent:
    """the agent by its name, e.g. quiz-creation-agent, or by its prompt's, e.g. quiz"""
    agents = ROOT / "experiments" / "agents"
    for directory in sorted(agents.glob("*/spo")):
        agent = spo.Agent.load(directory.parent.name)
        if name in (agent.name, agent.prompt):
            return agent
    raise spo.ExperimentError(f"there's no agent here for {name!r}")


def _models(pairs: list[str]) -> dict[str, Model]:
    overrides = {}
    for pair in pairs:
        role, _, spec = pair.partition("=")
        if role not in ("agent", "optimizer", "judge") or not spec:
            raise ValueError(f"give a model as agent=, optimizer= or judge=<provider:model>, not {pair!r}")
        overrides[role] = Model.parse(spec)
    return overrides


def samples(args, settings: Settings) -> None:
    agent = _agent(args.agent)
    engine = appdb.connect(settings.need("app_database_url"))
    found = quiz.samples(engine, args.student or settings.need("student"), args.rated_down)
    print(f"{len(found)} samples of {agent.prompt!r}")
    for sample in found:
        sections = ", ".join(f"{s.filename} › {s.title}" for s in sample.sections)
        print(f"  {sample.id}  {sample.count} questions on {sections}  ({', '.join(sample.prompts) or 'no version'})")


def optimize(args, settings: Settings) -> None:
    agent = _agent(args.agent)
    engine = appdb.connect(settings.need("app_database_url"))
    defaults = agent.defaults
    experiment = spo.optimize(
        agent,
        engine,
        args.student or settings.need("student"),
        tracker(settings),
        rounds=args.rounds or defaults.get("rounds", 10),
        samples=args.samples or defaults.get("samples", 3),
        held_out=args.held_out if args.held_out is not None else defaults.get("held_out", 10),
        seed=args.seed,
        comparisons=args.comparisons or defaults.get("comparisons", 4),
        budget=args.budget,
        model_overrides=_models(args.model),
        rated_down=args.rated_down,
    )
    _summary(experiment)


def _summary(experiment: spo.Experiment) -> None:
    print(f"experiment {experiment.id} of {experiment.start}")
    for played in experiment.rounds[1:]:
        if played.lost:
            outcome = f"lost: {played.lost}"
        else:
            c = played.comparison
            outcome = f"preferred in {c.challenger_wins} of {len(c.verdicts)} verdicts"
        print(f"  round {played.number}: {played.version} {outcome}; best {played.best} (${played.cost:.4f})")
    if experiment.stopped:
        print(f"  {experiment.stopped}")
    held = experiment.held_out
    if held and held.skipped:
        print(f"  held out: {held.skipped}")
    elif held and held.comparison:
        c = held.comparison
        verdict = "recommended" if held.recommended else "not recommended"
        print(f"  held out: {experiment.result} preferred in {c.challenger_wins} of {len(c.verdicts)}: {verdict}")
    total = sum(u["cost"] for u in experiment.usage.values())
    print(f"  result {experiment.result}; cost ${total:.4f}")


def show(args, settings: Settings) -> None:
    agent = _agent(args.agent)
    record = spo.Store(agent).load(args.experiment)
    print(
        f"experiment {record['id']} of {record['start']}: result {record['result']},"
        f" {'recommended' if record['recommended'] else 'not recommended'}"
    )
    print(f"kept in {spo.Store(agent).directory(record['id']).relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qn-experiment", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listed = commands.add_parser("samples", help="the samples of an agent in the app's database")
    listed.add_argument("agent")
    listed.add_argument("--student")
    listed.add_argument("--rated-down", action="store_true")
    listed.set_defaults(run=samples)

    run = commands.add_parser("optimize", help="optimize an agent's prompt by SPO")
    run.add_argument("agent")
    run.add_argument("--student")
    run.add_argument("--rounds", type=int)
    run.add_argument("--samples", type=int)
    run.add_argument("--held-out", type=int)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--comparisons", type=int)
    run.add_argument("--budget", type=float, help="the most the experiment may cost, in dollars")
    run.add_argument("--model", action="append", default=[], help="e.g. judge=anthropic:claude-sonnet-5-5")
    run.add_argument("--rated-down", action="store_true", help="only samples of quizzes rated down")
    run.set_defaults(run=optimize)

    shown = commands.add_parser("show", help="an experiment kept here")
    shown.add_argument("agent")
    shown.add_argument("experiment")
    shown.set_defaults(run=show)

    args = parser.parse_args(argv)
    try:
        args.run(args, Settings.from_env())
    except (SettingsError, spo.ExperimentError, quiz.NotEnoughSamples, appdb.NotReadOnly, ValueError) as e:
        print(f"qn-experiment: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
