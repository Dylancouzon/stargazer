#!/usr/bin/env python3
"""Phase 2: assemble location-matched people across the configured activity
types (starred, forked, contributor, issue_or_pr_author) for the configured
repos, and write outputs/nyc_github_stargazers_deduplicated.csv (one row per
person). Note: stargazers are only queryable for repos you have write access
to -- see pipeline_config.get_token's docstring.

Forks carry the owner's profile fields inline from GraphQL, no extra call
needed. Contributors and issue/PR authors only expose a login, so every one of
them needs a REST profile lookup to even know their location; those lookups
are cached in profile_cache.json, shared across activity types and repos."""
import csv
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from location_match import match_nyc_metro
from pipeline_config import load_repos, load_activity_types, get_token

REPOS = load_repos()
ACTIVITY_TYPES = load_activity_types()
# if a configured repo has been renamed on GitHub since config.json was written,
# map its current owner/name back to what was actually requested for reporting
REQUESTED_NAME = {}

BASE = os.path.dirname(__file__)
CACHE_ROOT = os.path.join(BASE, "outputs", ".cache")
OUT_DIR = os.path.join(BASE, "outputs")
PROFILE_CACHE_FILE = os.path.join(CACHE_ROOT, "profile_cache.json")

TOKEN = get_token()
COLLECTED_AT = datetime.now(timezone.utc).isoformat()

DEDUP_FIELDS = [
    "github_username", "display_name", "self_reported_location", "location_match_reason",
    "public_email", "email_status", "company", "blog_or_website", "profile_url",
    "repo_full_names", "companies_or_products", "segments", "activity_types", "activity_ats",
    "data_source", "collected_at_utc",
]


def rest_get(url, retries=6):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "Authorization": f"bearer {TOKEN}", "Accept": "application/vnd.github+json",
            "User-Agent": "stargazer-nyc-collector",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(int(e.headers.get("Retry-After", 0)) or (2 ** attempt))
        except Exception:
            time.sleep(2 ** attempt)
    return None


def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default


