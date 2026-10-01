"""Behavior profile checks; these do not call a model or network."""

import os
import unittest
from unittest.mock import patch

from mce.agent import Sandbox, ToolExecutor, TOOL_SCHEMAS
from mce.config import (
    agent_tools,
    final_answer_fallback_enabled,
    include_errors_in_metrics,
    validation_attempts,
)
from mce.prompts.base_agent import build_base_agent_prompt
from mce.prompts.meta_agent import build_meta_agent_prompt
from env.base import InterfaceSignature


class BehaviorProfileTests(unittest.TestCase):
    def test_cluster_safe_profile_preserves_restrictions(self):
        with patch.dict(os.environ, {
            "MCE_BEHAVIOR_PROFILE": "cluster_safe",
            "MCE_MAX_VALIDATION_ATTEMPTS": "5",
        }, clear=False):
            self.assertEqual(validation_attempts(), 5)
            self.assertTrue(final_answer_fallback_enabled())
            self.assertTrue(include_errors_in_metrics())
            self.assertNotIn("Edit", agent_tools("base"))
            prompt = build_base_agent_prompt([], [], "/tmp/iter1", behavior_profile="cluster_safe")
            self.assertIn("ALWAYS use the `Write` tool", prompt)

    def test_paper_compatible_profile_restores_behavior(self):
        with patch.dict(os.environ, {
            "MCE_BEHAVIOR_PROFILE": "paper_compatible",
            "MCE_MAX_VALIDATION_ATTEMPTS": "3",
        }, clear=False):
            self.assertEqual(validation_attempts(), 3)
            self.assertFalse(final_answer_fallback_enabled())
            self.assertFalse(include_errors_in_metrics())
            self.assertIn("Edit", agent_tools("base"))
            self.assertIn("Grep", agent_tools("meta"))
            prompt = build_base_agent_prompt([], [], "/tmp/iter1", behavior_profile="paper_compatible")
            self.assertNotIn("ALWAYS use the `Write` tool", prompt)

    def test_meta_prompt_concision_is_profile_controlled(self):
        with patch.dict(os.environ, clear=False):
            paper = build_meta_agent_prompt("task", [], "/tmp/iter1", "/tmp", "paper_compatible")
            safe = build_meta_agent_prompt("task", [], "/tmp/iter1", "/tmp", "cluster_safe")
        self.assertNotIn("MUST be under 800 words", paper)
        self.assertIn("MUST be under 800 words", safe)

    def test_edit_and_grep_tools_respect_sandbox(self):
        with self.subTest("schemas"):
            self.assertIn("Edit", TOOL_SCHEMAS)
            self.assertIn("Grep", TOOL_SCHEMAS)
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "notes.txt"
            target.write_text("alpha\nbeta\n", encoding="utf-8")
            executor = ToolExecutor(Sandbox(root, [root], [root]), 5000)
            output, error = executor.run("Edit", {
                "file_path": "notes.txt",
                "old_string": "beta",
                "new_string": "gamma",
            })
            self.assertFalse(error, output)
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")
            output, error = executor.run("Grep", {"pattern": "gamma", "path": str(root)})
            self.assertFalse(error, output)
            self.assertIn("notes.txt:2:gamma", output)


if __name__ == "__main__":
    unittest.main()
