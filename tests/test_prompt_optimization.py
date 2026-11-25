"""Steps of features/prompt_optimization.feature."""

import json
import shutil

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from qubit_notes_experiments import appdb, cli, spo
from qubit_notes_experiments.agents import quiz
from tests.conftest import READ

pytestmark = pytest.mark.real_models

scenarios("prompt_optimization.feature")


def _models(test_model) -> dict:
    return {"agent": test_model, "optimizer": test_model, "judge": test_model}


def _samples(engine, student, rated_down=False) -> list[quiz.Sample]:
    return quiz.samples(engine, student, rated_down)


# the app's database


@given("the app's database, read only")
def database_read_only(engine):
    assert appdb.read_only(engine)


@given("the student whose samples are drawn")
def the_student(student):
    assert student


@given(parsers.parse('the prompt "{prompt}" has its requirements'))
def has_requirements(agent, prompt):
    assert agent.prompt == prompt
    assert agent.requirements().strip()


@given(parsers.re(r'the student has at least (?P<count>\d+) samples? of "(?P<prompt>[^"]+)" in the app'))
def at_least(engine, student, count, prompt):
    found = _samples(engine, student)
    if len(found) < int(count):
        pytest.skip(
            f"the app's database has {len(found)} samples of {prompt!r} for the student, and the scenario needs"
            f" {count}: ask for quizzes in the app, on different sections, until it has"
        )


@given("the student has rated at least 1 quiz of the assistant down in the app")
def rated_one_down(engine, student):
    if not appdb.rated_down_quizzes(engine, student):
        pytest.skip(
            "the student hasn't rated a quiz of the assistant down: ask it for one in the app, and rate it down"
        )


@given(parsers.parse('the agent of "{prompt}" has no requirements'))
def no_requirements(context, tmp_path, agent, prompt):
    copy = tmp_path / "experiments" / "agents" / agent.name / "spo"
    shutil.copytree(agent.directory, copy, ignore=shutil.ignore_patterns("runs", "requirements.md"))
    context["agent"] = spo.Agent.load(agent.name, root=tmp_path)


# optimizing


def _optimize(context, experiments, engine, student, tracker, test_model, prompt, rounds, samples, held, **options):
    try:
        agent = context.get("agent") or cli._agent(prompt)
    except spo.ExperimentError as e:
        context["error"] = str(e)
        return
    key = (agent.directory, rounds, samples, held, tuple(sorted(options.items())))
    if key not in experiments:
        before = appdb.fingerprint(engine, READ)
        try:
            experiment = spo.optimize(
                agent,
                engine,
                student,
                tracker,
                rounds=rounds,
                samples=samples,
                held_out=held,
                seed=0,
                comparisons=options.get("comparisons", 4),
                budget=options.get("budget"),
                model_overrides=_models(test_model),
                experiment_id=spo.new_id("test-"),
            )
            experiments[key] = {"experiment": experiment, "error": None}
        except (spo.ExperimentError, quiz.NotEnoughSamples) as e:
            experiments[key] = {"experiment": None, "error": str(e)}
        experiments[key]["before"], experiments[key]["after"] = before, appdb.fingerprint(engine, READ)
        experiments[key]["agent"] = agent
    context.update(experiments[key])


@when(
    parsers.re(
        r'I optimize the prompt "(?P<prompt>[^"]+)" for (?P<rounds>\d+) rounds?, with (?P<samples>\d+) samples and'
        r" (?P<held>\d+) held out, of the student(?:, the judge comparing each (?P<comparisons>\d+) times)?"
        r"(?:, within a budget of \$(?P<budget>[\d.]+))?$"
    )
)
def optimize(
    context, experiments, engine, student, tracker, test_model, prompt, rounds, samples, held, comparisons, budget
):
    options = {}
    if comparisons:
        options["comparisons"] = int(comparisons)
    if budget:
        options["budget"] = float(budget)
    _optimize(
        context,
        experiments,
        engine,
        student,
        tracker,
        test_model,
        prompt,
        int(rounds),
        int(samples),
        int(held),
        **options,
    )


