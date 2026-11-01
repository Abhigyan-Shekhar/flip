"""Validated process configuration; credentials are never serialized by the API."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SWITCHCREW_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_path: Path = Path("data/switchcrew.sqlite3")
    checkpoint_path: Path = Path("data/checkpoints.sqlite3")
    memory_enabled: bool = False
    memory_project: str = "github.com/Abhigyan-Shekhar/flip"
    waggle_command: list[str] = Field(default_factory=list)
    waggle_url: str | None = None
    waggle_token: SecretStr | None = None
    api_token: SecretStr | None = None
    approval_credentials: dict[str, SecretStr] = Field(default_factory=dict)
    approval_user_ids: list[str] = Field(default_factory=list)
    engineering_agent_id: str = ""
    engineering_name: str = ""
    qa_agent_id: str = ""
    release_agent_id: str = ""
    release_name: str = ""
    release_repository: str = ""
    release_base: str = ""
    switch_base_url: str = "http://127.0.0.1:8080"
    switch_room_id: str = ""
    pm_participant_id: str = ""
    research_participant_id: str = ""
    pm_agent_id: str = ""
    research_agent_id: str = ""
    ingress_agent_id: str = ""
    pm_api_key: SecretStr | None = None
    research_api_key: SecretStr | None = None
    pm_name: str = "pm-agent"
    research_name: str = "research-agent"
    discord_bindings: list[dict] = Field(default_factory=list)
    discord_guild_ids: list[str] = Field(default_factory=list)
    discord_user_ids: list[str] = Field(default_factory=list)
    discord_participant_ids: list[str] = Field(default_factory=list)
    research_source_urls: list[str] = Field(default_factory=list)
    research_max_sources: int = Field(default=5, ge=1, le=10)
    poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)

    @field_validator("approval_credentials")
    @classmethod
    def human_credentials(cls, value):
        tokens = [token.get_secret_value() for token in value.values()]
        if any(not actor.strip() for actor in value) or any(len(t) < 32 for t in tokens):
            raise ValueError("Human approval identities and strong credentials are required")
        if len(tokens) != len(set(tokens)):
            raise ValueError("Each human requires an independent credential")
        return value

    @field_validator("waggle_url", mode="before")
    @classmethod
    def optional_waggle_url(cls, value):
        return None if value == "" else value

    @field_validator("api_token")
    @classmethod
    def strong_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("API token must have at least 32 characters")
        return value

    @field_validator("switch_base_url")
    @classmethod
    def valid_switch_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Switch URL must be an HTTP(S) origin")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Switch URL cannot contain credentials, query, or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "switch"}:
            raise ValueError("Remote Switch connections require HTTPS")
        return value.rstrip("/")
