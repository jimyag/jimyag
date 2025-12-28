#!/usr/bin/env python3
"""
GitHub Profile README Updater

Automatically fetches GitHub activity data and updates README.md with:
- Recent activity from your repositories (commits, releases)
- Open source contributions (merged PRs to other repositories)
All sorted by recent activity time.
"""

import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USERNAME = "jimyag"
README_PATH = os.path.join(os.path.dirname(__file__), "..", "README.md")

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "GitHub-Profile-Updater",
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def github_api_request(url: str) -> Optional[dict | list]:
    """Make a request to the GitHub API."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} for {url}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"URL Error for {url}: {e.reason}")
        return None


def github_graphql_request(query: str) -> Optional[dict]:
    """Make a GraphQL request to GitHub API."""
    url = "https://api.github.com/graphql"
    data = json.dumps({"query": query}).encode("utf-8")
    headers = {**HEADERS, "Content-Type": "application/json"}

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"GraphQL Error {e.code}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"GraphQL URL Error: {e.reason}")
        return None


def get_open_source_contributions(cutoff_iso: str) -> list[dict]:
    """Get merged PRs to repositories the user doesn't own."""
    query = """
    {
      user(login: "%s") {
        contributionsCollection {
          pullRequestContributionsByRepository(maxRepositories: 50) {
            repository {
              nameWithOwner
              url
              isPrivate
              owner {
                login
              }
            }
            contributions(first: 10) {
              nodes {
                pullRequest {
                  title
                  url
                  mergedAt
                  state
                }
              }
            }
          }
        }
      }
    }
    """ % GITHUB_USERNAME

    result = github_graphql_request(query)
    if not result or "data" not in result:
        return []

    contributions = []
    pr_repos = result["data"]["user"]["contributionsCollection"]["pullRequestContributionsByRepository"]

    for repo_contribution in pr_repos:
        repo = repo_contribution["repository"]

        # Skip private repos and user's own repos
        if repo["isPrivate"]:
            continue
        if repo["owner"]["login"].lower() == GITHUB_USERNAME.lower():
            continue
        # Skip repos with hash-like names (likely test/private repos)
        owner_name = repo["owner"]["login"]
        if len(owner_name) > 30 and all(c in "0123456789abcdef" for c in owner_name.lower()):
            continue

        merged_prs = [
            pr["pullRequest"]
            for pr in repo_contribution["contributions"]["nodes"]
            if pr["pullRequest"]["mergedAt"] is not None
            and pr["pullRequest"]["mergedAt"] >= cutoff_iso
        ]

        if merged_prs:
            # Sort by merge date and get the most recent
            merged_prs.sort(key=lambda x: x["mergedAt"], reverse=True)
            latest_pr = merged_prs[0]

            contributions.append({
                "type": "contribution",
                "name": repo["nameWithOwner"],
                "url": repo["url"],
                "pr_count": len(merged_prs),
                "latest_activity": latest_pr["mergedAt"],
            })

    return contributions


