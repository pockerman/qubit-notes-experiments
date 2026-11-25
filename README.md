# Qubit Notes experiments

Experiments on the AI agents of [Qubit Notes](../study-assistant), kept apart from the app. They never import the
app or change it: they read what they need from its database, through a connection that can only read, and an
improved prompt is given to the app in a step of its own, later.

The first experiment is optimizing the prompt of the **quiz creation agent** by Self-Supervised Prompt Optimization
(SPO; Xiang et al., 2025, [arXiv:2502.06855](https://arxiv.org/abs/2502.06855)). The spec is
[features/prompt_optimization.feature](features/prompt_optimization.feature).

## How SPO works here

1. The starting prompt, the app's own (a copy, checked by its hash), is run on a few samples: real requests rebuilt
   from the app's database.
2. Each round, the optimizer writes a new version from the best so far, its outputs and the agent's requirements.
3. The new version runs on the same samples, and the judge compares its outputs with the best's, sample by sample,
   in both orders. The version preferred in more than half the verdicts is the best so far.
4. After the last round, the result is compared with the starting prompt on held-out samples, and is recommended
   for the app only if the judge prefers it there too.

A new version that drops a placeholder the app fills in, e.g. `{choices}`, or whose outputs the app couldn't use,
loses its round without being judged.

## Layout

```
experiments/agents/quiz-creation-agent/spo/
  agent.toml        the app's prompt and version, its placeholders, the models, SPO's defaults
  prompts/v1.md     the app's prompt, copied
  requirements.md   what a good quiz is, for the optimizer and the judge
  runs/<id>/        each experiment: experiment.json and every version of the prompt
src/qubit_notes_experiments/
  appdb.py          the app's database, read only
  agents/quiz.py    the quiz agent as the app runs it
  spo.py            the optimization
  tracking.py       Langfuse, or only the records here
```

## Running it

```bash
cp .env.example .env     # the app's database, the student, ANTHROPIC_API_KEY, Langfuse's keys
uv sync
uv run qn-experiment samples quiz-creation-agent
uv run qn-experiment optimize quiz-creation-agent --rounds 10 --samples 3 --held-out 10 --budget 5
uv run qn-experiment show quiz-creation-agent <experiment>
```

The agent runs on the app's model (Claude Sonnet 5.5), and the optimizer and the judge on Claude Opus 5.5; change any
with e.g. `--model judge=anthropic:claude-sonnet-5-5`. Every experiment stops before it could cost more than its
`--budget`, in dollars. With Langfuse's keys of a project of the experiments' own, each experiment is tracked there
too: its samples as datasets, each round as a run of one, the judge's verdicts as scores, and each version of the
prompt in prompt management.

## Tests

`uv run pytest` runs the scenarios on the app's database as it is, with real models: the cheapest one
(`QUBIT_NOTES_EXPERIMENT_TEST_MODEL`) for every agent, so each run costs a little. A scenario that needs more samples
than the database has is skipped, and says what to do in the app first.