@when(
    parsers.parse(
        'I optimize the prompt "{prompt}" for 1 round, with all the student\'s samples of "{same}" and 1 held out'
    )
)
def optimize_all(context, experiments, engine, student, tracker, test_model, prompt, same):
    context["available"] = len(_samples(engine, student))
    _optimize(context, experiments, engine, student, tracker, test_model, prompt, 1, context["available"], 1)


def _experiment(context) -> spo.Experiment:
    assert context.get("error") is None, context["error"]
    return context["experiment"]


@then("the experiment read the app's database with a connection that can only read")
def read_with_read_only(engine, context):
    _experiment(context)
    assert appdb.read_only(engine)


@then("nothing in the app's database changed")
def nothing_changed(context):
    assert context["before"] == context["after"]


@then(
    parsers.parse(
        "the experiment started from the version of \"{prompt}\" the app's database records the student's quizzes"
        " were written with, with its hash"
    )
)
def started_from_app_version(context, agent, prompt):
    experiment = _experiment(context)
    assert experiment.start == agent.ref == f"{prompt}@{agent.start}"
    assert set(experiment.app_versions) <= {agent.ref}, experiment.app_versions
    assert experiment.start_sha256 == spo.sha256(agent.start_text)
    assert experiment.versions[f"v{agent.start}"].text == agent.start_text


@then(parsers.parse('I am told there\'s no agent here for "{prompt}"'))
def told_no_agent(context, prompt):
    assert context["error"] == f"there's no agent here for {prompt!r}", context["error"]


@then(parsers.parse("I am told to write {path} first"))
def told_to_write(context, path):
    assert context["error"] and f"write {path} first" in context["error"], context["error"]


# the samples


@when(
    parsers.re(r'I draw (?P<count>\d+) samples of the prompt "quiz", of the student(?:, with the seed (?P<seed>\d+))?$')
)
@given(
    parsers.re(
        r'I have drawn (?P<count>\d+) samples of the prompt "quiz", of the student, with the seed (?P<seed>\d+)$'
    )
)
def draw(context, engine, student, count, seed):
    drawn, _ = quiz.draw(_samples(engine, student), int(count), 0, int(seed or 0))
    context.setdefault("draws", []).append(drawn)


@then(
    "each sample is the text of the student's material that a quiz was asked on, as the app has it, and its number of"
    " questions"
)
def samples_are_texts(context, engine, student):
    requests = {r.id: r for r in appdb.quiz_requests(engine, student)}
    for sample in context["draws"][-1]:
        assert sample.count > 0 and sample.quizzes
        for quiz_id in sample.quizzes:
            assert requests[quiz_id].count == sample.count
            assert [(s["document_id"], s["section"]) for s in requests[quiz_id].sources] == [
                (s.document_id, s.section) for s in sample.sections
            ]
        for section in sample.sections:
            assert appdb.section_texts(engine, section.document_id)[section.section] == section.content


@then("they are the same samples")
def same_samples(context):
    first, second = context["draws"]
    assert [s.id for s in first] == [s.id for s in second]


@when(
    'I draw as many samples of the prompt "quiz", of the student, from the quizzes rated down, as there are, and 1 more'
)
def draw_rated_down(context, engine, student):
    found = _samples(engine, student, rated_down=True)
    context["rated_down"] = found
    try:
        quiz.draw(found, len(found) + 1, 0, 0)
    except quiz.NotEnoughSamples as e:
        context["error"] = str(e)


@then("every sample is of a quiz the student rated down")
def all_rated_down(context, engine, student):
    down = appdb.rated_down_quizzes(engine, student)
    assert context["rated_down"]
    assert all(set(sample.quizzes) <= down for sample in context["rated_down"])


@then("I am told how many there were, fewer than asked")
def told_how_many(context):
    count = len(context["rated_down"])
    assert f"the app has {count} samples" in context["error"], context["error"]


