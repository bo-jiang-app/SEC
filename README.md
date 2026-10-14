# SEC 10-K Fetcher

This project fetches the latest 10-K annual reports for a list of companies from the SEC EDGAR API, saves the filing HTML, and converts the primary filing to PDF.

## Included companies

- Apple
- Meta
- Alphabet
- Amazon
- Netflix
- Goldman Sachs

## Requirements

- Python 3.9+
- `pip`
- `wkhtmltopdf` installed and on your `PATH`
  - macOS: `brew install wkhtmltopdf`
  - Ubuntu/Debian: `sudo apt-get install wkhtmltopdf`
  - Windows: install from https://wkhtmltopdf.org/

## Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Set environment variables

The SEC requires a descriptive User-Agent header. Set one before running the script:

```bash
export SEC_USER_AGENT="quartr-bot/1.0 (mailto:you@example.com)"
```

Optional: change the per-process request rate limiter. The default is 10 requests per second, matching the SEC fair-access limit:

```bash
export SEC_MAX_RPS=10
```

## Run the script

Default companies:

```bash
python fetch_10k.py --output-dir output
```

Custom companies:

```bash
python fetch_10k.py --companies Apple Meta Alphabet Amazon Netflix "Goldman Sachs" --output-dir output
```

## Output

The script writes:

- one HTML file per filing
- one PDF file per filing when `wkhtmltopdf` is available

Files are saved in the `output` directory.

## Notes

- `sec_rate_limiter.py` provides a thread-safe token-bucket rate limiter using `SEC_MAX_RPS`.
- `fetch_10k.py` uses Tenacity to retry transient request failures while obeying the fair-access rate limit.
- The script injects a `<base>` tag into the filing HTML so relative SEC resources resolve correctly during PDF conversion.
- This repo is a simple example for controlled access to SEC data and is intentionally conservative for rate limiting.
