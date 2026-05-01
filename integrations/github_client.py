"""GitHub API client using PyGithub.

Provides helpers for:
- Reading manifest files from a repo
- Creating GitHub Issues (for Copilot Autofix to act on)
"""
from __future__ import annotations

import base64
import logging

from github import Github, GithubException
from github.Repository import Repository

from config import settings

logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self) -> None:
        self._gh = Github(settings.github_token)

    def get_repo(self, owner: str, name: str) -> Repository:
        return self._gh.get_repo(f"{owner}/{name}")

    def get_file_content(self, repo: Repository, path: str, ref: str = "main") -> tuple[str, str]:
        """Return (decoded_content, sha) for a file at the given ref."""
        try:
            file_obj = repo.get_contents(path, ref=ref)
        except GithubException as exc:
            if exc.status == 404:
                raise FileNotFoundError(f"{path} not found in {repo.full_name}@{ref}") from exc
            raise
        if isinstance(file_obj, list):
            raise ValueError(f"{path} is a directory, not a file")
        content = base64.b64decode(file_obj.content).decode("utf-8")
        return content, file_obj.sha

    def ensure_labels(self, repo: Repository, labels: list[str]) -> None:
        """Create labels that don't yet exist in the repo (idempotent)."""
        existing = {lbl.name for lbl in repo.get_labels()}
        label_colors = {
            settings.issue_label: "d73a4a",
            settings.issue_label_autofix: "0075ca",
        }
        for label in labels:
            if label not in existing:
                color = label_colors.get(label, "ededed")
                try:
                    repo.create_label(name=label, color=color)
                    logger.info("Created label '%s' in %s", label, repo.full_name)
                except GithubException as exc:
                    if exc.status == 422:  # already exists (race condition)
                        pass
                    else:
                        raise

    def find_open_issue(self, repo: Repository, title: str) -> int | None:
        """Return the number of an existing open issue with this exact title, or None."""
        for issue in repo.get_issues(state="open", labels=[settings.issue_label]):
            if issue.title == title:
                return issue.number
        return None

    def create_issue(
        self,
        repo: Repository,
        title: str,
        body: str,
        labels: list[str],
        assignees: list[str] | None = None,
    ) -> tuple[int, str]:
        """Open a GitHub Issue and return (number, html_url).

        Skips creation if an open issue with the same title already exists
        (idempotent — safe to re-trigger for the same CVE).
        """
        existing = self.find_open_issue(repo, title)
        if existing is not None:
            url = f"https://github.com/{repo.full_name}/issues/{existing}"
            logger.info("Issue already open #%d for '%s', skipping creation", existing, title)
            return existing, url

        self.ensure_labels(repo, labels)

        issue = repo.create_issue(
            title=title,
            body=body,
            labels=labels,
            assignees=assignees or settings.issue_assignees or [],
        )
        logger.info("Created issue #%d: %s", issue.number, issue.html_url)
        return issue.number, issue.html_url