@then("every sample is of the student's own material, or of a course shared with them")
def own_material(context, engine, student):
    for sample in context["draws"][-1]:
        for section in sample.sections:
            assert student in appdb.readers(engine, section.document_id)


# the rounds


def _round(context, number: int) -> spo.Round:
    experiment = _experiment(context)
    return experiment.rounds[number]


def _judged(played: spo.Round) -> spo.Comparison:
    if played.lost:
        pytest.skip(f"round {played.number} was lost, so nothing was judged: {played.lost}")
    return played.comparison


@then(
    parsers.parse(
        'round 1 has a new version of "{prompt}", written by the optimizer from the starting version, its outputs and'
        " the requirements"
    )
)
def new_version(context, prompt):
    experiment = _experiment(context)
    version = experiment.versions[_round(context, 1).version]
    assert version.round == 1 and version.parent == experiment.rounds[0].version
    assert version.analysis and version.modification and version.text.strip()


@then(parsers.parse("the new version was run on the same {count:d} samples as the starting version"))
def same_samples_run(context, count):
    played = _round(context, 1)
    if played.lost and "fill in" in played.lost:
        pytest.skip(f"round 1 was lost before it ran: {played.lost}")
    assert set(played.outputs) == set(_round(context, 0).outputs) == set(_experiment(context).samples)
    assert len(played.outputs) == count


@then(
    parsers.parse(
        "the judge compared each sample's two outputs {times:d} times, {half:d} with the new version's first and"
        " {other:d} with the best's first"
    )
)
def compared_both_orders(context, times, half, other):
    comparison = _judged(_round(context, 1))
    for sample_id in _experiment(context).samples:
        verdicts = [v for v in comparison.verdicts if v.sample_id == sample_id]
        assert len(verdicts) == times
        assert sum(v.first == "challenger" for v in verdicts) == half
        assert sum(v.first == "incumbent" for v in verdicts) == other


@then("each verdict is kept with the judge's reason")
def verdict_reasons(context):
    comparison = _judged(_round(context, 1))
    assert all(v.reason.strip() for v in comparison.verdicts)


@then(
    "after each round, the best so far is the new version if it was preferred in more than half of the round's"
    " verdicts, else the best before it"
)
def best_by_majority(context):
    experiment = _experiment(context)
    for before, played in zip(experiment.rounds, experiment.rounds[1:], strict=False):
        assert played.against == before.best
        if played.lost:
            assert played.best == before.best
        else:
            wins = sum(v.preferred == "challenger" for v in played.comparison.verdicts)
            expected = played.version if wins * 2 > len(played.comparison.verdicts) else before.best
            assert played.best == expected


@given(parsers.parse('a version of "{prompt}" without the placeholder {placeholder}'))
def version_without(context, agent, prompt, placeholder):
    assert placeholder in agent.start_text
    context["version"] = agent.start_text.replace(f"exactly {placeholder} choices", "four choices").replace(
        placeholder, "four"
    )


@given(parsers.parse('a version of "{prompt}" that asks for each question\'s section by its title, not its reference'))
def version_by_title(context, agent, prompt):
    context["version"] = (
        agent.start_text.replace(
            "Every question\nnames the section it comes from by its reference, e.g. S3.",
            "Every question names the section it comes from by its title, e.g. Introduction, and never by its"
            " reference.",
        )
        + "\nNever write a section's reference, e.g. S3: write its title."
    )


@when(
    parsers.parse(
        'I run a round of "{prompt}" with that version, against the starting version, with {count:d} samples of the'
        " student"
    )
)
def run_round(context, engine, student, tracker, test_model, agent, prompt, count):
    optimization = spo.prepare(
        agent,
        engine,
        student,
        tracker,
        samples=count,
        held_out=0,
        seed=0,
        comparisons=4,
        model_overrides=_models(test_model),
        experiment_id=spo.new_id("test-"),
    )
    context["round"] = optimization.play(context["version"], spo.Store(agent))


