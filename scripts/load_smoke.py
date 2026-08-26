#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request


def request_once(url, timeout, payload=None, api_key=""):
    started = time.monotonic()
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (TimeoutError, urllib.error.URLError):
        status = 0
    return status, (time.monotonic() - started) * 1000


def percentile(values, quantile):
    ordered = sorted(values)
    index = min(int(len(ordered) * quantile), len(ordered) - 1)
    return ordered[index]


def main():
    parser = argparse.ArgumentParser(description="Bounded pre-launch HTTP load smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=750)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--allow-billable", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 200:
        parser.error("requests must be 1..10000 and concurrency 1..200")
    payload = None
    path = "/api/v1/readiness/"
    if args.api_key or args.model or args.allow_billable:
        if not (args.api_key and args.model and args.allow_billable):
            parser.error("Billable chat load requires --api-key, --model and --allow-billable")
        path = "/v1/chat/completions"
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": "Ответь одним словом: тест"}],
            "max_completion_tokens": 8,
        }
    url = args.base_url.rstrip("/") + path
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(
                lambda _: request_once(url, args.timeout, payload, args.api_key),
                range(args.requests),
            )
        )
    latencies = [item[1] for item in results]
    failures = sum(1 for status, _latency in results if not 200 <= status < 300)
    error_rate = failures / len(results)
    summary = {
        "url": url,
        "requests": len(results),
        "concurrency": args.concurrency,
        "failures": failures,
        "error_rate": round(error_rate, 5),
        "average_ms": round(statistics.mean(latencies), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "max_ms": round(max(latencies), 2),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if error_rate > args.max_error_rate or summary["p95_ms"] > args.max_p95_ms:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
