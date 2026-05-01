from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # GitHub — token must have Issues write + Contents read permissions
    github_token: str

    # JFrog Xray webhook shared secret (set in Xray webhook config)
    # Used to verify the HMAC-SHA256 signature on incoming webhook requests.
    xray_webhook_secret: str = ""

    # JFrog Xray REST API (used to enrich CVE data beyond what the webhook carries)
    jfrog_url: str = ""          # e.g. https://yourinstance.jfrog.io
    jfrog_username: str = ""
    jfrog_password: str = ""
    jfrog_api_key: str = ""

    # Default target repo (used when X-Repo-Owner / X-Repo-Name headers are absent)
    default_repo_owner: str = ""
    default_repo_name: str = ""

    # GitHub Issue config
    issue_label: str = "security"            # label applied to all raised issues
    issue_label_autofix: str = "copilot-autofix"   # signals Copilot to suggest a fix
    issue_assignees: list[str] = []          # GitHub usernames to auto-assign

    # Which manifest files to scan in the target repo (in order of preference)
    manifest_paths: list[str] = ["requirements.txt", "pyproject.toml", "Pipfile"]

    # Minimum Xray severity to act on (Low | Medium | High | Critical)
    min_severity: str = "Medium"


settings = Settings()
