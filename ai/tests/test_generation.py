import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

import enhance
from generation import AI_FIELDS, generate_fields
from structure import Structure


def parsed(**fields):
    return {"parsed": Structure(**fields), "raw": None, "parsing_error": None}


COMPLETE = dict.fromkeys(AI_FIELDS, "Generated text")


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch("generation.time.sleep").start()
        self.addCleanup(patch.stopall)

    def test_missing_conclusion_keeps_other_fields_and_serializes_null(self):
        response = parsed(**{name: value for name, value in COMPLETE.items() if name != "conclusion"})
        chain = Mock()
        chain.invoke.return_value = response
        fields, status = generate_fields(chain, "Abstract", "Chinese", "2611.18373")
        self.assertEqual(chain.invoke.call_count, 3)
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["missing_fields"], ["conclusion"])
        self.assertEqual(fields["tldr"], "Generated text")
        self.assertIn('"conclusion": null', json.dumps(fields))
        self.assertIn("conclusion", chain.invoke.call_args.args[0]["content"])

    def test_later_attempt_fills_missing_fields_without_losing_prior_text(self):
        chain = Mock()
        chain.invoke.side_effect = [parsed(tldr="Original TLDR"), parsed(**COMPLETE)]
        fields, status = generate_fields(chain, "Abstract", "Chinese", "paper")
        self.assertEqual(fields["tldr"], "Original TLDR")
        self.assertEqual(status["status"], "complete")
        self.assertEqual(chain.invoke.call_count, 2)

    def test_request_failure_after_partial_response_retains_partial_data(self):
        chain = Mock()
        chain.invoke.side_effect = [parsed(tldr="Keep me"), TimeoutError("timed out"), TimeoutError("timed out")]
        fields, status = generate_fields(chain, "Abstract", "Chinese", "paper")
        self.assertEqual(fields["tldr"], "Keep me")
        self.assertIsNone(fields["conclusion"])
        self.assertEqual(status["status"], "partial")

    def test_all_requests_fail_returns_all_null_instead_of_raising(self):
        chain = Mock()
        chain.invoke.side_effect = RuntimeError("API unavailable")
        fields, status = generate_fields(chain, "Abstract", "Chinese", "paper")
        self.assertTrue(all(value is None for value in fields.values()))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(chain.invoke.call_count, 3)

    def test_raw_tool_arguments_recover_valid_fields_after_schema_error(self):
        raw = AIMessage(content="", tool_calls=[{
            "name": "Structure", "args": {"tldr": "Keep this", "conclusion": ["invalid type"]}, "id": "call_1"
        }])
        chain = Mock()
        chain.invoke.return_value = {"raw": raw, "parsed": None, "parsing_error": ValueError("invalid type")}
        fields, _ = generate_fields(chain, "Abstract", "Chinese", "paper")
        self.assertEqual(fields["tldr"], "Keep this")
        self.assertIsNone(fields["conclusion"])

    def test_malformed_tool_json_returns_null_without_inventing_text(self):
        raw = AIMessage(content="", invalid_tool_calls=[{
            "name": "Structure", "args": '{"tldr":', "id": "call_1", "error": "invalid JSON"
        }])
        chain = Mock()
        chain.invoke.return_value = {"raw": raw, "parsed": None, "parsing_error": ValueError("invalid JSON")}
        fields, status = generate_fields(chain, "Abstract", "Chinese", "paper")
        self.assertTrue(all(value is None for value in fields.values()))
        self.assertEqual(status["status"], "failed")

    def test_installed_langchain_parser_accepts_missing_and_invalid_fields(self):
        llm = ChatOpenAI(model="test-model", api_key="test-key")
        for conclusion in (None, 42):
            args = {"tldr": "Valid summary"}
            if conclusion is not None:
                args["conclusion"] = conclusion
            response = {
                "id": "test", "model": "test-model", "object": "chat.completion", "created": 0,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "Structure", "arguments": json.dumps(args)}}]
                }}],
            }
            structured = llm.with_structured_output(Structure, method="function_calling", include_raw=True)
            with patch.object(llm.client, "create", return_value=response):
                result = structured.invoke("Summarize")
            fields, _ = generate_fields(Mock(invoke=Mock(return_value=result)), "Abstract", "Chinese", "paper")
            self.assertEqual(fields["tldr"], "Valid summary")
            self.assertIsNone(fields["conclusion"])