def save_json(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"))
    os.replace(tmp, path)


profile_cache = load_json(PROFILE_CACHE_FILE, {})
_dirty = False


def get_profile(login):
    """Fetch+cache a user's public profile. Returns None for bots/deleted users."""
    global _dirty
    if login in profile_cache:
        return profile_cache[login]
    data = rest_get(f"https://api.github.com/users/{login}")
    if data is None or data.get("type") == "Bot":
        profile_cache[login] = None
    else:
        profile_cache[login] = {
            "name": data.get("name") or "",
            "location": data.get("location") or "",
            "company": data.get("company") or "",
            "blog": data.get("blog") or "",
            "email": data.get("email") or "",
            "url": data.get("html_url") or "",
        }
    _dirty = True
    time.sleep(0.05)
    return profile_cache[login]


def email_status_for(profile, matched):
    if profile is None:
        return "profile_unavailable"
    return "public_email_present" if profile.get("email") else "no_public_email"


def make_row(login, profile, reason, owner, name, company_or_product, segment, activity_type, activity_at):
    repo_full_name = f"{owner}/{name}"
    return {
        "github_username": login,
        "display_name": (profile or {}).get("name", ""),
        "self_reported_location": (profile or {}).get("location", ""),
        "location_match_reason": reason,
        "public_email": (profile or {}).get("email", ""),
        "email_status": email_status_for(profile, True),
        "company": (profile or {}).get("company", ""),
        "blog_or_website": (profile or {}).get("blog", ""),
        "profile_url": (profile or {}).get("url", "") or f"https://github.com/{login}",
        "repo_owner": owner,
        "repo_name": name,
        "repo_full_name": REQUESTED_NAME.get(repo_full_name, repo_full_name),
        "company_or_product": company_or_product,
        "segment": segment,
        "activity_type": activity_type,
        "activity_at": activity_at or "",
        "data_source": "github_graphql_api+github_rest_api",
        "collected_at_utc": COLLECTED_AT,
    }


def collect_stars(owner, name, company_or_product, segment, rows, counts):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    state_path = os.path.join(d, "state.json")
    if not os.path.exists(state_path):
        return
    state = json.load(open(state_path))
    scanned = matched = 0
    i = 0
    while os.path.exists(os.path.join(d, f"page_{i:05d}.json")):
        page = json.load(open(os.path.join(d, f"page_{i:05d}.json")))
        for edge in page["edges"]:
            scanned += 1
            node = edge["node"]
            if node is None:
                continue
            ok, reason = match_nyc_metro(node.get("location"))
            if not ok:
                continue
            matched += 1
            login = node["login"]
            email_profile = get_profile(login) or {}
            profile = {
                "name": node.get("name") or "", "location": node.get("location") or "",
                "company": node.get("company") or "", "blog": node.get("websiteUrl") or "",
                "email": email_profile.get("email", ""), "url": node.get("url") or "",
            }
            rows.append(make_row(login, profile, reason, owner, name, company_or_product, segment,
                                  "starred", edge.get("starredAt")))
        i += 1
    counts["starred"] = {"scanned": scanned, "matched": matched, "fully_scanned": bool(state.get("done")),
                          "total_reported_by_api": state.get("total_count"), "errors": state.get("errors", [])}


def collect_forks(owner, name, company_or_product, segment, rows, counts):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    state_path = os.path.join(d, "forks_state.json")
    if not os.path.exists(state_path):
        return
    state = json.load(open(state_path))
    scanned = matched = 0
    i = 0
    while os.path.exists(os.path.join(d, f"forks_page_{i:05d}.json")):
        page = json.load(open(os.path.join(d, f"forks_page_{i:05d}.json")))
        for node in page["nodes"]:
            scanned += 1
            owner_node = node.get("owner") or {}
            if owner_node.get("__typename") != "User":
                continue
            ok, reason = match_nyc_metro(owner_node.get("location"))
            if not ok:
                continue
            matched += 1
            login = owner_node["login"]
            email_profile = get_profile(login) or {}
            profile = {
                "name": owner_node.get("name") or "", "location": owner_node.get("location") or "",
                "company": owner_node.get("company") or "", "blog": owner_node.get("websiteUrl") or "",
                "email": email_profile.get("email", ""), "url": owner_node.get("url") or "",
            }
            rows.append(make_row(login, profile, reason, owner, name, company_or_product, segment,
                                  "forked", node.get("createdAt")))
        i += 1
    counts["forked"] = {"scanned": scanned, "matched": matched, "fully_scanned": bool(state.get("done")),
                         "errors": state.get("errors", [])}


def collect_contributors(owner, name, company_or_product, segment, rows, counts):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    state_path = os.path.join(d, "contributors_state.json")
    if not os.path.exists(state_path):
        return
    state = json.load(open(state_path))
    scanned = matched = 0
    page = 1
    while os.path.exists(os.path.join(d, f"contributors_page_{page:05d}.json")):
        items = json.load(open(os.path.join(d, f"contributors_page_{page:05d}.json")))
        for item in items:
            if item.get("type") != "User":
                continue
            scanned += 1
            login = item["login"]
            profile = get_profile(login)
            if profile is None:
                continue
            ok, reason = match_nyc_metro(profile.get("location"))
            if not ok:
                continue
            matched += 1
            rows.append(make_row(login, profile, reason, owner, name, company_or_product, segment,
                                  "contributor", ""))
        page += 1
    counts["contributor"] = {"scanned": scanned, "matched": matched, "fully_scanned": bool(state.get("done")),
                              "errors": state.get("errors", [])}


def collect_search(owner, name, company_or_product, segment, kind, activity_type, rows, counts):
    d = os.path.join(CACHE_ROOT, f"{owner}__{name}")
    state_path = os.path.join(d, f"{kind}_state.json")
    if not os.path.exists(state_path):
        return
    state = json.load(open(state_path))
    seen_logins = {}  # login -> earliest created_at
    scanned = 0
    for w in state.get("windows") or []:
        page = 1
        while os.path.exists(os.path.join(d, f"{kind}_w{w['start']}_{w['end']}_page_{page:05d}.json")):
            pagedata = json.load(open(os.path.join(d, f"{kind}_w{w['start']}_{w['end']}_page_{page:05d}.json")))
            for item in pagedata.get("items", []):
                scanned += 1
                user = item.get("user") or {}
                if user.get("type") != "User":
                    continue
                login = user.get("login")
                if not login:
                    continue
                created = item.get("created_at", "")
                if login not in seen_logins or created < seen_logins[login]:
                    seen_logins[login] = created
            page += 1
    matched = 0
    for login, created in seen_logins.items():
        profile = get_profile(login)
        if profile is None:
            continue
        ok, reason = match_nyc_metro(profile.get("location"))
        if not ok:
            continue
        matched += 1
        rows.append(make_row(login, profile, reason, owner, name, company_or_product, segment,
                              activity_type, created))
    key = "issue_or_pr_author"
    prior = counts.get(key, {"scanned": 0, "matched": 0, "unique_authors": 0, "fully_scanned": True, "errors": []})
    prior["scanned"] += scanned
    prior["matched"] += matched
    prior["unique_authors"] += len(seen_logins)
    prior["fully_scanned"] = prior["fully_scanned"] and state.get("done", False) and not state.get("oversized_windows")
    prior["errors"] += [f"{kind}: {e}" for e in state.get("errors", [])]
    prior.setdefault("oversized_windows", []).extend(state.get("oversized_windows", []))
    prior[f"{kind}_total_reported_by_api"] = state.get("total_count")
    counts[key] = prior


def main():
    all_rows = []
    counts_by_repo = {}

    for owner, name, company_or_product, segment in REPOS:
        counts = {}
        if "starred" in ACTIVITY_TYPES:
            collect_stars(owner, name, company_or_product, segment, all_rows, counts)
        if "forked" in ACTIVITY_TYPES:
            collect_forks(owner, name, company_or_product, segment, all_rows, counts)
        if "contributor" in ACTIVITY_TYPES:
            collect_contributors(owner, name, company_or_product, segment, all_rows, counts)
        if "issue_or_pr_author" in ACTIVITY_TYPES:
            collect_search(owner, name, company_or_product, segment, "issue", "issue_or_pr_author", all_rows, counts)
            collect_search(owner, name, company_or_product, segment, "pr", "issue_or_pr_author", all_rows, counts)
        repo_full_name = REQUESTED_NAME.get(f"{owner}/{name}", f"{owner}/{name}")
        counts_by_repo[repo_full_name] = counts
        print(f"{owner}/{name}: {counts}")
        save_json(PROFILE_CACHE_FILE, profile_cache)  # checkpoint after each repo

    # dedup rows on (login, repo, activity_type) in case of any overlap
    seen_keys = set()
    dedup_membership_rows = []
    for row in all_rows:
        k = (row["github_username"], row["repo_full_name"], row["activity_type"])
        if k in seen_keys:
            continue
        seen_keys.add(k)
        dedup_membership_rows.append(row)
    all_rows = dedup_membership_rows

    os.makedirs(OUT_DIR, exist_ok=True)

    by_user = {}
    for row in all_rows:
        u = row["github_username"]
        rec = by_user.setdefault(u, {
            "github_username": u, "display_name": "", "self_reported_location": "", "location_match_reason": "",
            "public_email": "", "email_status": "", "company": "", "blog_or_website": "", "profile_url": "",
            "repo_full_names": [], "companies_or_products": [], "segments": [], "activity_types": [], "activity_ats": [],
            "data_source": row["data_source"], "collected_at_utc": row["collected_at_utc"],
        })
        for f_ in ["display_name", "self_reported_location", "location_match_reason",
                   "public_email", "email_status", "company", "blog_or_website", "profile_url"]:
            if row[f_]:
                rec[f_] = row[f_]
        rec["repo_full_names"].append(row["repo_full_name"])
        rec["companies_or_products"].append(row["company_or_product"])
        rec["segments"].append(row["segment"])
        rec["activity_types"].append(row["activity_type"])
        rec["activity_ats"].append(f"{row['repo_full_name']}:{row['activity_type']}:{row['activity_at']}")

    with open(os.path.join(OUT_DIR, "nyc_github_stargazers_deduplicated.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DEDUP_FIELDS)
        w.writeheader()
        for rec in by_user.values():
            row = dict(rec)
            for k in ["repo_full_names", "companies_or_products", "segments", "activity_types", "activity_ats"]:
                row[k] = ";".join(rec[k])
            w.writerow(row)

    save_json(PROFILE_CACHE_FILE, profile_cache)
    print(f"\nunique people matched: {len(by_user)}, total memberships: {len(all_rows)}")
    for repo_full_name, counts in counts_by_repo.items():
        print(f"{repo_full_name}: {counts}")


if __name__ == "__main__":
    main()
