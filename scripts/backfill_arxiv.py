#!/usr/bin/env python3
"""Backfill exact arXiv Catchup announcement dates (not submission dates)."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import requests
from parsel import Selector


PAGE_SIZE = 2000  # arXiv's Catchup service has fixed-size pages.
ROOT = Path(__file__).resolve().parents[1]


def date_range(start, end, today=None):
    today = today or datetime.now(timezone.utc).date()
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (start, end)):
        raise ValueError("Dates must use YYYY-MM-DD.")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("start_date must not be later than end_date.")
    if first < today - timedelta(days=90) or last > today:
        raise ValueError("arXiv Catchup only supports the past 90 days through today (UTC).")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def parse_categories(value):
    categories = sorted(set(part.strip() for part in value.split(",") if part.strip()))
    if not categories or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", c) for c in categories):
        raise ValueError("Set CATEGORIES or the categories input to comma-separated arXiv categories.")
    return categories


def text_without_label(node):
    return " ".join(node.xpath(".//text()[not(ancestor::*[@class='descriptor'])]").getall()).strip()


def parse_page(html, day, page):
    """Fail closed on wrong dates, changed markup or incomplete pagination."""
    selector = Selector(text=html)
    container = selector.css("#dlpage")
    heading = " ".join(container.css("h1::text").getall()).strip()
    date_label = day.strftime("%a, %d %b %Y")
    if not heading.startswith("Catchup results for ") or not heading.endswith(" on " + date_label):
        raise ValueError(f"Unexpected Catchup page/date for {day}: {heading!r}")
    match = re.search(r"Total of\s+(\d+)\s+entries", container.xpath("string(.)").get() or "")
    if not match:
        raise ValueError(f"Missing Catchup entry count for {day}.")
    total = int(match.group(1))
    expected = min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE))
    if len(container.css("dl dt")) != expected:
        raise ValueError(f"Incomplete Catchup page {page} for {day} (expected {expected} entries).")

    papers = []
    for section in container.css("dl"):
        section_heading = " ".join(section.css("h3::text").getall()).strip()
        if section_heading.startswith("Replacement submissions"):
            continue
        if section_heading.startswith("New submissions"):
            kind = "new"
        elif section_heading.startswith("Cross submissions"):
            kind = "cross"
        elif section.css("dt"):
            raise ValueError(f"Unknown announcement section: {section_heading!r}")
        else:
            continue
        for entry in section.css("dt"):
            link = entry.css('a[title="Abstract"]::attr(href)').get() or ""
            if not re.fullmatch(r"/abs/(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", link):
                raise ValueError(f"Invalid arXiv abstract link: {link!r}")
            paper_id = re.sub(r"v\d+$", "", link.removeprefix("/abs/"))
            details = entry.xpath("following-sibling::*[1][self::dd]")
            title = text_without_label(details.css(".list-title"))
            authors = [a.xpath("string(.)").get().strip() for a in details.css(".list-authors a")]
            categories = re.findall(r"\(([A-Za-z][A-Za-z0-9.-]*)\)", text_without_label(details.css(".list-subjects")))
            summary = text_without_label(details.css("p.mathjax"))
            if not all((title, authors, categories, summary)):
                raise ValueError(f"Missing metadata for {paper_id}; refusing to publish incomplete data.")
            papers.append({
                "id": paper_id, "title": title, "authors": authors,
                "categories": categories, "summary": summary,
                "comment": text_without_label(details.css(".list-comments")) or None,
                "abs": f"https://arxiv.org/abs/{paper_id}",
                "pdf": f"https://arxiv.org/pdf/{paper_id}",
                "announcement_date": day.isoformat(), "announcement_type": kind,
            })
    return total, papers


class CatchupClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "daily-arXiv-ai-enhanced-backfill/1.0 "
            "(+https://github.com/yp51md/daily-arXiv-ai-enhanced)"
        )
        self.last_request = 0.0

    def page(self, category, day, page):
        url = f"https://arxiv.org/catchup/{category}/{day.isoformat()}"
        for attempt in range(4):
            time.sleep(max(0, 3 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(url, params={"abs": "True", "page": page}, timeout=60)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 3:
                        retry = response.headers.get("Retry-After", "")
                        time.sleep(max(10 * 2**attempt, int(retry) if retry.isdigit() else 0))
                        continue
                response.raise_for_status()
                return parse_page(response.text, day, page)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 3:
                    raise
                time.sleep(10 * 2**attempt)
        raise RuntimeError(f"Unable to fetch {url}")

    def papers(self, categories, day):
        found = {}
        for category in categories:
            total, batch = self.page(category, day, 1)
            for paper in batch:
                found.setdefault(paper["id"], paper)
            for page in range(2, math.ceil(total / PAGE_SIZE) + 1):
                next_total, batch = self.page(category, day, page)
                if next_total != total:
                    raise ValueError(f"Catchup count changed during pagination for {category}/{day}.")
                for paper in batch:
                    found.setdefault(paper["id"], paper)
        return sorted(found.values(), key=lambda paper: paper["id"])


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, papers):
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in papers), encoding="utf-8")
    temporary.replace(path)


def validate_enhanced(raw, enhanced):
    records = read_jsonl(enhanced)
    expected = {paper["id"] for paper in raw}
    actual = {paper.get("id") for paper in records}
    # The existing enhancer intentionally drops items rejected by content_filter.
    if len(records) != len(actual) or not actual.issubset(expected):
        raise ValueError("AI output contains duplicate or unexpected paper IDs.")
    fields = {"tldr", "motivation", "method", "result", "conclusion"}
    if any(not isinstance(p.get("AI"), dict) or not fields.issubset(p["AI"]) for p in records):
        raise ValueError("AI output is missing required summary fields.")


def process_day(day, categories, store, client, enhance, overwrite, language):
    data = store / "data"
    data.mkdir(parents=True, exist_ok=True)
    raw = data / f"{day}.jsonl"
    enhanced = data / f"{day}_AI_enhanced_{language}.jsonl"
    markdown = data / f"{day}.md"
    existing = raw.exists() and not overwrite
    papers = read_jsonl(raw) if existing else client.papers(categories, day)
    if existing and not papers:
        existing = False
        papers = client.papers(categories, day)
    if not papers:
        # A non-announcement day must not replace a previously populated date.
        return {"date": str(day), "status": "empty", "papers": 0}

    # Complete a day in isolation before replacing its published files.
    with tempfile.TemporaryDirectory(prefix=".backfill-", dir=store) as tmp:
        stage = Path(tmp)
        (stage / "data").mkdir()
        staged_raw = stage / "data" / raw.name
        write_jsonl(staged_raw, papers)
        updated = not existing
        if enhance:
            staged_enhanced = stage / "data" / enhanced.name
            if existing and enhanced.exists():
                try:
                    validate_enhanced(papers, enhanced)
                    shutil.copy2(enhanced, staged_enhanced)
                except (ValueError, KeyError, TypeError):
                    pass  # Resume an incomplete AI batch.
            if not staged_enhanced.exists():
                subprocess.run([sys.executable, "enhance.py", "--data", str(staged_raw)], cwd=ROOT / "ai", check=True)
                updated = True
            validate_enhanced(papers, staged_enhanced)
            if updated or not markdown.exists():
                # convert.py expects its template in cwd and a ../data/... path.
                render = stage / "render"
                render.mkdir()
                shutil.copy2(ROOT / "to_md/paper_template.md", render / "paper_template.md")
                subprocess.run([sys.executable, str(ROOT / "to_md/convert.py"), "--data", f"../data/{enhanced.name}"], cwd=render, check=True)
                updated = True
            if updated:
                shutil.copy2(staged_enhanced, enhanced)
                shutil.copy2(stage / "data" / markdown.name, markdown)
                if overwrite:
                    for old in data.glob(f"{day}_AI_enhanced_*.jsonl"):
                        if old != enhanced:
                            old.unlink()
        elif overwrite:
            # A raw-only refresh must not leave stale derived output behind.
            for old in data.glob(f"{day}_AI_enhanced_*.jsonl"):
                old.unlink()
            markdown.unlink(missing_ok=True)
        if updated:
            shutil.copy2(staged_raw, raw)
    return {"date": str(day), "status": "updated" if updated else "skipped", "papers": len(papers)}


def update_index(store):
    assets = store / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in (store / "data").glob("*.jsonl"))
    (assets / "file-list.txt").write_text("".join(name + "\n" for name in names), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--categories", default=os.environ.get("CATEGORIES", ""))
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--enhance", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    days = date_range(args.start_date, args.end_date)
    categories = parse_categories(args.categories)
    language = os.environ.get("LANGUAGE", "Chinese").strip() or "Chinese"
    if not re.fullmatch(r"[\w -]+", language):
        raise ValueError("LANGUAGE must be a language name, without path separators.")
    os.environ.update(LANGUAGE=language, CATEGORIES=",".join(categories))
    if args.enhance and any(not os.environ.get(k, "").strip() for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME")):
        raise ValueError("AI enhancement requires OPENAI_API_KEY, OPENAI_BASE_URL and MODEL_NAME.")
    if args.validate_only:
        print(f"Validated {len(days)} announcement dates; categories: {', '.join(categories)}")
        return
    store = args.store.resolve()
    store.mkdir(parents=True, exist_ok=True)
    report = []
    client = CatchupClient()
    try:
        for day in days:
            result = process_day(day, categories, store, client, args.enhance, args.overwrite, language)
            report.append(result)
            recovery = store / "backfill-results"
            recovery.mkdir(exist_ok=True)
            for output in (store / "data").glob(f"{day}*"):
                if output.is_file():
                    shutil.copy2(output, recovery / output.name)
            print(json.dumps(result), flush=True)
        update_index(store)
    except Exception as error:
        report.append({"date": str(day), "status": "failed", "papers": 0})
        raise RuntimeError(f"Backfill failed on announcement date {day}: {error}") from error
    finally:
        (store / "backfill-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("### arXiv announcement backfill\n\nDate | Status | Papers\n--- | --- | ---\n")
                for row in report:
                    handle.write(f"{row['date']} | {row['status']} | {row['papers']}\n")


if __name__ == "__main__":
    main()
