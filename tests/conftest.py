"""The scenarios run on the app's database as it is, read only, and with real models: the cheapest one
(QUBIT_NOTES_EXPERIMENT_TEST_MODEL) for every agent. Without the app's database, the student or the model, they're
skipped; and each scenario that needs more samples than the database has is skipped, saying what to do in the app.

An experiment asked for with the same settings by several scenarios is run once, as they check different things of
it: each costs real money."""

import pytest

from qubit_notes_experiments import appdb, spo
from qubit_notes_experiments.models import Model
from qubit_notes_experiments.settings import Settings
from qubit_notes_experiments.tracking import tracker as make_tracker

# the tables an experiment reads, which mustn't change
READ = ["quizzes", "platform_sections", "platform_documents", "answer_ratings", "conversation_turns"]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="session")
def engine(settings):
    if not settings.app_database_url:
        pytest.skip("set QUBIT_NOTES_EXPERIMENT_APP_DATABASE_URL to the app's database")
    return appdb.connect(settings.app_database_url)


@pytest.fixture(scope="session")
def student(settings) -> str:
    if not settings.student:
        pytest.skip("set QUBIT_NOTES_EXPERIMENT_STUDENT to the student whose samples are drawn")
    return settings.student


@pytest.fixture(scope="session")
def test_model(settings) -> Model:
    if not settings.test_model:
        pytest.skip("set QUBIT_NOTES_EXPERIMENT_TEST_MODEL to the cheapest model, e.g. anthropic:claude-haiku-5-5")
    return Model.parse(settings.test_model)


@pytest.fixture(scope="session")
def tracker(settings):
    return make_tracker(settings)


@pytest.fixture(scope="session")
def experiments() -> dict:
    """the experiments run so far, by their settings, with the app's database before and after each"""
    return {}


@pytest.fixture
def context() -> dict:
    return {}


@pytest.fixture
def agent() -> spo.Agent:
    return spo.Agent.load("quiz-creation-agent")
