#!/usr/bin/env python3
"""Phase 1b: resumable collection of forks, contributors, and issue/PR authors
for all 5 repos (stargazers are handled separately by collect.py, and are only
reachable for qdrant/qdrant per the access restriction documented in the README).

Forks come from GraphQL with owner profile fields inline (no extra calls needed).
Contributors and issue/PR authors only expose a login, so their profiles are
fetched via REST and cached in profile_cache.json, shared across activity types
and repos to avoid repeat lookups."""
import json, os, subprocess, time, urllib.error, urllib.request
from datetime import date, timedelta

from pipeline_config import load_repos, load_activity_types, get_token

GITHUB_FOUNDING = date(2008, 1, 1)

# note: GitHub renames repos; if a configured repo has moved (e.g. pinecone-io's
# python client became pinecone-io/python-sdk) the API needs the current owner/name.
REPOS = load_repos()
ACTIVITY_TYPES = load_activity_types()

BASE = os.path.dirname(__file__)
CACHE_ROOT = os.path.join(BASE, "outputs", ".cache")
TOKEN = get_token()

FORKS_QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    forks(first:100, after:$cursor, orderBy:{field:CREATED_AT, direction:ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        createdAt
        owner {
          login
          __typename
          ... on User { name location company websiteUrl url }
        }
      }
    }
  }
  rateLimit { remaining }
}
"""


def gh_graphql(query, variables, retries=6):
    body = json.dumps({"query": query, "variables": variables}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.github.com/graphql", data=body,
            headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json",
                     "User-Agent": "stargazer-nyc-collector"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if "errors" in data:
                    raise RuntimeError(str(data["errors"]))
                return data["data"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def gh_rest(url, retries=6):
    for attempt in range(retries):
        req = urllib.request.Request(
            url, headers={"Authorization": f"bearer {TOKEN}", "Accept": "application/vnd.github+json",
                           "User-Agent": "stargazer-nyc-collector"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read()), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, {}
            wait = int(e.headers.get("Retry-After", 0)) or (2 ** attempt)
            time.sleep(wait)
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {retries} retries: {url}")


def repo_dir(owner, name):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    os.makedirs(d, exist_ok=True)
    return d


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def collect_forks(owner, name):
    d = repo_dir(owner, name)
    state_path = os.path.join(d, "forks_state.json")
    state = load_json(state_path, {"cursor": None, "page_index": 0, "done": False, "scanned": 0, "errors": []})
    while not state["done"]:
        pf = os.path.join(d, f"forks_page_{state['page_index']:05d}.json")
        if os.path.exists(pf):
            page = json.load(open(pf))
        else:
            try:
                data = gh_graphql(FORKS_QUERY, {"owner": owner, "name": name, "cursor": state["cursor"]})
            except Exception as e:
                state["errors"].append(f"page {state['page_index']}: {e}")
                save_json(state_path, state)
                print(f"{owner}/{name} forks: FAILED page {state['page_index']}: {e}", flush=True)
                return
            page = data["repository"]["forks"]
            with open(pf, "w") as f:
                json.dump(page, f)
        state["scanned"] += len(page["nodes"])
        state["cursor"] = page["pageInfo"]["endCursor"]
        state["page_index"] += 1
        state["done"] = not page["pageInfo"]["hasNextPage"]
        save_json(state_path, state)
        time.sleep(0.15)
    print(f"{owner}/{name} forks: DONE, {state['scanned']} scanned", flush=True)


def collect_contributors(owner, name):
    d = repo_dir(owner, name)
    state_path = os.path.join(d, "contributors_state.json")
    state = load_json(state_path, {"page": 1, "done": False, "scanned": 0, "errors": []})
    while not state["done"]:
        pf = os.path.join(d, f"contributors_page_{state['page']:05d}.json")
        if os.path.exists(pf):
            page = json.load(open(pf))
        else:
            url = f"https://api.github.com/repos/{owner}/{name}/contributors?per_page=100&anon=0&page={state['page']}"
            try:
                page, _ = gh_rest(url)
            except Exception as e:
                state["errors"].append(f"page {state['page']}: {e}")
                save_json(state_path, state)
                print(f"{owner}/{name} contributors: FAILED page {state['page']}: {e}", flush=True)
                return
            page = page or []
            with open(pf, "w") as f:
                json.dump(page, f)
        state["scanned"] += len(page)
        state["done"] = len(page) < 100
        state["page"] += 1
        save_json(state_path, state)
        time.sleep(0.15)
    print(f"{owner}/{name} contributors: DONE, {state['scanned']} scanned", flush=True)


def count_for_window(owner, name, kind, start, end):
    url = f"https://api.github.com/search/issues?q=repo:{owner}/{name}+type:{kind}+created:{start}..{end}&per_page=1"
    data, _ = gh_rest(url)
    time.sleep(2.2)  # search API: 30 req/min limit
    return (data or {}).get("total_count", 0)


def split_windows(owner, name, kind, start, end, count, depth=0):
    """Binary-split a date range until each leaf has <=1000 matches (the Search
    API's hard per-query cap), so every item is reachable across leaves. Falls
    back to accepting an over-1000 single-day leaf (documented, not silent)."""
    if count <= 1000 or start >= end or depth > 30:
        return [(start, end, count)]
    mid = start + (end - start) // 2
    left_count = count_for_window(owner, name, kind, start, mid)
    right_count = max(count - left_count, 0)
    return (split_windows(owner, name, kind, start, mid, left_count, depth + 1)
            + split_windows(owner, name, kind, mid + timedelta(days=1), end, right_count, depth + 1))


def collect_search(owner, name, kind):
    """kind: 'issue' or 'pr'. Paginates via date-windowed queries to get past
    the Search API's 1000-result-per-query cap and cover the full history."""
    d = repo_dir(owner, name)
    state_path = os.path.join(d, f"{kind}_state.json")
    state = load_json(state_path, {"windows": None, "done": False, "scanned": 0, "total_count": None,
                                    "oversized_windows": [], "errors": []})
    if state["windows"] is None:
        today = date.today()
        total = count_for_window(owner, name, kind, GITHUB_FOUNDING, today)
        state["total_count"] = total
        windows = split_windows(owner, name, kind, GITHUB_FOUNDING, today, total)
        state["windows"] = [{"start": str(s), "end": str(e), "count": c, "page": 1, "done": False} for s, e, c in windows]
        save_json(state_path, state)

    for w in state["windows"]:
        if w["done"]:
            continue
        if w["count"] > 1000 and w["start"] not in state["oversized_windows"]:
            state["oversized_windows"].append(f"{w['start']}..{w['end']} ({w['count']} items, single day, cap not splittable further)")
        while not w["done"] and w["page"] <= 10:
            pf = os.path.join(d, f"{kind}_w{w['start']}_{w['end']}_page_{w['page']:05d}.json")
            if os.path.exists(pf):
                page = json.load(open(pf))
            else:
                url = (f"https://api.github.com/search/issues?q=repo:{owner}/{name}+type:{kind}"
                       f"+created:{w['start']}..{w['end']}&sort=created&order=asc&per_page=100&page={w['page']}")
                try:
                    page, _ = gh_rest(url)
                except Exception as e:
                    state["errors"].append(f"window {w['start']}..{w['end']} page {w['page']}: {e}")
                    save_json(state_path, state)
                    print(f"{owner}/{name} {kind}: FAILED window {w['start']}..{w['end']} page {w['page']}: {e}", flush=True)
                    return
                with open(pf, "w") as f:
                    json.dump(page, f)
                time.sleep(2.2)
            items = page.get("items", [])
            state["scanned"] += len(items)
            # Search exposes at most 1,000 results. A window with exactly 1,000
            # items has ten full pages, so it has no short final page to mark it
            # complete. Do not apply this to known oversized single-day windows:
            # those are intentionally left incomplete and reported as capped.
            w["done"] = len(items) < 100 or (w["page"] == 10 and w["count"] <= 1000)
            w["page"] += 1
            save_json(state_path, state)
    state["done"] = all(w["done"] for w in state["windows"])
    save_json(state_path, state)
    oversized_note = f", {len(state['oversized_windows'])} oversized" if state["oversized_windows"] else ""
    print(f"{owner}/{name} {kind}: DONE, {state['scanned']}/{state.get('total_count')} scanned across "
          f"{len(state['windows'])} date windows{oversized_note}", flush=True)


def main():
    for owner, name, _, _ in REPOS:
        print(f"=== {owner}/{name} ===", flush=True)
        if "forked" in ACTIVITY_TYPES:
            collect_forks(owner, name)
        if "contributor" in ACTIVITY_TYPES:
            collect_contributors(owner, name)
        if "issue_or_pr_author" in ACTIVITY_TYPES:
            collect_search(owner, name, "issue")
            collect_search(owner, name, "pr")


if __name__ == "__main__":
    main()
