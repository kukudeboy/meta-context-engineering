"""Behavior profile checks; these do not call a model or network."""

import os
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from mce.agent import AgentResult, Sandbox, ToolAgent, ToolExecutor, TOOL_SCHEMAS
from mce.config import (
    agent_tools,
    final_answer_fallback_enabled,
    include_errors_in_metrics,
    validation_attempts,
    force_write_only,
)
from mce.prompts.base_agent import build_base_agent_prompt
from mce.prompts.meta_agent import build_meta_agent_prompt
from env.base import Sample
from mce.validation import ValidationResult, format_validation_feedback


class BehaviorProfileTests(unittest.TestCase):
    def setUp(self):
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # The real .env may contain explicit overrides. Exercise profile defaults
        # separately, and test explicit override priority below.
        for key in ["MCE_MAX_VALIDATION_ATTEMPTS", "MCE_AGENT_FINAL_FALLBACK",
                    "MCE_FORCE_WRITE_ONLY", "MCE_METRICS_INCLUDE_ERRORS",
                    "MCE_AGENT_TOOLS", "MCE_AGENT_ENABLE_BASH"]:
            os.environ.pop(key, None)

    def test_cluster_safe_profile_preserves_restrictions(self):
        with patch.dict(os.environ, {
            "MCE_BEHAVIOR_PROFILE": "cluster_safe",
        }, clear=False):
            self.assertEqual(validation_attempts(), 5)
            self.assertTrue(final_answer_fallback_enabled())
            self.assertTrue(include_errors_in_metrics())
            self.assertNotIn("Edit", agent_tools("base"))
            self.assertEqual(agent_tools("meta"), ["Read", "Write", "Glob"])
            self.assertEqual(agent_tools("eval"), ["Read", "Glob"])
            prompt = build_base_agent_prompt("task", [], "/tmp/iter1", behavior_profile="cluster_safe")
            self.assertIn("ALWAYS use the `Write` tool", prompt)

    def test_paper_compatible_profile_restores_behavior(self):
        with patch.dict(os.environ, {
            "MCE_BEHAVIOR_PROFILE": "paper_compatible",
        }, clear=False):
            self.assertEqual(validation_attempts(), 3)
            self.assertFalse(final_answer_fallback_enabled())
            self.assertFalse(include_errors_in_metrics())
            self.assertIn("Edit", agent_tools("base"))
            self.assertIn("Grep", agent_tools("meta"))
            prompt = build_base_agent_prompt("task", [], "/tmp/iter1", behavior_profile="paper_compatible")
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

    def test_edit_rejects_ambiguous_and_empty_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "notes.txt"
            target.write_text("alpha alpha", encoding="utf-8")
            executor = ToolExecutor(Sandbox(root, [root], [root]), 5000)
            for old_string in ["", "missing", "alpha"]:
                _, error = executor.run("Edit", {
                    "file_path": "notes.txt", "old_string": old_string, "new_string": "beta",
                })
                self.assertTrue(error)
                self.assertEqual(target.read_text(), "alpha alpha")
            _, error = executor.run("Edit", {
                "file_path": "notes.txt", "old_string": "alpha", "new_string": "beta",
                "replace_all": True,
            })
            self.assertFalse(error)
            self.assertEqual(target.read_text(), "beta beta")

    def test_tools_deny_outside_paths_symlinks_and_readonly_utils(self):
        with tempfile.TemporaryDirectory() as folder:
            outer = Path(folder)
            root = outer / "workspace"
            root.mkdir()
            outside = outer / "private.txt"
            outside.write_text("secret")
            (root / "link.txt").symlink_to(outside)
            (root / "utils").mkdir()
            (root / "utils" / "helper.py").write_text("old")
            executor = ToolExecutor(Sandbox(root, [root], [root], [root / "utils"]), 5000)
            for path in [outside, root / "link.txt", root / "utils" / "helper.py"]:
                _, error = executor.run("Edit", {
                    "file_path": str(path), "old_string": "secret" if path != root / "utils" / "helper.py" else "old",
                    "new_string": "changed",
                })
                self.assertTrue(error)
            _, error = executor.run("Grep", {"pattern": "secret", "path": str(outside)})
            self.assertTrue(error)
            output, error = executor.run("Grep", {"pattern": "secret", "path": str(root)})
            self.assertFalse(error)
            self.assertNotIn("private.txt:", output)
            self.assertNotIn("link.txt:", output)
            self.assertEqual(outside.read_text(), "secret")

    def test_explicit_switches_override_profile_defaults(self):
        with patch.dict(os.environ, {
            "MCE_BEHAVIOR_PROFILE": "paper_compatible", "MCE_MAX_VALIDATION_ATTEMPTS": "7",
            "MCE_AGENT_FINAL_FALLBACK": "1", "MCE_METRICS_INCLUDE_ERRORS": "1",
            "MCE_FORCE_WRITE_ONLY": "1", "MCE_AGENT_ENABLE_BASH": "0",
        }):
            self.assertEqual(validation_attempts(), 7)
            self.assertTrue(final_answer_fallback_enabled())
            self.assertTrue(include_errors_in_metrics())
            self.assertTrue(force_write_only())
            self.assertNotIn("Bash", agent_tools("meta"))
            self.assertNotIn("Edit", agent_tools("base"))

    def test_invalid_configuration_fails_explicitly(self):
        with patch.dict(os.environ, {"MCE_BEHAVIOR_PROFILE": "typo"}):
            with self.assertRaises(ValueError):
                agent_tools("base")
        with patch.dict(os.environ, {"MCE_BEHAVIOR_PROFILE": "cluster_safe", "MCE_MAX_VALIDATION_ATTEMPTS": "0"}):
            with self.assertRaises(ValueError):
                validation_attempts()

    def test_system_and_validation_prompts_allow_edit_in_paper_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for profile in ["cluster_safe", "paper_compatible"]:
                with patch.dict(os.environ, {"MCE_BEHAVIOR_PROFILE": profile}):
                    agent = ToolAgent(Sandbox(root, [root], [root]), agent_tools("base"))
                    try:
                        system = agent.messages[0]["content"]
                        feedback = format_validation_feedback(ValidationResult(False, ["missing interface"]))
                        if profile == "paper_compatible":
                            self.assertIn("Write or Edit", system)
                            self.assertNotIn("COMPLETE corrected", feedback)
                        else:
                            self.assertIn("COMPLETE file content", system)
                            self.assertIn("COMPLETE corrected", feedback)
                    finally:
                        asyncio.run(agent.client.close())

    def test_meta_prompt_in_paper_mode_matches_upstream(self):
        # Freeze the two upstream prompt builders from snapshot c4b7a7c when
        # available; this check does not depend on access to GitHub.
        from importlib.util import module_from_spec, spec_from_file_location
        upstream = Path(__file__).resolve().parents[2] / "meta-context-engineering-github" / "mce" / "prompts"
        if not upstream.exists():
            self.skipTest("Optional upstream clone is not available")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, builder in [("meta_agent", build_meta_agent_prompt), ("base_agent", build_base_agent_prompt)]:
                spec = spec_from_file_location("upstream_" + name, upstream / (name + ".py"))
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                kwargs = dict(task_instruction="task", interface_signatures=[],
                              iter_dir=str(root / "iter1_sub0"), workspace_base=str(root))
                expected = getattr(module, "build_" + name + "_prompt")(**kwargs)
                actual = builder(**kwargs, behavior_profile="paper_compatible")
                self.assertEqual(actual, expected)

    def test_evaluation_reports_both_denominators(self):
        from mce.eval import batch_evaluate
        class Environment:
            def get_primary_metric_name(self):
                return "accuracy"
            async def aevaluate(self, sample, **kwargs):
                if sample.id == 1:
                    raise RuntimeError("execution failure")
                return SimpleNamespace(feedback="ok", ground_truth="a", metrics={"accuracy": 1.0}, trajectory=[])
        with patch("mce.eval.EnvironmentRegistry.get", return_value=Environment()):
            for profile, score in [("cluster_safe", 0.5), ("paper_compatible", 1.0)]:
                with patch.dict(os.environ, {"MCE_BEHAVIOR_PROFILE": profile}):
                    summary = asyncio.run(batch_evaluate({}, [Sample(0, "q"), Sample(1, "q")], "test"))["summary"]
                    self.assertEqual(summary["primary_metric_value"], score)
                    self.assertEqual(summary["metrics_all_samples"]["accuracy"], 0.5)
                    self.assertEqual(summary["metrics_success_only"]["accuracy"], 1.0)
                    self.assertEqual(summary["execution_error_rate"], 0.5)

    def test_diagnosis_fallback_enabled_only_in_cluster_profile(self):
        from env.symptom_diagnosis_agent.symptom_diagnosis_agent_environment import SymptomDiagnosisAgentEnvironment
        for profile in ["cluster_safe", "paper_compatible"]:
            fake_agent = SimpleNamespace(
                query=AsyncMock(return_value=AgentResult(texts=["No final answer"])),
                answer_without_tools=AsyncMock(return_value="[DIAGNOSIS]allergy[/DIAGNOSIS]"),
            )
            with patch.dict(os.environ, {"MCE_BEHAVIOR_PROFILE": profile}), patch(
                "env.symptom_diagnosis_agent.symptom_diagnosis_agent_environment.ToolAgent",
                return_value=fake_agent,
            ):
                asyncio.run(SymptomDiagnosisAgentEnvironment()._run_agent("task", None, 1))
                self.assertEqual(fake_agent.answer_without_tools.await_count, int(profile == "cluster_safe"))


if __name__ == "__main__":
    unittest.main()
