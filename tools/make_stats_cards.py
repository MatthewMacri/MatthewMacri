#!/usr/bin/env python3
"""Draw the two stat cards on the profile README.

These used to come from the shared github-readme-stats instance on Vercel.
That deployment is paused, so the cards rendered as broken images for
everyone using it, with nothing wrong on this end and nothing to be done
about it from here. So the cards are generated here instead and committed
as plain SVG: no third-party service sits between the README and the
reader, and nothing can rate-limit or pause it.

  GITHUB_TOKEN=... python3 tools/make_stats_cards.py

Writes assets/github-stats.svg, assets/top-langs.svg, and assets/stats.json,
which holds the last figure successfully read for each stat. It also stamps
each card's content hash onto its <img> in the README: GitHub serves README
images through a cache keyed on the URL, so a card whose address never
changes goes on being served from the old bytes however often the file
behind it is rewritten.

On the token: the Search API is what supplies the commit, pull request and
issue counts, and Actions' built-in GITHUB_TOKEN cannot use it across
repositories - every such query comes back 422, and the same token returns
a user object with no follower count. A run with only that token therefore
knows the stars and the languages and nothing else. Rather than publish
zeros it reuses the last figure it did read, from assets/stats.json. For
live numbers, put a classic PAT with public_repo and read:user in the
repository secret STATS_TOKEN; the workflow prefers it when it exists.
"""

import collections
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

USER = os.environ.get("PROFILE_USER", "MatthewMacri")
TOKEN = (os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
         or os.environ.get("GH_TOKEN"))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

# ---------------------------------------------------------------- palette

# tokyonight, the theme the README asked github-readme-stats for, so the
# cards still sit with the rest of the page.
BG, TITLE, TEXT, ICON, MUTED = "#1a1b27", "#70a5fd", "#38bdad", "#bf91f3", "#8a92b2"

# Linguist's colours, so a language looks the way it does everywhere else
# on GitHub.
LANG_COLOR = {
    "PHP": "#4F5D95", "Dart": "#00B4AB", "Java": "#b07219", "CSS": "#563d7c",
    "Python": "#3572A5", "C#": "#178600", "JavaScript": "#f1e05a",
    "HTML": "#e34c26", "Monkey C": "#8D6747", "Astro": "#ff5a03",
    "Shell": "#89e051", "TypeScript": "#3178c6", "C++": "#f34b7d",
    "C": "#555555", "Ruby": "#701516", "Go": "#00ADD8", "Rust": "#dea584",
    "Kotlin": "#A97BFF", "Swift": "#F05138", "Vue": "#41b883",
    "Svelte": "#ff3e00", "SQL": "#e38c00", "Jupyter": "#DA5B0B",
    "Objective-C": "#438eff", "Lua": "#000080", "R": "#198CE7",
    "Perl": "#0298c3",
}

# ------------------------------------------------------- language counting

EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".cs": "C#",
    ".dart": "Dart", ".php": "PHP", ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "CSS", ".c": "C", ".h": "C", ".cpp": "C++",
    ".cc": "C++", ".hpp": "C++", ".mc": "Monkey C", ".sh": "Shell",
    ".bash": "Shell", ".sql": "SQL", ".rb": "Ruby", ".go": "Go",
    ".rs": "Rust", ".kt": "Kotlin", ".swift": "Swift", ".astro": "Astro",
    ".vue": "Vue", ".svelte": "Svelte", ".m": "Objective-C",
    ".ipynb": "Jupyter", ".r": "R", ".pl": "Perl", ".lua": "Lua",
}

