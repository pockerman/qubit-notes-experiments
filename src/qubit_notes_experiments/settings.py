"""The experiments' settings, from the environment or the repository's .env (see .env.example)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from qubit_notes_experiments import ROOT


class SettingsError(Exception):
    """A setting the experiments need isn't given."""


@dataclass(frozen=True)
class Settings:
    # the app's database, which is only ever read
    app_database_url: str | None
    # whose material samples are drawn from, by their id in the app's database
    student: str | None
    # the cheapest model, which the scenarios run every agent on, e.g. anthropic:claude-haiku-5-5
    test_model: str | None
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str
    # the app's own Langfuse project, which the experiments never trace into
    app_langfuse_public_key: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        get = lambda name: os.environ.get(name) or None  # noqa: E731
        return cls(
            app_database_url=get("QUBIT_NOTES_EXPERIMENT_APP_DATABASE_URL"),
            student=get("QUBIT_NOTES_EXPERIMENT_STUDENT"),
            test_model=get("QUBIT_NOTES_EXPERIMENT_TEST_MODEL"),
            langfuse_public_key=get("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=get("LANGFUSE_SECRET_KEY"),
            langfuse_host=get("LANGFUSE_HOST") or "https://cloud.langfuse.com",
            app_langfuse_public_key=get("QUBIT_NOTES_EXPERIMENT_APP_LANGFUSE_PUBLIC_KEY"),
        )

    @property
    def langfuse(self) -> bool:
        """whether experiments are tracked in Langfuse, as well as kept here"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def need(self, name: str) -> str:
        value = getattr(self, name)
        if not value:
            raise SettingsError(f"set QUBIT_NOTES_EXPERIMENT_{name.upper()} (see .env.example)")
        return value
