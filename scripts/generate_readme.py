#!/usr/bin/env python3
"""Generate the Repository Showcase sections of README.md from GitHub repos.

Reads config/repos.json for category definitions, fetches repo metadata from
GitHub API, and replaces the content between the markers in README.md.

README.md holds two generated sections:

    ## 🔀 Forked Repositories      <!-- FORKED-SHOWCASE:START/END -->
    ## 📁 Repository Showcase      <!-- REPOSITORY-SHOWCASE:START/END -->

Which section a repo lands in is decided by GitHub's ``fork`` flag, so forks
never mix with the repos I authored. Within each category — forked or not —
entries are ordered by ``pushed_at`` (last push), newest first. Anything
unconfigured falls back to that section's uncategorized bucket, sorted the same
way.

Repos missing from the API response (e.g. private repos when no token is
available) stay in the section where ``config/repos.json`` puts them, and
``private_repos`` keeps their 🔒 badge visible during token-less local runs.
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
FORKED_MARKER_START = "<!-- FORKED-SHOWCASE:START -->"
FORKED_MARKER_END = "<!-- FORKED-SHOWCASE:END -->"
FORKED_HEADING = "## 🔀 Forked Repositories"

GITHUB_API = "https://api.github.com"
OWNER = "hswsp"

OWNED = "owned"
FORKED = "forked"
SECTION_KEYS = {
    OWNED: ("categories", "uncategorized_title", "📦 其他仓库"),
    FORKED: ("forked_categories", "forked_uncategorized_title", "📦 其他 Fork"),
}


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


def fetch_page(endpoint, page, per_page):
    """Build URL and fetch a single page of repos."""
    if endpoint == "user":
        url = (
            f"{GITHUB_API}/user/repos?affiliation=owner&per_page={per_page}"
            f"&page={page}&sort=created&direction=desc"
        )
    else:
        url = (
            f"{GITHUB_API}/users/{OWNER}/repos?per_page={per_page}"
            f"&page={page}&sort=created&direction=desc"
        )
    return api_request(url)


def fetch_all_repos():
    """Fetch all public (and private, if token permits) repos for OWNER."""
    repos = []
    page = 1
    per_page = 100

    # Prefer /user/repos to include private repos. If the token lacks permission,
    # fall back to /users/{owner}/repos (public repos only).
    endpoint = "user" if get_github_token() else "public"

    while True:
        try:
            data = fetch_page(endpoint, page, per_page)
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and endpoint == "user":
                print(
                    "GH_PAT cannot access /user/repos (missing 'repo' scope or "
                    "insufficient token permissions). Falling back to public repos only.",
                    file=sys.stderr,
                )
                endpoint = "public"
                # Retry the same page with the public endpoint.
                data = fetch_page(endpoint, page, per_page)
            else:
                raise

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


def configured_sections(config):
    """Map repo name -> set of sections the config lists it under."""
    mapping = {}
    for section, (key, _, _) in SECTION_KEYS.items():
        for category in config.get(key, []):
            for name in category.get("repos", []):
                mapping.setdefault(name, set()).add(section)
    return mapping


def section_of(name, repo_map, sections):
    """Return the section a repo belongs to.

    The GitHub ``fork`` flag wins for repos we can see; repos absent from the
    API response keep the section the config lists them under.
    """
    repo = repo_map.get(name)
    if repo is not None:
        return FORKED if repo.get("fork") else OWNED

    planned = sections.get(name, set())
    if OWNED in planned:
        return OWNED
    if FORKED in planned:
        return FORKED
    return OWNED


def format_repo(repo_name, repo_map, config):
    """Format a single repo entry as a markdown list item."""
    private_hint = repo_name in set(config.get("private_repos", []))
    repo = repo_map.get(repo_name)
    if repo is None:
        # Repo is configured but not returned by API (e.g., deleted or no permission).
        url = f"https://github.com/{OWNER}/{repo_name}"
        badge_str = " 🔒 *private*" if private_hint else ""
        return f"- [**{repo_name}**]({url}){badge_str}\n"

    name = repo["name"]
    url = repo["html_url"]
    desc = repo.get("description") or ""
    private = repo.get("private", False) or private_hint

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


def sort_by_pushed_at(repo_names, repo_map):
    """Sort repo names by last push time (pushed_at), most recent first.

    This is the ordering contract for every category, forked or not: newest
    push first, oldest last. Repos Github did not report (private repos when
    running without a token, or deleted repos) have no known push time, so they
    are placed at the end and reported on stderr — their position is only
    provisional until a token-enabled run re-sorts them.
    """
    indexed = []
    tail = []
    for idx, name in enumerate(repo_names):
        repo = repo_map.get(name)
        if repo and repo.get("pushed_at"):
            indexed.append((repo["pushed_at"], idx, name))
        else:
            tail.append((idx, name))

    for _, name in tail:
        print(
            f"warning: no pushed_at for {name}; keeping it last in its category "
            "until a token-enabled run can sort it",
            file=sys.stderr,
        )

    indexed.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [name for _, _, name in indexed] + [name for _, name in tail]


def generate_showcase(config, repo_map, section):
    """Generate markdown for one showcase section (owned or forked)."""
    category_key, title_key, default_title = SECTION_KEYS[section]
    sections = configured_sections(config)
    lines = []
    configured = set()

    for category in config.get(category_key, []):
        title = category.get("title", "")
        repo_names = category.get("repos", [])
        if not title or not repo_names:
            continue

        # A repo whose real fork status disagrees with this section is skipped
        # here and picked up by the other section's uncategorized bucket.
        names = [
            name for name in repo_names if section_of(name, repo_map, sections) == section
        ]
        if not names:
            continue

        configured.update(names)
        lines.append(f"### {title}\n")
        for repo_name in sort_by_pushed_at(names, repo_map):
            lines.append(format_repo(repo_name, repo_map, config))
        lines.append("\n")

    # Uncategorized repos: present in GitHub but not in any category of this section.
    exclude = set(config.get("exclude_repos", []))
    uncategorized_title = config.get(title_key, default_title)
    show_uncategorized = config.get("show_uncategorized", True)

    uncategorized = [
        name
        for name in repo_map
        if section_of(name, repo_map, sections) == section
        and name not in configured
        and name not in exclude
    ]
    # Repos declared in config but not returned by the API (private repos when
    # running without a token) still need a place in the list.
    uncategorized += [
        name
        for name in config.get("private_repos", [])
        if name not in repo_map
        and name not in configured
        and name not in exclude
        and section_of(name, repo_map, sections) == section
    ]

    if show_uncategorized and uncategorized:
        ordered = sort_by_pushed_at(uncategorized, repo_map)
        lines.append(f"### {uncategorized_title}\n")
        for name in ordered:
            lines.append(format_repo(name, repo_map, config))
        lines.append("\n")

    return "".join(lines).rstrip() + "\n"


def replace_block(content, start_marker, end_marker, showcase):
    """Replace the marker block, or return None when it is absent."""
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    if not pattern.search(content):
        return None
    new_block = f"{start_marker}\n\n{showcase}{end_marker}"
    return pattern.sub(new_block, content)


def update_readme(showcases):
    """Replace (or bootstrap) the two generated blocks in README.md."""
    content = README_PATH.read_text(encoding="utf-8")

    forked_block = f"{FORKED_MARKER_START}\n\n{showcases[FORKED]}{FORKED_MARKER_END}"
    replaced = replace_block(
        content, FORKED_MARKER_START, FORKED_MARKER_END, showcases[FORKED]
    )
    if replaced is not None:
        content = replaced
    else:
        # Bootstrap: put the forked section above the main showcase block.
        anchor = content.find(MARKER_START)
        insertion = f"{FORKED_HEADING}\n\n{forked_block}\n\n"
        if anchor == -1:
            content = content.rstrip() + "\n\n" + insertion
        else:
            content = content[:anchor] + insertion + content[anchor:]

    replaced = replace_block(
        content, MARKER_START, MARKER_END, showcases[OWNED]
    )
    if replaced is not None:
        content = replaced
    else:
        new_block = f"{MARKER_START}\n\n{showcases[OWNED]}{MARKER_END}"
        content = content.rstrip() + "\n\n" + new_block + "\n"

    README_PATH.write_text(content, encoding="utf-8")


def main():
    print("Loading config...")
    config = load_config()

    print("Fetching repos from GitHub API...")
    repos = fetch_all_repos()
    repo_map = build_repo_map(repos)
    forked_count = sum(1 for repo in repos if repo.get("fork"))
    print(f"Fetched {len(repos)} repos ({forked_count} forks).")

    print("Generating showcases...")
    showcases = {
        OWNED: generate_showcase(config, repo_map, OWNED),
        FORKED: generate_showcase(config, repo_map, FORKED),
    }

    print("Updating README.md...")
    update_readme(showcases)

    print("Done.")


if __name__ == "__main__":
    main()