class ReportTests(unittest.TestCase):
    def test_entire_batch_failure_still_returns_every_paper(self):
        def fail(prompt):
            raise RuntimeError("Model unavailable")

        fake_llm = Mock()
        fake_llm.with_structured_output.return_value = RunnableLambda(fail)
        with patch.object(enhance, "ChatOpenAI", return_value=fake_llm), \
             patch.object(enhance, "is_sensitive", return_value=False), \
             patch("generation.time.sleep"), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test", "GITHUB_STEP_SUMMARY": ""}):
            result = enhance.process_all_items(
                [{"id": "one", "summary": "Abstract"}, {"id": "two", "summary": "Abstract"}],
                "model", "Chinese", 2,
            )
        self.assertEqual([p["id"] for p in result], ["one", "two"])
        self.assertTrue(all(p["AI_generation"]["status"] == "failed" for p in result))
        self.assertTrue(all(value is None for p in result for value in p["AI"].values()))

    def test_mixed_batch_writes_all_papers_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            raw = root / "data/2026-11-16.jsonl"
            records = [{"id": str(i), "title": f"Paper {i}", "authors": ["Author"],
                        "categories": ["quant-ph"], "abs": f"https://arxiv.org/abs/{i}", "summary": summary}
                       for i, summary in enumerate(("Complete abstract", "Partial abstract", "Failed abstract"))]
            raw.write_text("".join(json.dumps(row) + "\n" for row in records))
            target = raw.with_name("2026-11-16_AI_enhanced_Chinese.jsonl")
            target.write_text("old report")

            def generate(prompt):
                content = prompt.to_string()
                if "Complete abstract" in content:
                    return parsed(**COMPLETE)
                if "Partial abstract" in content:
                    return parsed(tldr="Partial TLDR")
                raise RuntimeError("Model unavailable")

            fake_llm = Mock()
            fake_llm.with_structured_output.return_value = RunnableLambda(generate)
            summary = root / "summary.txt"
            with patch.object(enhance, "ChatOpenAI", return_value=fake_llm), \
                 patch.object(enhance, "is_sensitive", return_value=False), \
                 patch.object(enhance, "parse_args", return_value=argparse.Namespace(data=str(raw), max_workers=2)), \
                 patch("generation.time.sleep"), \
                 patch.dict(os.environ, {"MODEL_NAME": "model", "OPENAI_API_KEY": "test", "LANGUAGE": "Chinese", "GITHUB_STEP_SUMMARY": str(summary)}):
                enhance.main()

            result = [json.loads(line) for line in target.read_text().splitlines()]
            self.assertEqual([p["id"] for p in result], ["0", "1", "2"])
            self.assertEqual([p["AI_generation"]["status"] for p in result], ["complete", "partial", "failed"])
            self.assertIsNone(result[1]["AI"]["conclusion"])
            self.assertTrue(all(value is None for value in result[2]["AI"].values()))
            self.assertIn("- partial: 1", summary.read_text())
            self.assertIn("- failed: 1", summary.read_text())

            render = root / "render"
            render.mkdir()
            project = Path(enhance.__file__).resolve().parents[1]
            (render / "paper_template.md").write_text((project / "to_md/paper_template.md").read_text())
            subprocess.run([sys.executable, str(project / "to_md/convert.py"), "--data", f"../data/{target.name}"], cwd=render, check=True)
            markdown = raw.with_suffix(".md").read_text()
            self.assertIn("Paper 2", markdown)
            self.assertIn("Partial TLDR", markdown)
            self.assertIn("Conclusion: Not generated", markdown)
            self.assertNotIn("Conclusion: None", markdown)

    def test_existing_report_survives_setup_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "papers.jsonl"
            raw.write_text('{"id":"paper", "summary":"Abstract"}\n')
            target = Path(tmp) / "papers_AI_enhanced_Chinese.jsonl"
            target.write_text("existing report")
            with patch.object(enhance, "parse_args", return_value=argparse.Namespace(data=str(raw), max_workers=1)), \
                 patch.dict(os.environ, {"LANGUAGE": "Chinese", "OPENAI_API_KEY": ""}):
                with self.assertRaises(ValueError):
                    enhance.main()
            self.assertEqual(target.read_text(), "existing report")


if __name__ == "__main__":
    unittest.main()