def get_own_repos_activity(cutoff_iso: str) -> list[dict]:
    """Get recently updated repositories with commit counts and latest release."""
    repos_url = f"https://api.github.com/users/{GITHUB_USERNAME}/repos?per_page=100&type=owner&sort=pushed&direction=desc"
    repos = github_api_request(repos_url)

    if not repos:
        return []

    recent_repos = []
    for repo in repos:
        if repo.get("fork"):
            continue

        # Skip the profile repo itself
        if repo["name"].lower() == GITHUB_USERNAME.lower():
            continue

        pushed_at = repo.get("pushed_at")
        if not pushed_at or pushed_at < cutoff_iso:
            continue

        # Get commit count in the last 2 months using commits API
        commits_url = f"https://api.github.com/repos/{repo['full_name']}/commits?since={cutoff_iso}&per_page=100"
        commits = github_api_request(commits_url)
        commit_count = len(commits) if commits else 0

        if commit_count == 0:
            continue

        # Get releases and find recent ones
        releases_url = f"https://api.github.com/repos/{repo['full_name']}/releases?per_page=10"
        releases = github_api_request(releases_url)
        recent_releases = []
        latest_tag = None
        latest_tag_url = None

        if releases:
            for release in releases:
                if release.get("published_at") and release["published_at"] >= cutoff_iso:
                    recent_releases.append(release)
            # Get the latest release tag within the time period
            if recent_releases:
                latest_tag = recent_releases[0]["tag_name"]
                latest_tag_url = recent_releases[0]["html_url"]

        # If no releases, check for tags
        if not latest_tag:
            tags_url = f"https://api.github.com/repos/{repo['full_name']}/tags?per_page=5"
            tags = github_api_request(tags_url)
            if tags and len(tags) > 0:
                latest_tag = tags[0]["name"]
                latest_tag_url = f"{repo['html_url']}/releases/tag/{latest_tag}"

        recent_repos.append({
            "type": "repo",
            "name": repo["name"],
            "url": repo["html_url"],
            "commit_count": commit_count,
            "has_release": len(recent_releases) > 0,
            "latest_tag": latest_tag,
            "latest_tag_url": latest_tag_url,
            "latest_activity": pushed_at,
        })

    return recent_repos


def get_all_activity(months: int = 2, limit: int = 10) -> list[dict]:
    """Get all recent activity (own repos + contributions) sorted by time."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=months * 30)
    cutoff_iso = cutoff_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Get both types of activity
    own_repos = get_own_repos_activity(cutoff_iso)
    contributions = get_open_source_contributions(cutoff_iso)

    # Combine and sort by latest activity
    all_activity = own_repos + contributions
    all_activity.sort(key=lambda x: x["latest_activity"], reverse=True)

    return all_activity[:limit]


def generate_activity_markdown(activities: list[dict]) -> str:
    """Generate markdown for recent activity section."""
    if not activities:
        return "_No recent activity_"

    lines = []
    for item in activities:
        if item["type"] == "repo":
            # Own repository activity
            stats = []
            if item["commit_count"] > 0:
                stats.append(f"{item['commit_count']} commits")
            if item["latest_tag"]:
                stats.append(f"[{item['latest_tag']}]({item['latest_tag_url']})")

            line = f"- [{item['name']}]({item['url']})"
            if stats:
                line += f" ({', '.join(stats)})"
        else:
            # Open source contribution
            pr_text = "PR" if item["pr_count"] == 1 else "PRs"
            line = f"- [{item['name']}]({item['url']}) ({item['pr_count']} merged {pr_text})"

        lines.append(line)

    return "\n".join(lines)


def update_readme_section(content: str, section_name: str, new_content: str) -> str:
    """Update a section in the README between markers."""
    start_marker = f"<!-- {section_name}_START -->"
    end_marker = f"<!-- {section_name}_END -->"

    pattern = re.compile(
        rf"({re.escape(start_marker)})(.*?)({re.escape(end_marker)})",
        re.DOTALL
    )

    replacement = f"{start_marker}\n{new_content}\n{end_marker}"

    if pattern.search(content):
        return pattern.sub(replacement, content)
    else:
        print(f"Warning: Section markers for '{section_name}' not found in README")
        return content


def main():
    print("Fetching GitHub data...")

    print("  - Getting all recent activity...")
    activity = get_all_activity(months=2, limit=10)
    print(f"    Found {len(activity)} activities")

    # Generate markdown
    activity_md = generate_activity_markdown(activity)

    # Read current README
    readme_path = os.path.abspath(README_PATH)
    print(f"\nUpdating README at: {readme_path}")

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update sections
    content = update_readme_section(content, "ACTIVITY", activity_md)

    # Add last updated timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = update_readme_section(content, "UPDATED", f"_Last updated: {timestamp}_")

    # Write updated README
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("\nREADME updated successfully!")


if __name__ == "__main__":
    main()
