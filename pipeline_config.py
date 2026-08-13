"""Shared config/token loading for collect.py, collect_extra.py, build_all.py."""
import json
import os
import subprocess

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            "config.json not found. Copy config.example.json to config.json "
            "(the onboarding conversation in CLAUDE.md does this for you) before collecting."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_repos():
    """Returns a list of (owner, name, company_or_product, segment) tuples."""
    cfg = load_config()
    return [(r["owner"], r["name"], r["company_or_product"], r["segment"]) for r in cfg["repos"]]


def load_activity_types():
    return set(load_config().get("activity_types", ["starred", "forked", "contributor", "issue_or_pr_author"]))


def get_token():
    """Prefer an explicit classic PAT (GITHUB_TOKEN env var) -- required for
    stargazers of repos you don't maintain, see README's api_restriction_note.
    Falls back to `gh auth token`, which only works for repos you have write
    access to."""
    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token
    return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
