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
import sys
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


def rest_get_json(url: str, token: str = "") -> Any:
    """
    Fetch JSON from GitHub REST API.
    If a repo-scoped Actions GITHUB_TOKEN receives 401/403/404 on a cross-repo
    or search endpoint, automatically retry without Authorization for public data.
    """
    if token:
        try:
            return json.loads(http_get(url, api_headers(token)))
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403, 404):
                raise
        except subprocess.CalledProcessError:
            pass
    return json.loads(http_get(url, api_headers("")))


def fmt_short_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def fmt_range(start: date | None, end: date | None, fallback: str = "-") -> str:
    if not start or not end:
        return fallback
    if start == end:
        return fmt_short_date(start)
    return f"{fmt_short_date(start)} - {fmt_short_date(end)}"


def compute_calendar_stats(
    daily_counts: dict[str, int],
    yearly_totals: dict[int, int],
    last_year_total: int,
    current_year: int,
) -> dict[str, Any]:
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


def fetch_contributions_calendar_html(username: str, created_year: int, current_year: int) -> dict[str, Any]:
    """
    Fallback HTML parser for GitHub's live profile contribution graph
    when running locally without an API token.
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

    rolling_html = http_get(f"https://github.com/users/{urllib.parse.quote(username)}/contributions")
    rolling_match = re.search(r"([\d,]+)\s+contributions?\s+in\s+the\s+last\s+year", rolling_html)
    last_year_total = int(rolling_match.group(1).replace(",", "")) if rolling_match else yearly_totals.get(current_year, 0)

    return compute_calendar_stats(daily_counts, yearly_totals, last_year_total, current_year)


def fetch_graphql_profile(username: str, token: str) -> dict[str, Any] | None:
    """
    Use GitHub GraphQL (works natively in CI with both GITHUB_TOKEN and METRICS_TOKEN)
    to fetch profile metadata, rolling year total, and multi-year contribution calendar.
    """
    if not token:
        return None
    try:
        hdrs = api_headers(token)
        profile_query = """
        query($login: String!) {
          user(login: $login) {
            name
            login
            createdAt
            followers { totalCount }
            contributionsCollection {
              contributionCalendar {
                totalContributions
              }
            }
          }
        }
        """
        res = http_post_json("https://api.github.com/graphql", {"query": profile_query, "variables": {"login": username}}, hdrs)
        u = (res.get("data") or {}).get("user")
        if not u:
            return None

        created_date = datetime.fromisoformat(u["createdAt"].replace("Z", "+00:00")).date()
        current_year = datetime.now(timezone.utc).year
        last_year_total = int(u["contributionsCollection"]["contributionCalendar"]["totalContributions"])

        yr_query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              restrictedContributionsCount
              totalPullRequestContributions
              contributionCalendar {
                totalContributions
                weeks {
                  contributionDays {
                    date
                    contributionCount
                  }
                }
              }
            }
          }
        }
        """
        daily_counts: dict[str, int] = {}
        yearly_totals: dict[int, int] = {}
        total_commits = 0
        has_restricted = False

        # Query in <= 6-month slices so leap years never trip GraphQL's 1-year window limit
        for yr in range(created_date.year, current_year + 1):
            yr_start = created_date if yr == created_date.year else date(yr, 1, 1)
            slices = []
            mid = date(yr, 6, 30)
            yr_end = date(yr, 12, 31)
            if yr_start <= mid:
                slices.append((yr_start, mid))
                slices.append((date(yr, 7, 1), yr_end))
            else:
                slices.append((yr_start, yr_end))

            yr_total = 0
            for s_from, s_to in slices:
                c_res = http_post_json(
                    "https://api.github.com/graphql",
                    {
                        "query": yr_query,
                        "variables": {
                            "login": username,
                            "from": f"{s_from.isoformat()}T00:00:00Z",
                            "to": f"{s_to.isoformat()}T23:59:59Z",
                        },
                    },
                    hdrs,
                )
                if "errors" in c_res:
                    print(f"GraphQL calendar notice ({yr}): {c_res['errors']}", file=sys.stderr)
                cu = (c_res.get("data") or {}).get("user")
                if not cu:
                    return None
                cc = cu["contributionsCollection"]
                cal = cc["contributionCalendar"]
                yr_total += int(cal["totalContributions"])
                for wk in cal["weeks"]:
                    for day in wk["contributionDays"]:
                        d_str = day["date"]
                        if s_from.isoformat() <= d_str <= s_to.isoformat():
                            daily_counts[d_str] = int(day["contributionCount"])
                total_commits += int(cc["totalCommitContributions"]) + int(cc["restrictedContributionsCount"])
                if int(cc["restrictedContributionsCount"]) > 0:
                    has_restricted = True
            yearly_totals[yr] = yr_total

        calendar = compute_calendar_stats(daily_counts, yearly_totals, last_year_total, current_year)

        # Fetch repos/PRs in a separate query so a single-repo token restriction never fails the calendar above
        extras: dict[str, Any] = {}
        try:
            repo_query = """
            query($login: String!) {
              user(login: $login) {
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
                        node { name }
                      }
                    }
                  }
                }
              }
            }
            """
            r_res = http_post_json("https://api.github.com/graphql", {"query": repo_query, "variables": {"login": username}}, hdrs)
            ru = (r_res.get("data") or {}).get("user")
            if ru and "errors" not in r_res:
                owned_repos = ru["repositories"]["nodes"]
                langs: dict[str, int] = {}
                for r in owned_repos:
                    for edge in r["languages"]["edges"]:
                        lname = edge["node"]["name"]
                        langs[lname] = langs.get(lname, 0) + int(edge["size"])
                extras = {
                    "prs": int(ru["pullRequests"]["totalCount"]),
                    "issues": int(ru["issues"]["totalCount"]),
                    "stars": sum(int(r["stargazerCount"]) for r in owned_repos),
                    "owned_repos": len(owned_repos),
                    "has_private_repos": any(r["isPrivate"] for r in owned_repos),
                    "languages": langs,
                }
        except Exception:
            pass

        return {
            "name": u.get("name") or username,
            "login": u.get("login") or username,
            "created_date": created_date,
            "followers": int(u["followers"]["totalCount"]),
            "commits": total_commits,
            "has_private_scope": has_restricted or bool(extras.get("has_private_repos")),
            "calendar": calendar,
            **extras,
        }
    except Exception:
        return None


