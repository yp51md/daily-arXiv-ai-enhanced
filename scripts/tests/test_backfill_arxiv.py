import json
from datetime import date
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import backfill_arxiv as backfill


DAY = date(2026, 11, 12)


def article(paper_id="2611.00001"):
    return f'''<dt><a name="item1">[1]</a>
      <a title="Abstract" href="/abs/{paper_id}">arXiv:{paper_id}</a></dt>
      <dd><div class="meta">
        <div class="list-title mathjax"><span class="descriptor">Title:</span>Quantum gases</div>
        <div class="list-authors"><a>Alice</a>, <a>Bob</a></div>
        <div class="list-subjects"><span class="descriptor">Subjects:</span>
          <span class="primary-subject">Quantum Gases (cond-mat.quant-gas)</span>; Quantum Physics (quant-ph)</div>
        <p class="mathjax">An abstract with <b>inline</b> text.</p>
      </div></dd>'''


def section(kind, *ids):
    return f'<dl id="articles"><h3>{kind} (continued)</h3>' + "".join(article(i) for i in ids) + "</dl>"


def page_html(body="", total=0, day=DAY):
    # Shape follows arXiv's catchup.html and list/list_macros.html templates.
    return f'''<div id="dlpage"><h1>Catchup results for Quantum Gases on {day.strftime('%a, %d %b %Y')}</h1>
      <div>Total of {total} entries for {day}</div>{body}</div>'''


def paper(paper_id="2611.00001"):
    return backfill.parse_page(page_html(section("New submissions", paper_id), 1), DAY, 1)[1][0]


