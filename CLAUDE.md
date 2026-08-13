# GitHub Stargazer Lead Finder

Finds people in a target location (default: NYC metro) who starred, forked,
contributed to, or filed issues/PRs on a set of GitHub repos. Outputs land in
`outputs/` (git-ignored, contains personal data — do not commit or share raw).

## First message in this repo: run setup, in order. One question at a time. Keep every question to one line.

**1. Token.**
Check: `test -n "$GITHUB_TOKEN" && echo SET || echo MISSING`
- SET → continue silently.
- MISSING → say exactly this, then wait:
  > Need a GitHub token. Go to github.com/settings/tokens → "Generate new token (classic)" → check only `public_repo` → generate → paste it here.
  Then `export GITHUB_TOKEN=<paste>` and confirm with a throwaway API call before moving on.
  Do not accept a bare `gh auth login` token as a substitute — it only sees repos the user maintains. If they say "I already have gh set up," tell them why that's not enough, in one line.

**2. Location.** Ask once:
  > Location to match? Default: NYC metro (New York City, the boroughs, Long Island, Westchester, NJ, CT — see `config.example.json`).
- Default → copy `location_name` / `location_keywords` / `state_codes` from `config.example.json` verbatim.
- Custom → first ask: "Just that exact place, or the surrounding metro/region too?"
  - Exact only → keywords = the place name(s) they gave, nothing else.
  - Surrounding area too → ask them to list the neighborhoods/suburbs/nearby cities/state they want included (same shape as the NYC preset — boroughs, satellite cities, state name and abbreviation).
  - Either way, note once: a bare 2-letter code only matches after a comma ("Austin, TX"), never alone — ask if they want any state/region codes at all.

**3. Repos.** Ask once:
  > Which repos — (1) Qdrant only, (2) Qdrant + the 4 vector-DB competitors already in `config.example.json`, or (3) other (give me owner/repo pairs)?
- (1) → just the qdrant entry from `config.example.json`.
- (2) → `config.example.json`'s repo list as-is.
- (3) → get owner, repo, `company_or_product`, `segment` for each.

**4. Scope.** Say once, then ask:
  > Stars only work on repos you have write access to — GitHub blocks the public stargazer list otherwise (see README). Forks, contributors, and issue/PR authors work on any public repo and are the real source for repos you don't own. More repos and more activity types take longer: GitHub caps REST at 5,000 requests/hour and Search at 30/minute — one repo with stars+forks+contributors is minutes, full issue/PR history on a large repo is hours, in hourly-capped chunks.
  > Stars only, or everything (forks + contributors + issue/PR authors too)?

**5. Write `config.json`** from `config.example.json` plus the three answers above.

**6. Run**, in order:
```
python3 collect.py
python3 collect_extra.py
python3 build_all.py
```
Background `collect.py`/`collect_extra.py` if the scope chosen implies more than ~2 minutes (any competitor repo + issue_or_pr_author ⇒ background it; Qdrant-only stars ⇒ don't bother). Report progress tersely from `outputs/.cache/*.log`, not full log dumps.

**7. Deliver.** Point to the four files in `outputs/`: `github_people_memberships.csv`, `github_people_deduplicated.csv` (include both row counts), `collection_summary.json`, and `README.md`. State plainly which repos were fully scanned and which weren't, and why.

## Notes for any run after the first
`config.json` already exists → skip straight to step 6, unless the user asks to change scope/location/repos, in which case redo the relevant step only.

## Script map
- `pipeline_config.py` — loads `config.json`, resolves the GitHub token (env `GITHUB_TOKEN` > `gh auth token`).
- `location_match.py` — matches a profile's `location` string against `config.json`.
- `collect.py` — stargazers (GraphQL, only works on write-access repos).
- `collect_extra.py` — forks, contributors, issue/PR authors (works on any public repo; issue/PR search is date-windowed past the 1000-result cap).
- `build_all.py` — joins everything, matches locations, writes the CSVs/JSON/README into `outputs/`.
