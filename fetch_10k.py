#!/usr/bin/env python3
"""Fetch latest SEC 10-K filings for a list of companies (rate-limited + retried).

This script demonstrates integration with a process-local token-bucket
rate limiter (sec_rate_limiter.get_rate_limiter) and Tenacity for retry/backoff.

Usage:
  - Set SEC_USER_AGENT environment variable to a descriptive User-Agent
    (SEC requires one, e.g. "org-bot/1.0 (mailto:you@example.com)").
  - Optional: set SEC_MAX_RPS env var to change the max requests/sec (default 10).
  - Install dependencies from requirements.txt (requests, tenacity).

Note: This is a simple example intended to be dropped into the SEC repo. For
production use add rate coordination across processes, persistent manifests,
logging, and observability.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Dict, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sec_rate_limiter import get_rate_limiter

# --- Configuration ---
REQUEST_TIMEOUT = 30
DEFAULT_USER_AGENT = "quartr-bot/1.0 (mailto:you@example.com)"

# Minimal mapping for the requested companies
COMPANY_CIKS: Dict[str, str] = {
    "apple": "0000320193",
    "meta": "0001326801",
    "alphabet": "0001652044",
    "amazon": "0001018724",
    "netflix": "0001065280",
    "goldman sachs": "0000886982",
}

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik_padded}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


def get_user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def fetch_with_retry(session: requests.Session, url: str) -> requests.Response:
    """GET a URL with Tenacity retries and a process-local rate limiter.

    The rate limiter is enforced before each attempt (including retries),
    so overall request rate will not exceed the configured SEC_MAX_RPS
    for this process.
    """
    limiter = get_rate_limiter()
    limiter.acquire()
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    # raise for status to trigger Tenacity retry on 5xx/429 etc.
    resp.raise_for_status()
    return resp


def pad_cik_to_10(cik: str) -> str:
    numeric = cik.lstrip("0")
    if numeric == "":
        numeric = "0"
    return numeric.zfill(10)


def get_submissions_json(session: requests.Session, cik: str) -> Optional[dict]:
    url = SEC_SUBMISSIONS_URL.format(cik_padded=pad_cik_to_10(cik))
    try:
        resp = fetch_with_retry(session, url)
        return resp.json()
    except requests.RequestException as exc:
        print(f"ERROR: failed to fetch submissions for CIK {cik}: {exc}", file=sys.stderr)
        return None


def find_latest_10k(submissions: dict) -> Optional[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    filing_dates = recent.get("filingDate", [])

    for i, form in enumerate(forms):
        if form and form.upper().startswith("10-K"):
            return {
                "form": form,
                "accessionNumber": accessions[i] if i < len(accessions) else None,
                "primaryDocument": primary_docs[i] if i < len(primary_docs) else None,
                "filingDate": filing_dates[i] if i < len(filing_dates) else None,
            }
    return None


def build_filing_doc_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_int = str(int(cik))
    accession_nodash = accession_number.replace("-", "")
    return f"{SEC_ARCHIVES_BASE}/{cik_int}/{accession_nodash}/{primary_document}"


def fetch_filing_html(session: requests.Session, url: str) -> Optional[str]:
    try:
        resp = fetch_with_retry(session, url)
        return resp.text
    except requests.RequestException as exc:
        print(f"ERROR: failed to fetch filing html {url}: {exc}", file=sys.stderr)
        return None


def inject_base_tag(html: str, base_href: str) -> str:
    base_tag = f'<base href="{base_href}">'
    m = re.search(r"<head[^>]*>", html, flags=re.IGNORECASE)
    if m:
        pos = m.end()
        return html[:pos] + base_tag + html[pos:]
    return base_tag + html


def convert_html_to_pdf(html_path: str, pdf_path: str) -> bool:
    wk = shutil.which("wkhtmltopdf")
    if not wk:
        print("wkhtmltopdf not found on PATH; PDF conversion skipped.", file=sys.stderr)
        return False
    try:
        subprocess.run([wk, "--enable-local-file-access", html_path, pdf_path], check=True, timeout=180)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: wkhtmltopdf failed: {exc}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("ERROR: wkhtmltopdf timed out", file=sys.stderr)
        return False


def company_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def run_for_company(name: str, cik: str, output_dir: str, session: requests.Session) -> None:
    print(f"Processing {name} (CIK {cik})...")
    submissions = get_submissions_json(session, cik)
    if not submissions:
        print(f"  skipping {name}: submissions unavailable")
        return
    filing = find_latest_10k(submissions)
    if not filing:
        print(f"  no 10-K found for {name}")
        return
    accession = filing.get("accessionNumber")
    primary_doc = filing.get("primaryDocument")
    filing_date = filing.get("filingDate")

    if not accession or not primary_doc:
        print(f"  missing accession/primary document for {name}")
        return

    print(f"  latest 10-K: {accession} filed on {filing_date} -> {primary_doc}")

    url = build_filing_doc_url(cik, accession, primary_doc)
    html = fetch_filing_html(session, url)
    if html is None:
        print(f"  failed to download filing html for {name}")
        return

    base = os.path.dirname(url) + "/"
    final = inject_base_tag(html, base)

    ensure_dir(output_dir)
    slug = company_slug(name)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    html_fn = f"{slug}-{accession.replace('-', '')}-{ts}.html"
    pdf_fn = f"{slug}-{accession.replace('-', '')}-{ts}.pdf"

    html_path = os.path.join(output_dir, html_fn)
    pdf_path = os.path.join(output_dir, pdf_fn)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(final)

    print(f"  saved HTML: {html_path}")
    if convert_html_to_pdf(html_path, pdf_path):
        print(f"  saved PDF: {pdf_path}")
    else:
        print(f"  PDF conversion skipped. You can run: wkhtmltopdf --enable-local-file-access {html_path} {pdf_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest SEC 10-K filings and save as PDF")
    parser.add_argument("--companies", nargs="+", default=list(COMPANY_CIKS.keys()), help="Companies to fetch")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    parser.add_argument("--user-agent", default=None, help="Optional User-Agent header")
    args = parser.parse_args()

    user_agent = args.user_agent or get_user_agent()
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    for company in args.companies:
        key = company.strip().lower()
        if re.fullmatch(r"\d{1,10}", key):
            cik = key.zfill(10)
            name = f"CIK-{cik}"
        else:
            cik = COMPANY_CIKS.get(key)
            if cik is None:
                # fuzzy fallback
                found = None
                for k, v in COMPANY_CIKS.items():
                    if k in key or key in k:
                        found = v
                        break
                if found is None:
                    print(f"Unknown company '{company}', skipping.")
                    continue
                cik = found
                name = company
            else:
                name = company

        run_for_company(name, cik, args.output_dir, session)


if __name__ == "__main__":
    main()
