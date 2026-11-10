#!/usr/bin/env python3
"""
Generate live, auto-synced Crimson-Obsidian SVG cards for GitHub Profile README:
  - github-stats.svg   (Contributions, Commits, PRs, Owned Repos, Stars, Active Days)
  - top-langs.svg      (Aggregated language breakdown across non-fork owned repos)
  - github-streak.svg  (Total Contributions, Current Streak, Longest Streak)

Uses 100% Python standard library so it runs in seconds inside GitHub Actions
with zero third-party rate-limit dependencies.
"""

from __future__ import annotations

import html
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


LANG_COLORS: dict[str, str] = {
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "Dart": "#00b4ab",
    "C++": "#f34b7d",
    "C": "#555555",
    "Python": "#3572a5",
    "Java": "#b07219",
    "Go": "#00add8",
    "Rust": "#dea584",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "Shell": "#89e051",
    "Dockerfile": "#384d54",
    "CMake": "#da3434",
    "Lua": "#000080",
    "Kotlin": "#a97bff",
    "Swift": "#f05138",
}


def http_get(url: str, headers: dict[str, str] | None = None) -> str:
    hdrs = {"User-Agent": "Anivesh91-Profile-Sync/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        # Windows local Python installs sometimes lack Schannel root bindings;
        # fall back to system curl (which still enforces strict TLS verification).
        if isinstance(exc.reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            cmd = ["curl", "-fsSL", "-A", hdrs["User-Agent"]]
            for k, v in hdrs.items():
                if k.lower() != "user-agent":
                    cmd.extend(["-H", f"{k}: {v}"])
            cmd.append(url)
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
            return res.stdout
        raise


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {
        "User-Agent": "Anivesh91-Profile-Sync/1.0",
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            cmd = ["curl", "-fsSL", "-X", "POST", "-A", hdrs["User-Agent"], "-H", "Content-Type: application/json"]
            for k, v in hdrs.items():
                if k.lower() not in ("user-agent", "content-type"):
                    cmd.extend(["-H", f"{k}: {v}"])
            cmd.extend(["-d", json.dumps(payload), url])
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
            return json.loads(res.stdout)
        raise


def api_headers(token: str) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_contributions_calendar(username: str, created_year: int, current_year: int) -> dict[str, Any]:
    """
    Parse exact daily counts directly from GitHub's live profile contribution graph
    for every year from account creation to current_year.
    Includes private contributions automatically if profile setting is enabled.
    """
    daily_counts: dict[str, int] = {}
    yearly_totals: dict[int, int] = {}

    for yr in range(created_year, current_year + 1):
        url = f"https://github.com/users/{urllib.parse.quote(username)}/contributions?from={yr}-01-01&to={yr}-12-31"
        page = http_get(url)

        hdr_match = re.search(rf"([\d,]+)\s+contributions?\s+in\s+{yr}", page)
        hdr_total = int(hdr_match.group(1).replace(",", "")) if hdr_match else None

        tds = re.findall(
            r'<td[^>]+data-date="(\d{4}-\d{2}-\d{2})"[^>]+id="(contribution-day-component-[^"]+)"[^>]+data-level="(\d+)"',
            page,
        )
        tips = dict(
            re.findall(
                r'<tool-tip[^>]+for="(contribution-day-component-[^"]+)"[^>]*>([^<]+)</tool-tip>',
                page,
            )
        )

        yr_sum = 0
        for d_str, cid, lvl in tds:
            tip = tips.get(cid, "")
            m = re.match(r"(\d+)\s+contribution", tip)
            cnt = int(m.group(1)) if m else (1 if int(lvl) > 0 else 0)
            daily_counts[d_str] = cnt
            yr_sum += cnt

        yearly_totals[yr] = hdr_total if hdr_total is not None else yr_sum

    # Rolling "in the last year" headline from GitHub profile
    rolling_html = http_get(f"https://github.com/users/{urllib.parse.quote(username)}/contributions")
    rolling_match = re.search(r"([\d,]+)\s+contributions?\s+in\s+the\s+last\s+year", rolling_html)
    last_year_total = int(rolling_match.group(1).replace(",", "")) if rolling_match else yearly_totals.get(current_year, 0)

    # Calculate exact streaks up through today
    all_dates = sorted(daily_counts.keys())
    today_utc = datetime.now(timezone.utc).date()
    active_dates = [date.fromisoformat(d) for d, c in daily_counts.items() if c > 0]
    latest_active = max(active_dates) if active_dates else today_utc
    effective_today = max(today_utc, latest_active)

    longest = 0
    longest_start: date | None = None
    longest_end: date | None = None
    run = 0
    run_start: date | None = None

    for d_str in all_dates:
        d_obj = date.fromisoformat(d_str)
        if d_obj > effective_today:
            continue
        if daily_counts[d_str] > 0:
            if run == 0:
                run_start = d_obj
            run += 1
            if run >= longest:
                longest = run
                longest_start = run_start
                longest_end = d_obj
        else:
            run = 0

    # Current streak: counts ending today, or still active if yesterday had contributions
    cur_end = effective_today if daily_counts.get(effective_today.isoformat(), 0) > 0 else effective_today - timedelta(days=1)
    current = 0
    cur_start = cur_end
    probe = cur_end
    while daily_counts.get(probe.isoformat(), 0) > 0:
        current += 1
        cur_start = probe
        probe -= timedelta(days=1)

    return {
        "total_contributions": sum(yearly_totals.values()),
        "last_year_contributions": last_year_total,
        "this_year_contributions": yearly_totals.get(current_year, 0),
        "active_days": len(active_dates),
        "current_streak": current,
        "current_range": fmt_range(cur_start, cur_end, "No active streak") if current > 0 else "No active streak",
        "longest_streak": longest,
        "longest_range": fmt_range(longest_start, longest_end) if longest > 0 else "-",
    }


def fmt_short_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_range(start: date | None, end: date | None, fallback: str = "-") -> str:
    if not start or not end:
        return fallback
    if start == end:
        return fmt_short_date(start)
    return f"{fmt_short_date(start)} - {fmt_short_date(end)}"


def fetch_graphql_private_extras(username: str, token: str, created_year: int, current_year: int) -> dict[str, Any] | None:
    """
    If METRICS_TOKEN (or scoped token) grants GraphQL user queries, gather private + public
    commit counts, PR count, stars, and repo languages. Returns None cleanly if unauthenticated
    or scoped down (e.g. single-repo GITHUB_TOKEN without private access).
    """
    if not token:
        return None

    try:
        hdrs = api_headers(token)
        # Check current & prior years via GraphQL
        total_commits = 0
        has_restricted = False
        repo_query = """
        query($login: String!) {
          user(login: $login) {
            followers { totalCount }
            pullRequests(states: [OPEN, CLOSED, MERGED]) { totalCount }
            issues { totalCount }
            repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
              nodes {
                name
                isPrivate
                stargazerCount
                languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
                  edges {
                    size
                    node { name color }
                  }
                }
              }
            }
          }
        }
        """
        res = http_post_json("https://api.github.com/graphql", {"query": repo_query, "variables": {"login": username}}, hdrs)
        if "errors" in res or not res.get("data", {}).get("user"):
            return None

        u = res["data"]["user"]
        owned_repos = u["repositories"]["nodes"]
        stars = sum(r["stargazerCount"] for r in owned_repos)
        langs: dict[str, int] = {}
        for r in owned_repos:
            for edge in r["languages"]["edges"]:
                name = edge["node"]["name"]
                langs[name] = langs.get(name, 0) + int(edge["size"])

        contrib_query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              restrictedContributionsCount
              totalPullRequestContributions
              totalRepositoriesWithContributedCommits
            }
          }
        }
        """
        contributed_repos = 0
        for yr in range(created_year, current_year + 1):
            c_res = http_post_json(
                "https://api.github.com/graphql",
                {
                    "query": contrib_query,
                    "variables": {
                        "login": username,
                        "from": f"{yr}-01-01T00:00:00Z",
                        "to": f"{yr}-12-31T23:59:59Z",
                    },
                },
                hdrs,
            )
            if "errors" in c_res or not c_res.get("data", {}).get("user"):
                continue
            cc = c_res["data"]["user"]["contributionsCollection"]
            total_commits += int(cc["totalCommitContributions"]) + int(cc["restrictedContributionsCount"])
            if int(cc["restrictedContributionsCount"]) > 0:
                has_restricted = True
            if yr == current_year:
                contributed_repos = int(cc["totalRepositoriesWithContributedCommits"])

        return {
            "followers": int(u["followers"]["totalCount"]),
            "prs": int(u["pullRequests"]["totalCount"]),
            "issues": int(u["issues"]["totalCount"]),
            "stars": stars,
            "owned_repos": len(owned_repos),
            "commits": total_commits,
            "has_private_scope": any(r["isPrivate"] for r in owned_repos) or has_restricted,
            "contributed_repos": contributed_repos,
            "languages": langs,
        }
    except Exception:
        return None


def fetch_rest_metrics(username: str, token: str) -> dict[str, Any]:
    hdrs = api_headers(token)
    user = json.loads(http_get(f"https://api.github.com/users/{urllib.parse.quote(username)}", hdrs))
    repos = json.loads(http_get(f"https://api.github.com/users/{urllib.parse.quote(username)}/repos?per_page=100&type=owner", hdrs))
    non_fork = [r for r in repos if not r.get("fork")]

    stars = sum(int(r.get("stargazers_count", 0)) for r in non_fork)
    langs: dict[str, int] = {}
    for r in non_fork:
        r_langs = json.loads(http_get(r["languages_url"], hdrs))
        for k, v in r_langs.items():
            langs[k] = langs.get(k, 0) + int(v)

    prs = json.loads(http_get(f"https://api.github.com/search/issues?q=author:{urllib.parse.quote(username)}+type:pr", hdrs))
    issues = json.loads(http_get(f"https://api.github.com/search/issues?q=author:{urllib.parse.quote(username)}+type:issue", hdrs))

    # Count public commits attributed to username
    cmts_hdr = dict(hdrs)
    cmts_hdr["Accept"] = "application/vnd.github.cloak-preview+json"
    cmts = json.loads(http_get(f"https://api.github.com/search/commits?q=author:{urllib.parse.quote(username)}", cmts_hdr))

    return {
        "name": user.get("name") or username,
        "login": user.get("login") or username,
        "created_at": user["created_at"],
        "followers": int(user.get("followers", 0)),
        "owned_repos": len(non_fork),
        "stars": stars,
        "prs": int(prs.get("total_count", 0)),
        "issues": int(issues.get("total_count", 0)),
        "public_commits": int(cmts.get("total_count", 0)),
        "languages": langs,
    }


def render_stats_svg(metrics: dict[str, Any]) -> str:
    name = html.escape(metrics["display_name"])
    login = html.escape(metrics["login"])
    total_contrib = metrics["total_contributions"]
    last_year_contrib = metrics["last_year_contributions"]
    commits = metrics["commits"]
    commits_label = "Commits (Private+Public)" if metrics.get("has_private_scope") else "Public Commits"
    prs = metrics["prs"]
    owned_repos = metrics["owned_repos"]
    stars = metrics["stars"]
    active_days = metrics["active_days"]
    synced = html.escape(metrics["synced_label"])

    # Activity gauge reflecting rolling 1-year contribution pace
    ring_r = 34
    circumference = 2 * 3.14159265 * ring_r
    pct = min(1.0, max(0.18, last_year_contrib / 365.0))
    dash_filled = round(circumference * pct, 2)
    dash_rest = round(circumference - dash_filled, 2)

    rows = [
        ("Total Contributions", f"{total_contrib:,}", "#ef4444", "M8 1.5l1.8 4.4 4.7.4-3.6 3.1 1.1 4.6L8 11.5 4 14l1.1-4.6L1.5 6.3l4.7-.4z"),
        ("Contributions (Last Year)", f"{last_year_contrib:,}", "#f97316", "M3 2h10a1 1 0 011 1v10a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1zm1.5 4v5.5h2V6h-2zm3.5-2v7.5h2V4H8zm3.5 3.5v4h2v-4h-2z"),
        (commits_label, f"{commits:,}", "#fb7185", "M8 5a3 3 0 100 6 3 3 0 000-6zm0 1.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3zM1.5 7.25h2.2v1.5H1.5v-1.5zm10.8 0h2.2v1.5h-2.2v-1.5z"),
        ("Pull Requests", f"{prs:,}", "#fb923c", "M4.5 3.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm0 6a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm7 0a1.5 1.5 0 100 3 1.5 1.5 0 000-3zM3.8 6.4h1.4v3.2H3.8V6.4zm5-1.4h2.7a.7.7 0 01.7.7v3.9h-1.4V6.4H8.8V5z"),
        ("Owned Repos / Stars", f"{owned_repos}  ({stars} ★)", "#f87171", "M3 2.5A1.5 1.5 0 014.5 1h7A1.5 1.5 0 0113 2.5v11a.5.5 0 01-.78.41L8 11.06l-4.22 2.85A.5.5 0 013 13.5v-11z"),
    ]

    row_svg = []
    for i, (label, val, color, d_path) in enumerate(rows):
        y = 58 + i * 24
        row_svg.append(f'''    <g transform="translate(22, {y})">
      <g transform="translate(0, -11.5) scale(0.92)" fill="{color}">
        <path d="{d_path}" />
      </g>
      <text x="23" y="0" fill="#cbd5e1" font-size="12.5" font-weight="500">{html.escape(label)}</text>
      <text x="292" y="0" fill="#f8fafc" font-size="13" font-weight="700" text-anchor="end">{html.escape(val)}</text>
    </g>''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="460" height="188" viewBox="0 0 460 188" role="img" aria-label="{name} GitHub Stats">
  <title>{name} (@{login}) GitHub Stats</title>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Ubuntu, Helvetica, Arial, sans-serif; }}
    @keyframes ringPulse {{
      0%, 100% {{ opacity: 0.9; }}
      50% {{ opacity: 1; }}
    }}
    .ring-arc {{ animation: ringPulse 3.2s ease-in-out infinite; }}
  </style>
  <defs>
    <linearGradient id="accent-line" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ef4444" />
      <stop offset="55%" stop-color="#f97316" />
      <stop offset="100%" stop-color="#ef4444" stop-opacity="0.15" />
    </linearGradient>
    <linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ef4444" />
      <stop offset="100%" stop-color="#fb923c" />
    </linearGradient>
    <radialGradient id="card-glow" cx="84%" cy="52%" r="42%">
      <stop offset="0%" stop-color="#dc2626" stop-opacity="0.16" />
      <stop offset="100%" stop-color="#0d1117" stop-opacity="0" />
    </radialGradient>
  </defs>

  <rect width="460" height="188" rx="14" fill="#0d1117" stroke="#26191e" stroke-width="1.2" />
  <rect width="460" height="188" rx="14" fill="url(#card-glow)" />
  <rect x="22" y="1" width="416" height="2" rx="1" fill="url(#accent-line)" />

  <text x="22" y="30" fill="#f8fafc" font-size="15.5" font-weight="700">GitHub Overview</text>
  <text x="438" y="29" fill="#f87171" font-size="11.5" font-weight="600" text-anchor="end">@{login}</text>

{chr(10).join(row_svg)}

  <!-- Right Activity Gauge -->
  <g transform="translate(388, 96)">
    <circle cx="0" cy="0" r="{ring_r}" fill="none" stroke="#1e2430" stroke-width="6.5" />
    <circle class="ring-arc" cx="0" cy="0" r="{ring_r}" fill="none" stroke="url(#ring-grad)"
            stroke-width="6.5" stroke-linecap="round"
            transform="rotate(-90)" stroke-dasharray="{dash_filled} {dash_rest}" />
    <text x="0" y="3" fill="#ffffff" font-size="19" font-weight="800" text-anchor="middle">{active_days}</text>
    <text x="0" y="18" fill="#fb923c" font-size="9" font-weight="700" text-anchor="middle">ACTIVE DAYS</text>
  </g>

  <text x="438" y="176" fill="#64748b" font-size="10" text-anchor="end">Auto-synced • {synced}</text>
</svg>
'''


def render_langs_svg(metrics: dict[str, Any]) -> str:
    langs: dict[str, int] = metrics["languages"]
    sorted_langs = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:6]
    total_bytes = sum(v for _, v in sorted_langs) or 1
    synced = html.escape(metrics["synced_label"])

    bar_w = 316
    bar_x = 22
    bar_y = 48
    segments = []
    cur_x = float(bar_x)
    for i, (lang, sz) in enumerate(sorted_langs):
        seg_w = max(2.0, (sz / total_bytes) * bar_w)
        if i == len(sorted_langs) - 1:
            seg_w = max(1.0, (bar_x + bar_w) - cur_x)
        color = LANG_COLORS.get(lang, "#94a3b8")
        segments.append(f'    <rect x="{cur_x:.2f}" y="{bar_y}" width="{seg_w:.2f}" height="9" fill="{color}" />')
        cur_x += seg_w

    legend_items = []
    for idx, (lang, sz) in enumerate(sorted_langs):
        pct = (sz / total_bytes) * 100.0
        col = idx % 2
        row = idx // 2
        lx = 22 + col * 165
        ly = 82 + row * 28
        color = LANG_COLORS.get(lang, "#94a3b8")
        legend_items.append(f'''  <g transform="translate({lx}, {ly})">
    <circle cx="5" cy="-4" r="4.5" fill="{color}" stroke="rgba(255,255,255,0.24)" stroke-width="0.6" />
    <text x="16" y="0" fill="#e2e8f0" font-size="12.5" font-weight="500">{html.escape(lang)}</text>
    <text x="150" y="0" fill="#94a3b8" font-size="11.5" font-weight="600" text-anchor="end">{pct:.1f}%</text>
  </g>''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="360" height="188" viewBox="0 0 360 188" role="img" aria-label="Most Used Languages">
  <title>Most Used Languages</title>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Ubuntu, Helvetica, Arial, sans-serif; }}
  </style>
  <defs>
    <clipPath id="lang-bar-clip">
      <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="9" rx="4.5" />
    </clipPath>
    <linearGradient id="accent-line-l" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#f97316" />
      <stop offset="60%" stop-color="#ef4444" />
      <stop offset="100%" stop-color="#ef4444" stop-opacity="0.15" />
    </linearGradient>
    <radialGradient id="lang-glow" cx="18%" cy="20%" r="55%">
      <stop offset="0%" stop-color="#ea580c" stop-opacity="0.14" />
      <stop offset="100%" stop-color="#0d1117" stop-opacity="0" />
    </radialGradient>
  </defs>

  <rect width="360" height="188" rx="14" fill="#0d1117" stroke="#26191e" stroke-width="1.2" />
  <rect width="360" height="188" rx="14" fill="url(#lang-glow)" />
  <rect x="22" y="1" width="316" height="2" rx="1" fill="url(#accent-line-l)" />

  <text x="22" y="30" fill="#f8fafc" font-size="15.5" font-weight="700">Top Languages</text>
  <text x="338" y="29" fill="#94a3b8" font-size="11" text-anchor="end">Owned repos</text>

  <g clip-path="url(#lang-bar-clip)">
{chr(10).join(segments)}
  </g>

{chr(10).join(legend_items)}

  <text x="338" y="174" fill="#64748b" font-size="10" text-anchor="end">Live Repo Scan • {synced}</text>
</svg>
'''


def render_streak_svg(metrics: dict[str, Any]) -> str:
    total = metrics["total_contributions"]
    cur = metrics["current_streak"]
    cur_range = html.escape(metrics["current_range"])
    longest = metrics["longest_streak"]
    longest_range = html.escape(metrics["longest_range"])
    since = html.escape(metrics["since_label"])
    synced = html.escape(metrics["synced_label"])

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="500" height="195" viewBox="0 0 500 195" role="img" aria-label="GitHub Contribution Streak">
  <title>GitHub Contribution Streak ({total} Contributions)</title>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Ubuntu, Helvetica, Arial, sans-serif; }}
    @keyframes flamePulse {{
      0%, 100% {{ transform: scale(1); opacity: 0.9; }}
      50% {{ transform: scale(1.08); opacity: 1; }}
    }}
    .flame-icon {{
      transform-box: fill-box;
      transform-origin: center;
      animation: flamePulse 2.8s ease-in-out infinite;
    }}
  </style>
  <defs>
    <linearGradient id="streak-top" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ef4444" stop-opacity="0.15" />
      <stop offset="50%" stop-color="#f97316" />
      <stop offset="100%" stop-color="#ef4444" stop-opacity="0.15" />
    </linearGradient>
    <linearGradient id="ring-streak" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ef4444" />
      <stop offset="100%" stop-color="#f97316" />
    </linearGradient>
    <radialGradient id="center-aura" cx="50%" cy="46%" r="36%">
      <stop offset="0%" stop-color="#dc2626" stop-opacity="0.22" />
      <stop offset="100%" stop-color="#0d1117" stop-opacity="0" />
    </radialGradient>
  </defs>

  <rect width="500" height="195" rx="14" fill="#0d1117" stroke="#26191e" stroke-width="1.2" />
  <rect width="500" height="195" rx="14" fill="url(#center-aura)" />
  <rect x="30" y="1" width="440" height="2" rx="1" fill="url(#streak-top)" />

  <!-- Dividers -->
  <line x1="167" y1="30" x2="167" y2="162" stroke="#1e2430" stroke-width="1.2" />
  <line x1="333" y1="30" x2="333" y2="162" stroke="#1e2430" stroke-width="1.2" />

  <!-- Left: Total Contributions -->
  <g transform="translate(83, 80)">
    <text x="0" y="0" fill="#ffffff" font-size="30" font-weight="800" text-anchor="middle">{total:,}</text>
    <text x="0" y="28" fill="#f87171" font-size="13" font-weight="600" text-anchor="middle">Total Contributions</text>
    <text x="0" y="48" fill="#94a3b8" font-size="11" text-anchor="middle">{since} - Present</text>
  </g>

  <!-- Center: Current Streak -->
  <g transform="translate(250, 74)">
    <circle cx="0" cy="0" r="39" fill="none" stroke="url(#ring-streak)" stroke-width="4.5" />
    <circle cx="0" cy="-39" r="11" fill="#0d1117" />
    <g transform="translate(-8, -48)">
      <path class="flame-icon" fill="#f97316"
            d="M8.5 0C9 4 13 6.2 13 11a5 5 0 11-10 0c0-2.5 1.1-4.8 2.8-6.4-.2 1.6.5 2.7 1.4 3.1C6.8 5 7.5 2.4 8.5 0z" />
    </g>
    <text x="0" y="10" fill="#ffffff" font-size="31" font-weight="800" text-anchor="middle">{cur}</text>
    <text x="0" y="61" fill="#fb923c" font-size="13.5" font-weight="700" text-anchor="middle">Current Streak</text>
    <text x="0" y="80" fill="#94a3b8" font-size="11" text-anchor="middle">{cur_range}</text>
  </g>

  <!-- Right: Longest Streak -->
  <g transform="translate(417, 80)">
    <text x="0" y="0" fill="#ffffff" font-size="30" font-weight="800" text-anchor="middle">{longest}</text>
    <text x="0" y="28" fill="#f87171" font-size="13" font-weight="600" text-anchor="middle">Longest Streak</text>
    <text x="0" y="48" fill="#94a3b8" font-size="11" text-anchor="middle">{longest_range}</text>
  </g>

  <text x="250" y="182" fill="#64748b" font-size="10" text-anchor="middle">Exact Profile Calendar Sync • {synced}</text>
</svg>
'''


def main() -> None:
    username = os.environ.get("GITHUB_USER", "Anivesh91")
    token = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    out_dir = Path(os.environ.get("OUT_DIR", "dist"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rest = fetch_rest_metrics(username, token)
    created_date = datetime.fromisoformat(rest["created_at"].replace("Z", "+00:00")).date()
    current_year = datetime.now(timezone.utc).year

    calendar = fetch_contributions_calendar(username, created_date.year, current_year)
    gql = fetch_graphql_private_extras(username, token, created_date.year, current_year)

    # Prefer GraphQL metrics when METRICS_TOKEN/token grants full visibility
    commits = gql["commits"] if gql and gql["commits"] > rest["public_commits"] else rest["public_commits"]
    prs = gql["prs"] if gql and gql["prs"] >= rest["prs"] else rest["prs"]
    owned_repos = gql["owned_repos"] if gql else rest["owned_repos"]
    stars = gql["stars"] if gql else rest["stars"]
    languages = gql["languages"] if gql and sum(gql["languages"].values()) > sum(rest["languages"].values()) else rest["languages"]

    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    synced_label = now_ist.strftime("%b %d, %Y %H:%M IST")

    metrics = {
        "display_name": rest["name"],
        "login": rest["login"],
        "since_label": fmt_short_date(created_date) + f", {created_date.year}",
        "synced_label": synced_label,
        "commits": commits,
        "has_private_scope": bool(gql and gql.get("has_private_scope")),
        "prs": prs,
        "owned_repos": owned_repos,
        "stars": stars,
        "languages": languages,
        **calendar,
    }

    (out_dir / "github-stats.svg").write_text(render_stats_svg(metrics), encoding="utf-8")
    (out_dir / "top-langs.svg").write_text(render_langs_svg(metrics), encoding="utf-8")
    (out_dir / "github-streak.svg").write_text(render_streak_svg(metrics), encoding="utf-8")
    (out_dir / "sync-meta.json").write_text(json.dumps({k: v for k, v in metrics.items() if k != "languages"}, indent=2), encoding="utf-8")

    print(
        f"Generated synced metrics in {out_dir}: "
        f"contributions={metrics['total_contributions']}, last_year={metrics['last_year_contributions']}, "
        f"commits={metrics['commits']}, streak={metrics['current_streak']}/{metrics['longest_streak']}"
    )


if __name__ == "__main__":
    main()