@then(parsers.parse("the round is lost, as the version doesn't fill in {placeholder}"))
def lost_placeholder(context, placeholder):
    played = context["round"]
    assert played.lost == f"the version doesn't fill in {placeholder}", played.lost


@then("its outputs weren't judged")
def not_judged(context):
    played = context["round"]
    assert played.comparison is None and not played.outputs


@then("the round is lost, as the app couldn't use its quizzes")
def lost_unusable(context):
    played = context["round"]
    assert played.lost and played.lost.startswith("the app couldn't use its quiz"), played.lost
    assert played.comparison is None


@then(parsers.parse("the experiment has {count:d} rounds"))
def has_rounds(context, count):
    assert len(_experiment(context).rounds) - 1 == count


@then("its result is the version that was best after the last round, with the round it was written in")
def result_is_last_best(context):
    experiment = _experiment(context)
    assert experiment.result == experiment.rounds[-1].best
    result = experiment.versions[experiment.result]
    assert result.round == next((r.number for r in experiment.rounds if r.version == result.id), 0)


# the held-out check


@then(
    parsers.parse(
        'the held-out check was made on {held:d} samples of "{prompt}", none of them among the {count:d} it was'
        " optimized on"
    )
)
def held_out_disjoint(context, held, prompt, count):
    experiment = _experiment(context)
    assert len(experiment.held_out_samples) == held and len(experiment.samples) == count
    assert not set(experiment.held_out_samples) & set(experiment.samples)
    assert experiment.held_out.samples == experiment.held_out_samples


@then(
    "if the result is a new version, the judge compared its outputs with the starting version's on each held-out"
    " sample, in both orders"
)
def held_out_compared(context):
    experiment = _experiment(context)
    held = experiment.held_out
    if experiment.versions[experiment.result].round == 0:
        assert held.skipped and held.comparison is None
        return
    for sample_id in experiment.held_out_samples:
        verdicts = [v for v in held.comparison.verdicts if v.sample_id == sample_id]
        assert len(verdicts) == experiment.comparisons
        assert sum(v.first == "challenger" for v in verdicts) == experiment.comparisons // 2


@then(
    "the result is recommended if it was preferred in more than half of those verdicts, and not recommended otherwise"
)
def recommended_by_majority(context):
    experiment = _experiment(context)
    held = experiment.held_out
    if held.comparison is None:
        assert not experiment.recommended
        return
    wins = sum(v.preferred == "challenger" for v in held.comparison.verdicts)
    assert experiment.recommended == (wins * 2 > len(held.comparison.verdicts))


@given(
    parsers.parse('an experiment of "{prompt}" whose result is its starting version, as no round found a better one')
)
def result_is_start(context, engine, student, tracker, test_model, agent, prompt):
    if len(_samples(engine, student)) < 2:
        pytest.skip("the scenario needs 2 samples of the quiz in the app's database")
    optimization = spo.prepare(
        agent,
        engine,
        student,
        tracker,
        samples=1,
        held_out=1,
        seed=0,
        comparisons=4,
        model_overrides=_models(test_model),
        experiment_id=spo.new_id("test-"),
    )
    start = optimization._start()
    optimization.record.versions[start.id] = start
    optimization.record.result = start.id
    context["optimization"] = optimization


@when("its result is checked on held-out samples")
def check_held_out(context):
    context["optimization"]._check_held_out()


@then("no held-out check is made, and I am told the experiment found nothing better than its starting version")
def no_held_out_check(context, agent):
    optimization = context["optimization"]
    held = optimization.record.held_out
    assert held.comparison is None and not held.start_outputs and not held.result_outputs
    assert held.skipped == f"the experiment found nothing better than {agent.ref}"
    assert optimization.models.budget.total == 0


