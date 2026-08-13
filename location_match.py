"""Location matcher, driven by config.json. Case-insensitive substring/phrase
match against self-reported GitHub profile location strings. Bare 2-letter
state codes only count when they follow a comma (typical "City, ST" format) --
e.g. for the NYC preset, a bare "NY" alone never matches."""
import json
import os
import re

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            "config.json not found. Copy config.example.json to config.json "
            "(or run the onboarding conversation in CLAUDE.md) before collecting."
        )
    return json.load(open(CONFIG_PATH))


_config = load_config()
STRONG_PHRASES = [p.lower() for p in _config["location_keywords"]]
_state_codes = [c.lower() for c in _config.get("state_codes", [])]
STATE_CODE_RE = re.compile(r",\s*(" + "|".join(_state_codes) + r")\b", re.IGNORECASE) if _state_codes else None


def match_nyc_metro(location):
    """Return (is_match, reason) for a raw location string, per config.json's
    location_keywords / state_codes. Function name kept for compatibility; it
    matches whatever region config.json defines, not necessarily NYC."""
    if not location:
        return False, ""
    loc_lower = location.lower()
    for phrase in STRONG_PHRASES:
        if phrase in loc_lower:
            return True, f"matched '{phrase}'"
    if STATE_CODE_RE:
        m = STATE_CODE_RE.search(location)
        if m:
            return True, f"matched state code '{m.group(1).upper()}' in comma-separated location"
    return False, ""