def fetch_rest_metrics(username: str, token: str, need_repos: bool = True, need_prs: bool = True) -> dict[str, Any]:
    u_q = urllib.parse.quote(username)
    user = rest_get_json(f"https://api.github.com/users/{u_q}", token)

    owned_repos = 0
    stars = 0
    langs: dict[str, int] = {}
    if need_repos:
        try:
            repos = rest_get_json(f"https://api.github.com/users/{u_q}/repos?per_page=100&type=owner", token)
            non_fork = [r for r in repos if not r.get("fork")]
            owned_repos = len(non_fork)
            stars = sum(int(r.get("stargazers_count", 0)) for r in non_fork)
            for r in non_fork:
                try:
                    r_langs = rest_get_json(r["languages_url"], token)
                    for k, v in r_langs.items():
                        langs[k] = langs.get(k, 0) + int(v)
                except Exception:
                    pass
        except Exception as exc:
            print(f"Info: REST repos scan skipped ({exc})", file=sys.stderr)

    pr_count = 0
    if need_prs:
        try:
            prs = rest_get_json(f"https://api.github.com/search/issues?q=author:{u_q}+type:pr", token)
            pr_count = int(prs.get("total_count", 0))
        except Exception:
            pass

    public_commits = 0
    try:
        cmts = rest_get_json(f"https://api.github.com/search/commits?q=author:{u_q}", token)
        public_commits = int(cmts.get("total_count", 0))
    except Exception:
        pass

    return {
        "name": user.get("name") or username,
        "login": user.get("login") or username,
        "created_at": user["created_at"],
        "followers": int(user.get("followers", 0)),
        "owned_repos": owned_repos,
        "stars": stars,
        "prs": pr_count,
        "public_commits": public_commits,
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
    langs: dict[str, int] = metrics["languages"] or {"Code": 1}
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

    gql = fetch_graphql_profile(username, token)

    need_repos = not (gql and "languages" in gql and gql["owned_repos"] > 0)
    need_prs = not (gql and "prs" in gql)
    rest: dict[str, Any] = {}
    try:
        rest = fetch_rest_metrics(username, token, need_repos=need_repos, need_prs=need_prs)
    except Exception as exc:
        if not gql:
            raise
        print(f"Warning: optional REST fetch skipped ({exc})", file=sys.stderr)

    if gql:
        display_name = gql["name"]
        login = gql["login"]
        created_date = gql["created_date"]
        calendar = gql["calendar"]
        commits = max(int(gql["commits"]), int(rest.get("public_commits", 0)))
        prs = gql.get("prs", rest.get("prs", 0))
        owned_repos = gql.get("owned_repos", rest.get("owned_repos", 0))
        stars = gql.get("stars", rest.get("stars", 0))
        languages = gql.get("languages") or rest.get("languages") or {}
        has_private_scope = bool(gql.get("has_private_scope"))
    else:
        display_name = rest["name"]
        login = rest["login"]
        created_date = datetime.fromisoformat(rest["created_at"].replace("Z", "+00:00")).date()
        current_year = datetime.now(timezone.utc).year
        calendar = fetch_contributions_calendar_html(username, created_date.year, current_year)
        commits = int(rest.get("public_commits", 0))
        prs = int(rest.get("prs", 0))
        owned_repos = int(rest.get("owned_repos", 0))
        stars = int(rest.get("stars", 0))
        languages = rest.get("languages") or {}
        has_private_scope = False

    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    synced_label = now_ist.strftime("%b %d, %Y %H:%M IST")

    metrics = {
        "display_name": display_name,
        "login": login,
        "since_label": fmt_short_date(created_date) + f", {created_date.year}",
        "synced_label": synced_label,
        "commits": commits,
        "has_private_scope": has_private_scope,
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
        f"Generated synced metrics in {out_dir} (gql={bool(gql)}): "
        f"contributions={metrics['total_contributions']}, last_year={metrics['last_year_contributions']}, "
        f"commits={metrics['commits']}, prs={metrics['prs']}, repos={metrics['owned_repos']}, "
        f"streak={metrics['current_streak']}/{metrics['longest_streak']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"::error::generate_stats.py failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