@then(
    parsers.parse(
        'I am told how many samples of "{prompt}" the student has, fewer than asked for, before anything is run'
    )
)
def told_too_few(context, prompt):
    available = context["available"]
    assert context["experiment"] is None
    assert f"the app has {available} samples of the quiz, fewer than the {available + 1} asked for" in context["error"]


# tracking


def _langfuse(tracker):
    if not tracker.langfuse:
        pytest.skip("set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY of the experiments' project")
    return tracker._client


@then("Langfuse's experiment project has the experiment's samples and held-out samples as datasets")
def langfuse_datasets(context, tracker):
    client = _langfuse(tracker)
    experiment = _experiment(context)
    for name, ids in [("samples", experiment.samples), ("held-out", experiment.held_out_samples)]:
        dataset = client.get_dataset(f"{experiment.agent}/{experiment.id}/{name}")
        assert sorted(i.metadata["sample_id"] for i in dataset.items) == sorted(ids)


@then("a run of it for each round and for the held-out check, with the judge's verdicts as scores")
def langfuse_runs(context, tracker):
    _langfuse(tracker)
    experiment = _experiment(context)
    assert all(r.run_url for r in experiment.rounds if r.outputs)


@then(parsers.parse('each version of "{prompt}" in its prompt management, the result labelled "best"'))
def langfuse_prompts(context, tracker, prompt):
    client = _langfuse(tracker)
    experiment = _experiment(context)
    for version in experiment.versions.values():
        assert client.get_prompt(prompt, version=version.langfuse_version).prompt == version.text
    assert client.get_prompt(prompt, label="best").prompt == experiment.versions[experiment.result].text


@then(
    "the agent's runs/<experiment>/ has, for each round, the version, its outputs, the judge's verdicts and reasons,"
    " and what it cost"
)
def kept_here(context):
    experiment = _experiment(context)
    directory = spo.Store(context["agent"]).directory(experiment.id)
    record = json.loads((directory / "experiment.json").read_text())
    context["record"] = record
    for played in record["rounds"]:
        assert (directory / "versions" / f"{played['version']}.md").is_file()
        assert "cost" in played and "outputs" in played
        if played["comparison"]:
            assert all(v["reason"] for v in played["comparison"]["verdicts"])


@then("the held-out check, and whether the result is recommended")
def kept_held_out(context):
    record = context["record"]
    assert record["held_out"] is not None and "recommended" in record


@then("the experiment's samples, seed, models and result")
def kept_settings(context):
    record = context["record"]
    assert record["samples"] and record["seed"] == 0 and record["models"] and record["result"]


@then("nothing was traced in the app's Langfuse project")
def not_in_app_project(context, settings, tracker):
    _experiment(context)
    if tracker.langfuse:
        assert settings.langfuse_public_key != settings.app_langfuse_public_key


# the budget


@then(parsers.parse("the experiment stops before it costs more than ${budget:g}"))
def stopped_within(context, budget):
    experiment = _experiment(context)
    assert experiment.stopped and experiment.stopped.startswith("stopped at its budget")
    assert sum(u["cost"] for u in experiment.usage.values()) <= budget


@then("it is kept as stopped at its budget, with the rounds it ran and the best version so far, and no held-out check")
def kept_stopped(context):
    experiment = _experiment(context)
    record = spo.Store(context["agent"]).load(experiment.id)
    assert record["stopped"]
    assert record["result"] == (experiment.rounds[-1].best if experiment.rounds else f"v{context['agent'].start}")
    assert record["held_out"] is None


@then("the experiment says what the optimizer, the prompt and the judge cost, in tokens and in dollars")
def says_cost(context):
    experiment = _experiment(context)
    for role in ("agent", "optimizer", "judge"):
        usage = experiment.usage.get(role)
        if role == "judge" and usage is None and experiment.rounds[-1].lost:
            pytest.skip(f"the round was lost, so the judge wasn't asked: {experiment.rounds[-1].lost}")
        assert usage and usage["input_tokens"] > 0 and usage["output_tokens"] > 0 and usage["cost"] > 0, role
