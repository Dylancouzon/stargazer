"""Offline regression tests for collection completion and delivery artifacts."""
import csv
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

# Prevent imports from invoking the local GitHub CLI during offline tests.
os.environ.setdefault("GITHUB_TOKEN", "test-token")

import build_all
import collect_extra


class CollectionRegressionTests(unittest.TestCase):
    def test_exactly_thousand_search_results_completes(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(collect_extra, "CACHE_ROOT", temp_dir), \
             patch.object(collect_extra, "count_for_window", return_value=1000), \
             patch.object(collect_extra, "split_windows", return_value=[(date(2024, 1, 1), date(2024, 1, 1), 1000)]), \
             patch.object(collect_extra.time, "sleep"), \
             patch.object(collect_extra, "gh_rest", return_value=({"items": [{}] * 100}, {})):
            collect_extra.collect_search("owner", "repo", "issue")
            state_path = os.path.join(temp_dir, "owner__repo", "issue_state.json")
            with open(state_path) as f:
                state = json.load(f)

        self.assertTrue(state["done"])
        self.assertTrue(state["windows"][0]["done"])
        self.assertEqual(state["scanned"], 1000)

    def test_build_writes_all_documented_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = os.path.join(temp_dir, "cache")
            output_dir = os.path.join(temp_dir, "outputs")
            with patch.object(build_all, "REPOS", [("owner", "repo", "Product", "Segment")]), \
                 patch.object(build_all, "ACTIVITY_TYPES", set()), \
                 patch.object(build_all, "CACHE_ROOT", cache_dir), \
                 patch.object(build_all, "OUT_DIR", output_dir), \
                 patch.object(build_all, "PROFILE_CACHE_FILE", os.path.join(cache_dir, "profile_cache.json")):
                build_all.main()

            expected = {
                build_all.MEMBERSHIP_CSV,
                build_all.DEDUP_CSV,
                build_all.SUMMARY_JSON,
                build_all.DELIVERY_README,
            }
            self.assertEqual(set(os.listdir(output_dir)), expected)
            with open(os.path.join(output_dir, build_all.MEMBERSHIP_CSV)) as f:
                self.assertEqual(next(csv.reader(f)), build_all.MEMBERSHIP_FIELDS)
            with open(os.path.join(output_dir, build_all.SUMMARY_JSON)) as f:
                summary = json.load(f)
            self.assertEqual(summary["output_files"], [
                    build_all.MEMBERSHIP_CSV, build_all.DEDUP_CSV,
                    build_all.SUMMARY_JSON, build_all.DELIVERY_README,
            ])


if __name__ == "__main__":
    unittest.main()
