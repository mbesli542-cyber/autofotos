"""Download the visual regression photos into the local cache and verify them.

The photos listed in ``tests/visual/regression_set.json`` are third-party images
under open licences. They are NEVER committed; they live in a local cache
(default: the manifest's ``cacheDirDefault``, ``/opt/ae-regression/images``).

Usage (from processor/, any Python 3.10+ – standard library only)::

    python3 scripts/fetch_regression_set.py                      # every case
    python3 scripts/fetch_regression_set.py --cases too_far,front
    python3 scripts/fetch_regression_set.py --cache-dir /data/ae-images
    python3 scripts/fetch_regression_set.py --check              # verify only, no network

Behaviour:

- files that already exist with the manifest's sha256 are skipped,
- downloads run one at a time with a pause in between (Wikimedia rate-limits),
- HTTP 429/5xx → wait (Retry-After but at least 20 s, else 20–60 s, growing) and retry,
- every download is checked against ``sha256`` before it replaces anything; a
  mismatch is kept next to it as ``<file>.mismatch`` for a manual look,
- requests carry a generic User-Agent and no personal data.

Exit code 0 = every selected case is present and verified, 1 = problems.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "AutoExpertenRegressionFetch/1.0 (+https://github.com/mbesli542-cyber/autofotos)"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "tests" / "visual" / "regression_set.json"
#: Refuse anything larger – the manifest only lists ≤ 3840 px JPEG renditions.
MAX_BYTES = 60 * 1024 * 1024
RETRY_STATUS = {429, 500, 502, 503, 504}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:  # HTTP-date form – fall back to our own backoff
        return None


def _backoff(attempt: int, hint: float | None) -> float:
    if hint is not None:
        return min(max(hint, 20.0), 300.0)
    return min(random.uniform(20.0, 60.0) * (1.0 + 0.5 * attempt), 300.0)


def download(url: str, part: Path, *, retries: int, timeout: float) -> tuple[str | None, str]:
    """Download `url` into the temporary file `part`. Returns (sha256, "") or (None, reason)."""
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    return None, f"unexpected Content-Type {content_type!r}"
                digest = hashlib.sha256()
                size = 0
                with part.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
                        digest.update(chunk)
                        handle.write(chunk)
            return digest.hexdigest(), ""
        except urllib.error.HTTPError as error:
            part.unlink(missing_ok=True)
            if error.code not in RETRY_STATUS or attempt == retries:
                return None, f"HTTP {error.code}"
            wait = _backoff(attempt, _retry_after(error))
            print(f"    HTTP {error.code}, waiting {wait:.0f} s (attempt {attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            part.unlink(missing_ok=True)
            if attempt == retries:
                return None, f"network error: {error}"
            wait = 10.0 * (attempt + 1)
            print(f"    network error ({error}), waiting {wait:.0f} s", flush=True)
            time.sleep(wait)
        except ValueError as error:
            part.unlink(missing_ok=True)
            return None, str(error)
    return None, "gave up"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="regression manifest (JSON)")
    parser.add_argument("--cache-dir", type=Path, default=None, help="image cache (default: manifest cacheDirDefault)")
    parser.add_argument("--cases", default="", help="comma separated case ids (default: all)")
    parser.add_argument("--check", action="store_true", help="only verify the cache, never download")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between two downloads (default 3)")
    parser.add_argument("--retries", type=int, default=8, help="retries per file on 429/5xx/network errors")
    parser.add_argument("--timeout", type=float, default=120.0, help="socket timeout per request in seconds")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cache = args.cache_dir or Path(manifest.get("cacheDirDefault") or "/opt/ae-regression/images")
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    cases = [c for c in manifest["cases"] if not wanted or c["id"] in wanted]
    unknown = sorted(set(wanted) - {c["id"] for c in manifest["cases"]})
    if unknown:
        print(f"unknown case ids: {', '.join(unknown)}", file=sys.stderr)
        return 2
    cache.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    downloaded = 0
    for case in cases:
        path = cache / case["file"]
        if path.is_file():
            actual = sha256_file(path)
            if actual == case["sha256"]:
                print(f"ok        {case['id']:<18} {path}")
                continue
            print(f"stale     {case['id']:<18} sha256 {actual[:12]}… ≠ manifest {case['sha256'][:12]}…")
        else:
            print(f"missing   {case['id']:<18} {path}")
        if args.check:
            problems.append(case["id"])
            continue
        if downloaded:
            time.sleep(args.pause)
        downloaded += 1
        print(f"fetch     {case['id']:<18} {case['url']}", flush=True)
        tmp = cache / (case["file"] + ".part")
        digest, reason = download(case["url"], tmp, retries=args.retries, timeout=args.timeout)
        if digest is None:
            print(f"  FAILED  {reason}")
            problems.append(case["id"])
            continue
        if digest != case["sha256"]:
            # Wikimedia may re-render a thumbnail with other bytes – never overwrite silently.
            mismatch = cache / (case["file"] + ".mismatch")
            os.replace(tmp, mismatch)
            print(f"  MISMATCH sha256 {digest[:12]}… – kept as {mismatch}; check it by eye, then update the manifest")
            problems.append(case["id"])
            continue
        os.replace(tmp, path)
        print(f"  saved   {path} ({path.stat().st_size / 1e6:.1f} MB)")

    print(f"\n{len(cases) - len(problems)}/{len(cases)} cases verified in {cache}")
    if problems:
        print(f"problems: {', '.join(problems)}")
        if any(c.get("originalUrl") for c in cases if c["id"] in problems):
            print(
                "If a rendition URL is gone, `originalUrl` points to the full-size file (see tests/visual/README.md)."
            )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
