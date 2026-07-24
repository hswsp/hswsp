#!/usr/bin/env python3
"""Generate the Repository Showcase section of README.md from GitHub repos.

Reads config/repos.json for category definitions, fetches repo metadata from
GitHub API, and replaces the content between markers in README.md.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

README_PATH = Path("README.md")
CONFIG_PATH = Path("config/repos.json")
MARKER_START = "<!-- REPOSITORY-SHOWCASE:START -->"
MARKER_END = "<!-- REPOSITORY-SHOWCASE:END -->"

GITHUB_API = "https://api.github.com"
OWNER = "hswsp"


def get_github_token():
    """Return GH_PAT if set, otherwise GITHUB_TOKEN."""
    return os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")


def api_request(url):
    """Make an authenticated GET request and return parsed JSON."""
    token = get_github_token()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "hswsp-readme-generator",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP error {exc.code} for {url}: {exc.reason}", file=sys.stderr)
        if exc.code == 401:
            print(
                "Authentication failed. If you have private repos, set GH_PAT secret.",
                file=sys.stderr,
            )
        elif exc.code == 403:
            print(
                "Rate limit likely exceeded. Provide a GitHub token to increase limits.",
                file=sys.stderr,
            )
        raise


def fetch_all_repos():
    """Fetch all public (and private, if token permits) repos for OWNER."""
    repos = []
    page = 1
    per_page = 100
    while True:
        # Use /user/repos when authenticated to include private repos,
        # otherwise fall back to /users/{owner}/repos for public repos.
        if get_github_token():
            url = (
                f"{GITHUB_API}/user/repos?affiliation=owner&per_page={per_page}"
                f"&page={page}&sort=created&direction=desc"
            )
        else:
            url = (
                f"{GITHUB_API}/users/{OWNER}/repos?per_page={per_page}"
                f"&page={page}&sort=created&direction=desc"
            )

        data = api_request(url)
        if not data:
            break
        repos.extend(data)
        if len(data) < per_page:
            break
        page += 1

    return repos


def load_config():
    """Load repo category configuration."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_repo_map(repos):
    """Build a name -> repo metadata map."""
    return {repo["name"]: repo for repo in repos}


def format_repo(repo_name, repo_map):
    """Format a single repo entry as a markdown list item."""
    repo = repo_map.get(repo_name)
    if repo is None:
        # Repo is configured but not returned by API (e.g., deleted or no permission).
        url = f"https://github.com/{OWNER}/{repo_name}"
        return f"- [**{repo_name}**]({url})\n"

    name = repo["name"]
    url = repo["html_url"]
    desc = repo.get("description") or ""
    private = repo.get("private", False)

    badges = []
    if private:
        badges.append("🔒 *private*")
    badge_str = " ".join(badges)
    if badge_str:
        badge_str = f" {badge_str}"

    if desc:
        line = f"- [**{name}**]({url}){badge_str} - {desc}"
    else:
        line = f"- [**{name}**]({url}){badge_str}"
    return line + "\n"


def generate_showcase(config, repo_map):
    """Generate markdown for the repository showcase section."""
    lines = []
    configured = set()

    for category in config.get("categories", []):
        title = category.get("title", "")
        repo_names = category.get("repos", [])
        if not title or not repo_names:
            continue

        lines.append(f"### {title}\n")
        for repo_name in repo_names:
            configured.add(repo_name)
            lines.append(format_repo(repo_name, repo_map))
        lines.append("\n")

    # Uncategorized repos: present in GitHub but not in any category.
    exclude = set(config.get("exclude_repos", []))
    uncategorized_title = config.get("uncategorized_title", "📦 其他仓库")
    show_uncategorized = config.get("show_uncategorized", True)

    uncategorized = [
        name
        for name in repo_map
        if name not in configured and name not in exclude
    ]

    if show_uncategorized and uncategorized:
        lines.append(f"### {uncategorized_title}\n")
        for name in sorted(uncategorized, key=str.lower):
            lines.append(format_repo(name, repo_map))
        lines.append("\n")

    return "".join(lines).rstrip() + "\n"


def update_readme(showcase):
    """Replace content between markers in README.md."""
    content = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.DOTALL,
    )
    new_block = f"{MARKER_START}\n\n{showcase}{MARKER_END}"

    if pattern.search(content):
        new_content = pattern.sub(new_block, content)
    else:
        # Markers not found: append the block at the end.
        new_content = content.rstrip() + "\n\n" + new_block + "\n"

    README_PATH.write_text(new_content, encoding="utf-8")


def main():
    print("Loading config...")
    config = load_config()

    print("Fetching repos from GitHub API...")
    repos = fetch_all_repos()
    repo_map = build_repo_map(repos)
    print(f"Fetched {len(repos)} repos.")

    print("Generating showcase...")
    showcase = generate_showcase(config, repo_map)

    print("Updating README.md...")
    update_readme(showcase)

    print("Done.")


if __name__ == "__main__":
    main()