# GitHub's own language figures count everything in the tree, which on a
# Flutter project means the generated linux/ windows/ macos/ runners. Those
# put C++, C and CMake at the top of this profile's languages, above any
# language actually written in it. Generated and vendored trees are skipped
# so the card describes the person rather than their scaffolding.
SKIP_DIR = re.compile(
    r"(^|/)("
    r"node_modules|vendor|bower_components|third_party|external|"
    r"build|dist|out|bin|obj|\.dart_tool|\.next|\.nuxt|__pycache__|"
    r"migrations|linux|windows|macos|ios|android|Pods|gradle|\.gradle"
    r")(/|$)"
)
SKIP_FILE = re.compile(
    r"(generated_plugin_registrant|\.g\.dart$|\.freezed\.dart$|"
    r"\.min\.(js|css)$|package-lock\.json$)"
)


def api(path):
    url = path if path.startswith("http") else "https://api.github.com/" + path
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-stats-card",
        **({"Authorization": "Bearer " + TOKEN} if TOKEN else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), r.headers.get("Link", "")
    except urllib.error.HTTPError as e:
        print("  ! %s -> HTTP %s" % (url, e.code), file=sys.stderr)
        return None, ""


def paged(path):
    """Walk a paginated collection, following the Link header."""
    url, out = path, []
    while url:
        data, link = api(url)
        if not data:
            break
        out.extend(data)
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out


def public_repos():
    return [r for r in paged("users/%s/repos?per_page=100" % USER)
            if not r.get("fork") and not r.get("archived")]


def language_bytes(repos):
    total = collections.Counter()
    for repo in repos:
        tree, _ = api("repos/%s/%s/git/trees/HEAD?recursive=1"
                      % (USER, repo["name"]))
        if not tree or "tree" not in tree:
            continue
        for node in tree["tree"]:
            if node.get("type") != "blob":
                continue
            path = node["path"]
            if SKIP_DIR.search(path) or SKIP_FILE.search(path):
                continue
            tail = path.rsplit("/", 1)[-1]
            ext = tail[tail.rfind("."):].lower() if "." in tail else ""
            lang = EXT.get(ext)
            if lang:
                total[lang] += node.get("size", 0)
    return total


def count(query):
    """Total matches, or None if the query could not be run at all.

    The distinction matters: a failed query is not an achievement of zero,
    and writing it onto the card as one would be a quiet lie.
    """
    data, _ = api("search/issues?q=%s&per_page=1" % query)
    if not data or "total_count" not in data:
        return None
    return data["total_count"]


