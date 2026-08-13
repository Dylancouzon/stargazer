#!/usr/bin/env python3
"""Resumable GraphQL stargazer collector. Caches every page to disk so a
crash/interrupt just resumes. Run repeatedly until it prints DONE for all repos."""
import json, os, subprocess, sys, time, urllib.request, urllib.error

from pipeline_config import load_repos, load_activity_types, get_token

REPOS = load_repos()

CACHE_ROOT = os.path.join(os.path.dirname(__file__), "outputs", ".cache")
QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    stargazers(first:100, after:$cursor, orderBy:{field:STARRED_AT, direction:ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      edges {
        starredAt
        node { login name location company websiteUrl url }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""

TOKEN = get_token()


def gh_graphql(owner, name, cursor, retries=6):
    body = json.dumps({"query": QUERY, "variables": {"owner": owner, "name": name, "cursor": cursor}}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=body,
            headers={
                "Authorization": f"bearer {TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "stargazer-nyc-collector",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                if "errors" in data:
                    raise RuntimeError(str(data["errors"]))
                return data["data"]
        except urllib.error.HTTPError as e:
            wait = int(e.headers.get("Retry-After", 0)) or (2 ** attempt)
            if e.code in (502, 503, 504) or e.code == 403 or e.code == 429:
                print(f"  transient HTTP {e.code}, retry in {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  error {e!r}, retry in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"failed after {retries} retries for {owner}/{name} cursor={cursor}")


def repo_dir(owner, name):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    os.makedirs(d, exist_ok=True)
    return d


def load_state(d):
    p = os.path.join(d, "state.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {"cursor": None, "page_index": 0, "done": False, "total_count": None, "scanned": 0, "errors": []}


def save_state(d, state):
    p = os.path.join(d, "state.json")
    tmp = p + ".tmp"
    json.dump(state, open(tmp, "w"))
    os.replace(tmp, p)


def collect_repo(owner, name):
    d = repo_dir(owner, name)
    state = load_state(d)
    if state["done"]:
        print(f"{owner}/{name}: already complete ({state['scanned']} scanned)")
        return
    while not state["done"]:
        page_file = os.path.join(d, f"page_{state['page_index']:05d}.json")
        if os.path.exists(page_file):
            page = json.load(open(page_file))
        else:
            try:
                data = gh_graphql(owner, name, state["cursor"])
            except Exception as e:
                state["errors"].append(f"page {state['page_index']}: {e}")
                save_state(d, state)
                print(f"{owner}/{name}: FAILED page {state['page_index']}: {e}", flush=True)
                return
            page = data["repository"]["stargazers"]
            state["total_count"] = page["totalCount"]
            json.dump(page, open(page_file, "w"))
            remaining = data["rateLimit"]["remaining"]
            if remaining < 50:
                print(f"  rate limit low ({remaining}), sleeping 60s", flush=True)
                time.sleep(60)
        state["scanned"] += len(page["edges"])
        state["cursor"] = page["pageInfo"]["endCursor"]
        state["page_index"] += 1
        state["done"] = not page["pageInfo"]["hasNextPage"]
        save_state(d, state)
        if state["page_index"] % 20 == 0 or state["done"]:
            print(f"{owner}/{name}: page {state['page_index']}, scanned {state['scanned']}/{state['total_count']}", flush=True)
        time.sleep(0.15)
    print(f"{owner}/{name}: DONE, {state['scanned']} scanned")


def main():
    if "starred" not in load_activity_types():
        print("'starred' not in config.json activity_types, skipping.")
        return
    for owner, name, _, _ in REPOS:
        print(f"=== {owner}/{name} ===", flush=True)
        collect_repo(owner, name)


if __name__ == "__main__":
    main()
