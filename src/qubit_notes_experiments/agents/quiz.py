"""The app's quiz creation agent, as backend/src/qubit_notes/domain/quizzes.py runs it: the material it is given, the
call of its model, and how the app reads the quiz the model writes. Kept the same as the app's, so a prompt that does
better here does better in the app.

One difference: the app asks for the quiz by forcing a tool, which Claude Sonnet 5.5 and Opus 5.5 refuse, so the
agent here asks for it as JSON of the same schema."""

import hashlib
import json
import random
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Engine

from qubit_notes_experiments import appdb
from qubit_notes_experiments.models import Model, ModelError, Models, OverBudget, Usage

# as the app: the most text a quiz is written from, each section given an equal share of it
MAX_CHARACTERS = 60_000
# as the app's llm_max_tokens
MAX_TOKENS = 4000
# as the app: a quiz the app can't use is asked for once more
ATTEMPTS = 2

# the quiz the model writes, as the app's WrittenQuiz: with no descriptions of its fields, as the app's has none
SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "integer"},
                    "source": {"type": "string"},
                },
                "required": ["question", "choices", "answer", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


class Section(BaseModel):
    document_id: str
    filename: str
    section: int
    title: str
    content: str


class Sample(BaseModel):
    """what the agent was given when a student asked for a quiz: the sections and how many questions"""

    id: str
    count: int
    sections: list[Section]
    # the app's quizzes asked for on it, and the prompt's versions they were written with
    quizzes: list[str]
    prompts: list[str]


class MissingPlaceholder(Exception):
    """A version of the prompt lacks a placeholder the app fills in."""


class NotEnoughSamples(Exception):
    """The app's database has fewer samples than asked for."""


def system_prompt(version: str, placeholders: dict[str, object]) -> str:
    """the prompt as the app fills it in: each {placeholder} replaced, and none of them missing"""
    for key, value in placeholders.items():
        placeholder = "{" + key + "}"
        if placeholder not in version:
            raise MissingPlaceholder(f"the version doesn't fill in {placeholder}")
        version = version.replace(placeholder, str(value))
    return version


def material(sample: Sample) -> str:
    """the material as the app gives it: each section labelled by its reference, e.g. S3"""
    share = max(500, MAX_CHARACTERS // max(1, len(sample.sections)))
    return "\n\n".join(
        f"[S{i}] {s.filename} › {s.title}\n{s.content[:share]}" for i, s in enumerate(sample.sections, start=1)
    )


def user_message(sample: Sample) -> str:
    return f"Write a quiz of {sample.count} questions on this material.\n\n{material(sample)}"


def readable(answer: str, sample: Sample, choices: int) -> list[dict]:
    """the questions the app would keep of the answer, as its _well_formed: raises ValueError if fewer than asked"""
    written = json.loads(answer)
    cited = {f"S{i}": s for i, s in enumerate(sample.sections, start=1)}
    valid = [
        q
        for q in written.get("questions", [])
        if len(q.get("choices", [])) == choices and 0 <= q.get("answer", -1) < choices and q.get("source") in cited
    ]
    if len(valid) < sample.count:
        raise ValueError(f"{len(valid)} of the {sample.count} questions asked for are well-formed")
    return valid[: sample.count]


@dataclass
class Output:
    """what a version of the prompt wrote for a sample: the quiz the app would keep, or why there is none"""

    sample_id: str
    questions: list[dict] | None
    # why the app couldn't use what the model wrote, if it couldn't
    unreadable: str | None
    usage: Usage

    @property
    def usable(self) -> bool:
        return self.questions is not None


def run(models: Models, model: Model, system: str, sample: Sample, choices: int) -> Output:
    """the agent's quiz for the sample, with the prompt filled in as ``system``, as the app asks for it"""
    usage = Usage()
    problem = None
    for _ in range(ATTEMPTS):
        try:
            completion = models.json("agent", model, system, user_message(sample), SCHEMA, MAX_TOKENS)
        except OverBudget:
            raise
        except ModelError as e:
            problem = str(e)
            continue
        usage.add(completion.usage)
        try:
            return Output(sample.id, readable(completion.text, sample, choices), None, usage)
        except (ValueError, json.JSONDecodeError) as e:
            problem = str(e)
    return Output(sample.id, None, problem, usage)


def show(output: Output, sample: Sample) -> str:
    """a quiz as the optimizer and the judge read it: each question, its choices, the correct one and its section"""
    if not output.usable:
        return f"(no quiz the app could use: {output.unreadable})"
    cited = {f"S{i}": s for i, s in enumerate(sample.sections, start=1)}
    lines = []
    for n, q in enumerate(output.questions, start=1):
        section = cited[q["source"]]
        lines.append(f"{n}. {q['question']}  (cites {q['source']}: {section.title})")
        lines += [f"   {'*' if i == q['answer'] else '-'} {choice}" for i, choice in enumerate(q["choices"])]
    return "\n".join(lines)


# the samples


def samples(engine: Engine, student: str, rated_down: bool = False) -> list[Sample]:
    """every distinct quiz the student asked for in the app, rebuilt from its database: the same sections and number of
    questions asked for again are one sample. Only the material the student may read is drawn from; with
    ``rated_down``, only the quizzes they rated down."""
    found: dict[str, Sample] = {}
    down = appdb.rated_down_quizzes(engine, student) if rated_down else None
    texts: dict[str, dict[int, str]] = {}
    readable_by: dict[str, set[str]] = {}
    for request in appdb.quiz_requests(engine, student):
        if down is not None and request.id not in down:
            continue
        sections = []
        for source in request.sources:
            document_id = source["document_id"]
            if document_id not in readable_by:
                readable_by[document_id] = appdb.readers(engine, document_id)
            if student not in readable_by[document_id]:
                break
            if document_id not in texts:
                texts[document_id] = appdb.section_texts(engine, document_id)
            content = texts[document_id].get(source["section"])
            if content is None:
                # the section is gone from the material since
                break
            sections.append(
                Section(**{k: source[k] for k in ("document_id", "filename", "section", "title")}, content=content)
            )
        else:
            if not sections:
                continue
            key = json.dumps([[s.document_id, s.section] for s in sections] + [request.count])
            sample_id = hashlib.sha256(key.encode()).hexdigest()[:12]
            sample = found.setdefault(
                sample_id, Sample(id=sample_id, count=request.count, sections=sections, quizzes=[], prompts=[])
            )
            sample.quizzes.append(request.id)
            if request.prompt and request.prompt not in sample.prompts:
                sample.prompts.append(request.prompt)
    return sorted(found.values(), key=lambda s: s.id)


def draw(all_samples: list[Sample], count: int, held_out: int, seed: int) -> tuple[list[Sample], list[Sample]]:
    """``count`` samples to optimize on and ``held_out`` others, none of them the same, drawn with the seed"""
    if count + held_out > len(all_samples):
        raise NotEnoughSamples(
            f"the app has {len(all_samples)} samples of the quiz, fewer than the {count + held_out} asked for"
            f" ({count} to optimize on and {held_out} held out)"
        )
    drawn = random.Random(seed).sample(all_samples, count + held_out)
    return drawn[:count], drawn[count:]