def load_known():
    try:
        with open(os.path.join(OUT, "stats.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def keep_best(fresh, known, key):
    """Prefer a figure read just now; otherwise the last one that was."""
    if fresh is not None:
        return fresh, True
    return known.get(key), False


# ------------------------------------------------------------------ svg

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def group(n):
    return "{:,}".format(n)


FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Helvetica, Arial, sans-serif"

ICONS = {
    "star": "M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 "
            ".416 1.279l-3.046 2.97.719 4.192a.75.75 0 0 1-1.088.791L8 "
            "12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 "
            "6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 "
            ".25Z",
    "commit": "M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 "
              "0-1.5h3.32a4.002 4.002 0 0 1 7.86 0h3.32a.75.75 0 0 1 0 "
              "1.5ZM10.5 7.75a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z",
    "pr": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 "
          "0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 "
          "1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 "
          "0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 "
          "3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 "
          "0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75"
          ".75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z",
    "issue": "M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3ZM8 0a8 8 0 1 1 0 "
             "16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z",
    "repo": "M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 "
            "0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 "
            "0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 "
            "11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 "
            "9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a"
            ".25.25 0 0 1-.4.2l-1.45-1.087a.25.25 0 0 0-.3 0L5.4 15.7a.25.25 "
            "0 0 1-.4-.2Z",
}


def card_open(w, h, title, desc):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-labelledby="t d">\n'
        '<title id="t">%s</title><desc id="d">%s</desc>\n'
        '<rect x="0.5" y="0.5" width="%s" height="%s" rx="6" fill="%s" '
        'stroke="#2c3050"/>\n' % (w, h, w, h, esc(title), esc(desc),
                                  w - 1, h - 1, BG)
    )


def stats_card(stats, name):
    w, h = 460, 195
    s = card_open(w, h, "%s's GitHub stats" % name,
                  ", ".join("%s %s" % (group(v), k)
                            for k, v, _ in stats))
    s += ('<text x="25" y="33" fill="%s" font-family="%s" font-size="16" '
          'font-weight="600">%s’s GitHub Stats</text>\n'
          % (TITLE, FONT, esc(name)))
    s += '<line x1="25" y1="45" x2="%d" y2="45" stroke="#2c3050"/>\n' % (w - 25)

    y = 72
    for label, value, icon in stats:
        s += ('<g transform="translate(25,%d) scale(0.9)" fill="%s">'
              '<path d="%s"/></g>\n' % (y - 12, ICON, ICONS[icon]))
        s += ('<text x="50" y="%d" fill="%s" font-family="%s" '
              'font-size="14">%s</text>\n' % (y, TEXT, FONT, esc(label)))
        s += ('<text x="%d" y="%d" fill="%s" font-family="%s" font-size="14" '
              'font-weight="700" text-anchor="end">%s</text>\n'
              % (w - 25, y, TEXT, FONT, group(value)))
        y += 25
    return s + "</svg>\n"


def langs_card(totals, top=6):
    w, h = 360, 195
    items = totals.most_common(top)
    grand = sum(totals.values()) or 1
    s = card_open(w, h, "Most used languages",
                  ", ".join("%s %.1f%%" % (k, 100 * v / grand)
                            for k, v in items))
    s += ('<text x="25" y="33" fill="%s" font-family="%s" font-size="16" '
          'font-weight="600">Most Used Languages</text>\n' % (TITLE, FONT))
    s += '<line x1="25" y1="45" x2="%d" y2="45" stroke="#2c3050"/>\n' % (w - 25)

    # One bar, split by share. Rounded ends via a clip so the segments do
    # not each get their own corners.
    bar_x, bar_y, bar_w, bar_h = 25, 60, w - 50, 9
    s += ('<clipPath id="bar"><rect x="%d" y="%d" width="%d" height="%d" '
          'rx="%d"/></clipPath>\n<g clip-path="url(#bar)">\n'
          % (bar_x, bar_y, bar_w, bar_h, bar_h // 2))
    x = float(bar_x)
    for name, value in items:
        seg = bar_w * value / grand
        s += ('<rect x="%.2f" y="%d" width="%.2f" height="%d" fill="%s"/>\n'
              % (x, bar_y, seg + 0.5, bar_h, LANG_COLOR.get(name, MUTED)))
        x += seg
    if x < bar_x + bar_w:   # whatever the rest of the languages add up to
        s += ('<rect x="%.2f" y="%d" width="%.2f" height="%d" fill="#2c3050"/>\n'
              % (x, bar_y, bar_x + bar_w - x, bar_h))
    s += "</g>\n"

    # Legend, two columns.
    for i, (name, value) in enumerate(items):
        col, row = i % 2, i // 2
        lx, ly = 25 + col * 165, 95 + row * 24
        s += ('<circle cx="%d" cy="%d" r="5" fill="%s"/>\n'
              % (lx + 5, ly - 4, LANG_COLOR.get(name, MUTED)))
        s += ('<text x="%d" y="%d" fill="%s" font-family="%s" '
              'font-size="12">%s</text>\n'
              % (lx + 17, ly, TEXT, FONT, esc(name)))
        s += ('<text x="%d" y="%d" fill="%s" font-family="%s" font-size="12" '
              'text-anchor="end">%.1f%%</text>\n'
              % (lx + 150, ly, MUTED, FONT, 100 * value / grand))
    return s + "</svg>\n"


# ------------------------------------------------------------- the streak

CALENDAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def graphql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, headers={
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-card",
            **({"Authorization": "Bearer " + TOKEN} if TOKEN else {}),
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        print("  ! graphql -> HTTP %s" % e.code, file=sys.stderr)
        return None
    if payload.get("errors"):
        print("  ! graphql -> %s" % payload["errors"][0].get("message"),
              file=sys.stderr)
        return None
    return payload.get("data")


def contribution_days(created):
    """Every day the account has existed, as {date: count}.

    The calendar can only be asked for a year at a time, so this walks one
    year per request from the account's creation. A private contribution
    graph reads as all-zero to anyone but its owner, so this needs a token
    belonging to the account, carrying read:user. Actions' built-in token
    is neither.
    """
    today = datetime.date.today()
    start = datetime.date(created.year, created.month, created.day)
    days, year = {}, start.year
    while year <= today.year:
        a = max(start, datetime.date(year, 1, 1))
        b = min(today, datetime.date(year, 12, 31))
        if a > b:
            year += 1
            continue
        data = graphql(CALENDAR_QUERY, {
            "login": USER,
            "from": a.isoformat() + "T00:00:00Z",
            "to": b.isoformat() + "T23:59:59Z",
        })
        if data is None:
            return None
        try:
            weeks = (data["user"]["contributionsCollection"]
                     ["contributionCalendar"]["weeks"])
        except (KeyError, TypeError):
            return None
        for week in weeks:
            for day in week["contributionDays"]:
                date = datetime.date.fromisoformat(day["date"])
                if start <= date <= today:
                    days[date] = day["contributionCount"]
        year += 1
    return days


def streaks(days):
    """Total, current and longest run of consecutive contributing days."""
    if not days:
        return None
    today = datetime.date.today()
    total = sum(days.values())

    longest = best = None
    run_start = None
    for date in sorted(days):
        if days[date] > 0:
            if run_start is None:
                run_start = date
            run = (date - run_start).days + 1
            if best is None or run > best:
                best, longest = run, (run_start, date)
        else:
            run_start = None

    # A day with nothing on it yet does not break a streak until it is over,
    # which is why today being empty counts as the streak still standing.
    cursor = today
    if days.get(today, 0) == 0:
        cursor = today - datetime.timedelta(days=1)
    current, current_end = 0, cursor
    while days.get(cursor, 0) > 0:
        current += 1
        cursor -= datetime.timedelta(days=1)
    current_start = cursor + datetime.timedelta(days=1)

    return {
        "streak_total": total,
        "streak_current": current,
        "streak_current_from": current_start.isoformat() if current else None,
        "streak_current_to": current_end.isoformat() if current else None,
        "streak_longest": best or 0,
        "streak_longest_from": longest[0].isoformat() if longest else None,
        "streak_longest_to": longest[1].isoformat() if longest else None,
        "streak_first_day": min(days).isoformat(),
        "streak_as_of": today.isoformat(),
    }


def pretty(iso, with_year=True):
    if not iso:
        return ""
    d = datetime.date.fromisoformat(iso)
    out = d.strftime("%b %-d")
    return out + d.strftime(", %Y") if with_year else out


def streak_card(st):
    w, h = 495, 195
    cols = w / 3.0
    s = card_open(w, h, "Contribution streak",
                  "%s contributions, current streak %s days, longest %s days"
                  % (st["streak_total"], st["streak_current"],
                     st["streak_longest"]))

    for i in (1, 2):   # the two rules between the three columns
        s += ('<line x1="%.1f" y1="30" x2="%.1f" y2="%d" stroke="#2c3050"/>\n'
              % (cols * i, cols * i, h - 30))

    def column(cx, big, label, under, highlight=False):
        colour = ICON if highlight else TITLE
        out = ('<text x="%.1f" y="78" fill="%s" font-family="%s" '
               'font-size="28" font-weight="700" text-anchor="middle">%s'
               '</text>\n' % (cx, colour, FONT, esc(big)))
        out += ('<text x="%.1f" y="106" fill="%s" font-family="%s" '
                'font-size="13" text-anchor="middle">%s</text>\n'
                % (cx, TEXT, FONT, esc(label)))
        out += ('<text x="%.1f" y="128" fill="%s" font-family="%s" '
                'font-size="11" text-anchor="middle">%s</text>\n'
                % (cx, MUTED, FONT, esc(under)))
        return out

    first = pretty(st.get("streak_first_day"))
    s += column(cols * 0.5, group(st["streak_total"]), "Total Contributions",
                "%s \u2013 Present" % first)

    if st["streak_current"]:
        span = "%s \u2013 %s" % (pretty(st["streak_current_from"], False),
                                 pretty(st["streak_current_to"], False))
    else:
        span = "\u2014"
    s += column(cols * 1.5, group(st["streak_current"]), "Current Streak",
                span, highlight=True)

    if st["streak_longest"]:
        span = "%s \u2013 %s" % (pretty(st["streak_longest_from"], False),
                                 pretty(st["streak_longest_to"], False))
    else:
        span = "\u2014"
    s += column(cols * 2.5, group(st["streak_longest"]), "Longest Streak",
                span)

    # A streak is a claim about today. If the figures could not be refreshed
    # today, the card says when they were, rather than implying otherwise.
    if st.get("streak_as_of") != datetime.date.today().isoformat():
        s += ('<text x="%d" y="%d" fill="%s" font-family="%s" font-size="10" '
              'text-anchor="middle">as of %s</text>\n'
              % (w // 2, h - 14, MUTED, FONT, pretty(st.get("streak_as_of"))))
    return s + "</svg>\n"


README = os.path.join(os.path.dirname(OUT), "README.md")


STREAK_START = "<!-- streak-card:start -->"
STREAK_END = "<!-- streak-card:end -->"


def sync_streak_block(text):
    """Show the streak card only while there is a real one to show.

    The card depends on a token that can read this profile's contributions,
    which the built-in one cannot. Rather than leave a broken image on the
    page whenever that is missing, the README carries a slot that this
    fills when the card exists and empties when it does not.
    """
    if STREAK_START not in text or STREAK_END not in text:
        return text
    path = os.path.join(OUT, "streak.svg")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:8]
        body = ('\n<p align="center">\n'
                '  <img height="175" src="assets/streak.svg?h=%s" '
                'alt="Contribution streak: total contributions, current '
                'streak and longest streak">\n</p>\n' % digest)
    else:
        body = "\n"
    head = text[:text.index(STREAK_START) + len(STREAK_START)]
    tail = text[text.index(STREAK_END):]
    return head + body + tail


def stamp_readme():
    """Point the README at each card by content hash.

    GitHub proxies and caches README images, keyed on their URL. Rewriting
    a card without changing its address leaves the old picture on the page
    - which is how a card reading zeroes outlived the commit that fixed it.
    A hash in the query string changes exactly when the picture does.
    """
    try:
        with open(README, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return
    out = text
    for name in ("github-stats.svg", "top-langs.svg", "streak.svg"):
        path = os.path.join(OUT, name)
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()[:8]
        except OSError:
            continue
        out = re.sub(r'src="assets/%s(\?h=[0-9a-f]+)?"' % re.escape(name),
                     'src="assets/%s?h=%s"' % (name, digest), out)
    out = sync_streak_block(out)
    if out != text:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("stamped README with the new card hashes")


def main():
    if not TOKEN:
        print("warning: no token, the API will rate-limit quickly",
              file=sys.stderr)
    known = load_known()

    print("fetching repositories...")
    repos = public_repos()
    if not repos:
        print("no repositories came back; refusing to overwrite the cards "
              "with an empty one", file=sys.stderr)
        return 1
    stars = sum(r.get("stargazers_count", 0) for r in repos)
    print("  %d public source repos, %d stars" % (len(repos), stars))

    user, _ = api("users/%s" % USER)
    user = user or {}
    # Actions' built-in token gets a reduced user object whose name is just
    # the login, so a scheduled run would quietly retitle the card from
    # "Matthew Macri" to "MatthewMacri". A stored display name that differs
    # from the login was read with a token that could see it, so it wins.
    name = user.get("name") or USER
    if name == USER and known.get("name"):
        name = known["name"]

    commits, _ = api("search/commits?q=author:%s&per_page=1" % USER)
    commits = commits.get("total_count") if commits else None
    prs = count("author:%s+type:pr" % USER)
    issues = count("author:%s+type:issue" % USER)

    # Anything that could not be read falls back to the last figure that
    # could. A card showing a slightly old number is honest; a card showing
    # a zero because a request failed is not.
    fresh = {"commits": commits, "prs": prs, "issues": issues}
    values, stale = {}, []
    for key, got in fresh.items():
        value, is_fresh = keep_best(got, known, key)
        values[key] = value
        if not is_fresh and value is not None:
            stale.append(key)
    if stale:
        print("  could not read %s; keeping the last known figures"
              % ", ".join(sorted(stale)), file=sys.stderr)
    print("  commits %s, PRs %s, issues %s"
          % tuple(values[k] if values[k] is not None else "unknown"
                  for k in ("commits", "prs", "issues")))

    print("reading the contribution calendar...")
    created = user.get("created_at")
    created = (datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").date()
               if created else datetime.date(2023, 1, 1))
    days = contribution_days(created)
    st = streaks(days) if days else None
    if st and st["streak_total"] == 0:
        # An all-zero calendar is what a private contribution graph looks
        # like to a token that cannot see it, and an account with commits
        # to its name has not really contributed nothing. Treat it as
        # unreadable rather than as an answer - whether or not there are
        # stored figures to fall back on, a zeroed streak card is worse
        # than no streak card.
        print("  calendar came back empty - this token cannot see the "
              "profile's contributions", file=sys.stderr)
        st = None
    if st:
        print("  %d contributions, current streak %d, longest %d"
              % (st["streak_total"], st["streak_current"],
                 st["streak_longest"]))
    else:
        st = {k: known[k] for k in known if k.startswith("streak_")}
        if not st.get("streak_total"):
            # Nothing real was ever stored either - draw no streak card at
            # all rather than one asserting a person has done nothing.
            st = {}
            print("  no streak figures available; leaving that card out",
                  file=sys.stderr)
        else:
            print("  keeping the streak figures from %s"
                  % st.get("streak_as_of", "an earlier run"), file=sys.stderr)

    print("reading file trees for language shares...")
    totals = language_bytes(repos)
    if not totals:
        print("no language data; refusing to write an empty card",
              file=sys.stderr)
        return 1
    grand = sum(totals.values())
    print("  " + ", ".join("%s %.1f%%" % (k, 100 * v / grand)
                           for k, v in totals.most_common(6)))

    # A row whose figure was never once obtained is left off rather than
    # shown as nothing.
    rows = [("Total Stars Earned", stars, "star"),
            ("Total Commits", values["commits"], "commit"),
            ("Total PRs", values["prs"], "pr"),
            ("Total Issues", values["issues"], "issue"),
            ("Public Repositories", len(repos), "repo")]
    rows = [r for r in rows if r[1] is not None]

    os.makedirs(OUT, exist_ok=True)
    cards = [("github-stats.svg", stats_card(rows, name)),
             ("top-langs.svg", langs_card(totals))]
    if st.get("streak_total") is not None:
        cards.append(("streak.svg", streak_card(st)))
    for filename, svg in cards:
        with open(os.path.join(OUT, filename), "w", encoding="utf-8") as fh:
            fh.write(svg)
        print("wrote assets/%s" % filename)

    stamp_readme()

    keep = dict(known)
    keep.update({k: v for k, v in values.items() if v is not None})
    keep.update({"name": name, "stars": stars, "repos": len(repos)})
    keep.update({k: v for k, v in st.items() if v is not None})
    with open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(keep, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote assets/stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
