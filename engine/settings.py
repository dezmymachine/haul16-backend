"""All configuration, read from the environment (or a local .env file). Real secrets live in the host's variables, never in git."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    haul16_env: str = "dev"                   # dev | prod (prod turns demo routes off)
    haul16_port: int = 8000
    haul16_ct: bool = True                    # follow CT logs inside the API process
    embedded_worker: bool = True              # run the job worker inside the API process (set false when a separate worker service runs)
    haul16_dry_run: bool = True               # nothing is ever sent while true
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database (Neon in production). TEST_DATABASE_URL is used by the test suite only.
    database_url: str = "postgresql://haul16:haul16@127.0.0.1:55433/haul16"
    test_database_url: str | None = None

    # LLM (provider adapter in engine/llm.py)
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Collectors. Every one is optional: a missing key skips that collector and records why.
    firecrawl_api_key: str | None = None
    apify_token: str | None = None
    apify_linkedin_actor: str | None = None   # Apify actor id for LinkedIn company pages, e.g. "owner~actor-name"
    apify_linkedin_input: str = '{"startUrls":[{"url":"{url}"}]}'  # JSON actor input; {url} is replaced
    apify_social_actor: str | None = None     # Apify actor id for generic public social profile lookups
    search_provider: str = "brave"
    search_api_key: str | None = None
    urlscan_api_key: str | None = None
    meta_ad_library_token: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.haul16_env == "prod"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
