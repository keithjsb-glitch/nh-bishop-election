#!/usr/bin/env python3
"""Manual ballot entry (used by the 'Enter ballot manually' workflow).

Votes are comma-separated in candidate order: Cardoza, Gardner-Smith, Lindh-Payne.
Leave a position blank for a nominee not on the ballot, e.g. "25,37,".
Manual entries are never overwritten by the scraper.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import poll  # noqa: E402

IDS = [c["id"] for c in poll.CANDIDATES]


def votes(s):
    parts = [p.strip() for p in s.split(",")]
    if len(parts) > len(IDS):
        sys.exit(f"too many values: {s!r}")
    return {IDS[i]: poll.num(p) for i, p in enumerate(parts) if p}


def ids(s):
    out = []
    for t in filter(None, (x.strip().lower() for x in (s or "").split(","))):
        match = [i for i in IDS if i.startswith(t) or t in i]
        if len(match) != 1:
            sys.exit(f"unknown nominee {t!r}; use one of {IDS}")
        out.append(match[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--clergy", default="")
    ap.add_argument("--laity", default="")
    ap.add_argument("--clergy-cast", default="")
    ap.add_argument("--laity-cast", default="")
    ap.add_argument("--withdrawn-after", default="", help="nominees who withdrew after this ballot")
    ap.add_argument("--delete", action="store_true", help="remove this ballot")
    ap.add_argument("--final", action="store_true", help="mark the election final and stop polling")
    a = ap.parse_args()

    d = poll.load()
    rounds = {r["round"]: r for r in d.get("rounds", [])}
    if a.delete:
        rounds.pop(a.round, None)
    else:
        cur = rounds.get(a.round, {})
        r = {"round": a.round, "source": "manual", "detectedAt": cur.get("detectedAt") or poll.now_iso()}
        for order, v, cast in (("clergy", a.clergy, a.clergy_cast), ("laity", a.laity, a.laity_cast)):
            if v.strip():
                r[order] = {"votes": votes(v)}
                if cast.strip():
                    r[order]["totalCast"] = poll.num(cast)
            elif order in cur:
                r[order] = {k: cur[order][k] for k in ("votes", "totalCast") if k in cur[order]}
        wd = ids(a.withdrawn_after)
        if wd:
            r["withdrawnAfter"] = wd
        rounds[a.round] = r
    d["rounds"] = [rounds[k] for k in sorted(rounds)]
    for r in d["rounds"]:  # recompute derived fields
        for o in ("clergy", "laity"):
            if o in r:
                r[o].pop("needed", None)
                r[o].pop("majority", None)
        r.pop("withdrawnBefore", None)
    if a.final:
        d["status"] = "final"
    elif d.get("status") == "final" and not any(r.get("elected") for r in d["rounds"]):
        d["status"] = "in_progress"
    d["lastChecked"] = poll.now_iso()
    poll.finalize(d)
    poll.save(d)
    print(f"ballot {a.round} saved; status={d['status']} elected={d.get('elected')}")


if __name__ == "__main__":
    main()