class DateAndParsingTests(unittest.TestCase):
    def test_inclusive_range_across_month_boundary(self):
        self.assertEqual(backfill.date_range("2026-10-31", "2026-11-02", date(2026, 11, 13)),
                         [date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)])

    def test_invalid_future_old_and_reversed_dates(self):
        for start, end in [("2026-11-1", "2026-11-02"), ("2026-02-30", "2026-11-02"),
                           ("2026-11-12", "2026-11-11"), ("2026-11-13", "2026-11-14"),
                           ("2026-01-01", "2026-11-12")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                backfill.date_range(start, end, date(2026, 11, 13))

    def test_category_whitespace_and_invalid_paths(self):
        self.assertEqual(backfill.parse_categories(" quant-ph, cond-mat.quant-gas,quant-ph "),
                         ["cond-mat.quant-gas", "quant-ph"])
        for value in ("", "../quant-ph", "quant-ph?x=1"):
            with self.assertRaises(ValueError):
                backfill.parse_categories(value)

    def test_new_and_cross_lists_excluding_replacements(self):
        body = (section("New submissions", "2611.00001") + section("Cross submissions", "2611.00002")
                + section("Replacement submissions", "2611.00003"))
        total, papers = backfill.parse_page(page_html(body, 3), DAY, 1)
        self.assertEqual(total, 3)
        self.assertEqual([p["announcement_type"] for p in papers], ["new", "cross"])
        self.assertEqual(papers[0]["announcement_date"], "2026-11-12")
        self.assertEqual(papers[0]["categories"], ["cond-mat.quant-gas", "quant-ph"])
        self.assertEqual(papers[0]["authors"], ["Alice", "Bob"])
        self.assertNotIn("Title:", papers[0]["title"])
        self.assertIn("inline", papers[0]["summary"])

    def test_empty_day(self):
        self.assertEqual(backfill.parse_page(page_html(), DAY, 1), (0, []))

    def test_changed_date_layout_count_or_missing_metadata_fails(self):
        valid = page_html(section("New submissions", "2611.00001"), 1)
        for html in (valid.replace("12 Nov", "11 Nov"), valid.replace("Total of 1", "Total of 2"),
                     valid.replace("New submissions", "Unknown section"), valid.replace('class="mathjax"', 'class="other"'),
                     "<html>Rate limited</html>"):
            with self.assertRaises(ValueError):
                backfill.parse_page(html, DAY, 1)

    def test_second_page_continued_section(self):
        with patch.object(backfill, "PAGE_SIZE", 2):
            total, papers = backfill.parse_page(page_html(section("Cross submissions", "2611.00003"), 3), DAY, 2)
        self.assertEqual(total, 3)
        self.assertEqual(papers[0]["id"], "2611.00003")

    def test_pagination_and_cross_category_deduplication(self):
        client = backfill.CatchupClient()
        client.page = Mock(side_effect=[(3, [paper("2611.00001"), paper("2611.00002")]),
                                       (3, [paper("2611.00003")]), (1, [paper("2611.00001")])])
        with patch.object(backfill, "PAGE_SIZE", 2):
            papers = client.papers(["cond-mat.quant-gas", "quant-ph"], DAY)
        self.assertEqual(len(papers), 3)
        self.assertEqual(client.page.call_count, 3)
        self.assertEqual(client.page.call_args_list[1].args, ("cond-mat.quant-gas", DAY, 2))

    def test_rate_limit_retries_without_becoming_empty_result(self):
        client = backfill.CatchupClient()
        limited = Mock(status_code=429, headers={"Retry-After": "12"})
        ok = Mock(status_code=200, text=page_html())
        client.session.get = Mock(side_effect=[limited, ok])
        with patch.object(backfill.time, "sleep") as sleep:
            self.assertEqual(client.page("quant-ph", DAY, 1), (0, []))
        sleep.assert_any_call(12)
        self.assertEqual(client.session.get.call_count, 2)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name)
        self.client = Mock()
        self.client.papers.return_value = [paper()]

    def run_day(self, **kwargs):
        options = dict(enhance=False, overwrite=False, language="Chinese")
        options.update(kwargs)
        return backfill.process_day(DAY, ["cond-mat.quant-gas"], self.store, self.client, **options)

    def test_raw_skip_and_preserve_index_history(self):
        self.assertEqual(self.run_day()["status"], "updated")
        self.assertEqual(self.run_day()["status"], "skipped")
        self.assertEqual(self.client.papers.call_count, 1)
        (self.store / "data/2026-10-01.jsonl").write_text("{}\n")
        backfill.update_index(self.store)
        self.assertEqual((self.store / "assets/file-list.txt").read_text().splitlines(),
                         ["2026-10-01.jsonl", "2026-11-12.jsonl"])

    def test_empty_day_does_not_create_or_overwrite_data(self):
        self.client.papers.return_value = []
        self.assertEqual(self.run_day()["status"], "empty")
        self.assertFalse((self.store / "data/2026-11-12.jsonl").exists())
        self.client.papers.return_value = [paper()]
        self.run_day()
        self.client.papers.return_value = []
        self.assertEqual(self.run_day(overwrite=True)["status"], "empty")
        self.assertEqual(len(backfill.read_jsonl(self.store / "data/2026-11-12.jsonl")), 1)

    def test_failed_ai_refresh_preserves_existing_outputs(self):
        self.run_day()
        raw = self.store / "data/2026-11-12.jsonl"
        enhanced = self.store / "data/2026-11-12_AI_enhanced_Chinese.jsonl"
        enhanced.write_text("old AI output")
        before = raw.read_bytes()
        with patch.object(backfill.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "enhance")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_day(enhance=True, overwrite=True)
        self.assertEqual(raw.read_bytes(), before)
        self.assertEqual(enhanced.read_text(), "old AI output")

    def test_ai_resume_uses_real_markdown_converter_and_skips_complete_day(self):
        self.run_day()
        real_run = subprocess.run
        ai_calls = []

        def fake_ai(command, **kwargs):
            if command[1] != "enhance.py":
                return real_run(command, **kwargs)
            ai_calls.append(command)
            source = Path(command[-1])
            records = backfill.read_jsonl(source)
            for p in records:
                p["AI"] = {field: "Test summary" for field in ("tldr", "motivation", "method", "result", "conclusion")}
            backfill.write_jsonl(source.with_name("2026-11-12_AI_enhanced_Chinese.jsonl"), records)

        with patch.object(backfill.subprocess, "run", side_effect=fake_ai):
            self.assertEqual(self.run_day(enhance=True)["status"], "updated")
            self.assertEqual(self.run_day(enhance=True)["status"], "skipped")
        self.assertEqual(len(ai_calls), 1)
        self.assertIn("Quantum gases", (self.store / "data/2026-11-12.md").read_text())
        self.assertEqual(self.client.papers.call_count, 1)

    def test_raw_only_overwrite_removes_stale_ai(self):
        self.run_day()
        enhanced = self.store / "data/2026-11-12_AI_enhanced_Chinese.jsonl"
        enhanced.write_text("old AI")
        self.run_day(overwrite=True)
        self.assertFalse(enhanced.exists())

    def test_enhanced_validation_allows_existing_filter_but_not_foreign_ids(self):
        path = self.store / "enhanced.jsonl"
        path.write_text("")  # All records can be removed by the existing content filter.
        backfill.validate_enhanced([paper()], path)
        backfill.write_jsonl(path, [{"id": "foreign"}])
        with self.assertRaises(ValueError):
            backfill.validate_enhanced([paper()], path)


if __name__ == "__main__":
    unittest.main()
