"""Measure retrieval on the golden set: recall@10 of the expected pages for each chunking strategy and
search mode (`make eval-retrieval`). Embeds the 40 answerable questions once; no chat model.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.chunks import STRATEGIES
from app.edgar import DATA_DIR
from app.eval_v0 import recall_at_k
from app.golden import load_golden
from app.retrieve import Mode, search
from app.store import connect, db_page_store

MODES: tuple[Mode, ...] = ("vector", "fts", "hybrid")
K = 10


def main() -> int:
    from app.llm import ModelNotConfigured, get_embedder

    items = [i for i in load_golden() if i.kind == "answerable"]
    try:
        embedder = get_embedder()
    except ModelNotConfigured as exc:
        print(exc, file=sys.stderr)
        return 2
    started = time.monotonic()
    vectors = embedder.embed([i.question for i in items])
    results: dict[str, dict[str, list[float]]] = {s: {m: [] for m in MODES} for s in STRATEGIES}
    per_item: list[dict[str, object]] = []
    with connect() as conn:
        pages = db_page_store(conn)
        for item, vector in zip(items, vectors, strict=True):
            row: dict[str, object] = {"id": item.id}
            for strategy in STRATEGIES:
                for mode in MODES:
                    hits = search(
                        conn, item.ticker, strategy, item.question, vector, embedder.model, mode=mode, k=K
                    )
                    r = recall_at_k(item, [(h.accession, h.page) for h in hits], pages, k=K)
                    results[strategy][mode].append(r)
                    row[f"{strategy}/{mode}"] = r
            per_item.append(row)
    print(f"recall@{K} over {len(items)} answerable golden questions (model {embedder.model})")
    print(f"{'strategy':<8} " + " ".join(f"{m:>7}" for m in MODES))
    summary: dict[str, dict[str, float]] = {}
    for strategy in STRATEGIES:
        summary[strategy] = {m: sum(v) / len(v) for m, v in results[strategy].items()}
        print(f"{strategy:<8} " + " ".join(f"{summary[strategy][m]:>7.3f}" for m in MODES))
    print(f"query tokens={embedder.usage.tokens} seconds={time.monotonic() - started:.1f}")
    out = DATA_DIR / "evals"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out / f"retrieval-{stamp}.json")
    path.write_text(
        json.dumps(
            {
                "model": embedder.model,
                "k": K,
                "query_tokens": embedder.usage.tokens,
                "summary": summary,
                "items": per_item,
            },
            indent=1,
        )
    )
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
