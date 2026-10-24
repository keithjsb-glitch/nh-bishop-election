#!/usr/bin/env python3
"""Poll the ECNH convention page for bishop-election ballot results.

Writes data/results.json. In --loop mode, commits and pushes when data changes
(or every HEARTBEAT minutes so the dashboard's "last checked" stays fresh) and
stops when a nominee has a concurrent majority in both orders, or when
data/results.json status is set to "final".
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.nhepiscopal.org/diocesan-convention-2026"
SOURCES = [SOURCE_URL, "https://www.nhepiscopalbishoptransition.org/"]
FOLLOW_RE = re.compile(r"ballot|result|elect|convention|announce", re.I)
MAX_FOLLOW = 6
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "results.json")
HEARTBEAT_MIN = 15  # GitHub Pages soft limit is 10 builds/hour

CANDIDATES = [
    {"id": "cardoza", "name": "The Rev. Ed Cardoza", "short": "Cardoza",
     "pattern": r"Cardoza"},
    {"id": "gardner-smith", "name": "The Rev. Fran Gardner-Smith", "short": "Gardner-Smith",
     "pattern": r"Gardner[\s\-‐-―]*Smith"},
    {"id": "lindh-payne", "name": "The Rev. Kristofer Lindh-Payne", "short": "Lindh-Payne",
     "pattern": r"Lindh[\s\-‐-―]*Payne"},
]

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12}
BALLOT_RE = re.compile(
    r"\b(?:ballot|round)\s*(?:no\.?|number|#)?\s*(\d{1,2})\b"
    r"|\b(" + "|".join(ORDINALS) + r")\s+(?:ballot|round)\b", re.I)
CLERGY_RE = re.compile(r"\bclerg(?:y|ical)\b", re.I)
LAY_RE = re.compile(r"\b(?:lay|laity)\b", re.I)
NUM = r"(\d{1,4}(?:\.\d+)?)"
CAST_RE = re.compile(r"(?:votes?|ballots?)\s+cast\D{0,15}" + NUM + r"|total\D{0,15}" + NUM, re.I)
NEEDED_RE = re.compile(r"(?:needed|necessary|required)(?:\s+(?:to|for)\s+(?:elect|election|(?:a\s+)?majority|choice))?\D{0,15}" + NUM, re.I)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def num(s):
    f = float(s)
    return int(f) if f.is_integer() else f


def strip_percents(text):
    return re.sub(r"\d+(?:\.\d+)?\s*%", " ", text)


def fetch_text(url=SOURCE_URL):
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (election-dashboard poller)",
                                               "Cache-Control": "no-cache"},
                     params={"_": int(time.time())})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    texts = [html_to_text(r.text)]
    # Results are sometimes embedded as a published Google Sheet/Doc.
    for fr in soup.find_all("iframe"):
        src = fr.get("src") or fr.get("data-src") or ""
        if "docs.google.com" in src:
            try:
                rr = requests.get(src, timeout=30)
                texts.append(html_to_text(rr.text))
            except Exception as e:  # noqa: BLE001
                print("iframe fetch failed:", src, e, file=sys.stderr)
    return normalize("\n".join(texts))


def completeness(r):
    """Rank parsed ballots: more orders, more nominees, then more total votes."""
    os_ = [r[o] for o in ("clergy", "laity") if o in r]
    return (len(os_), sum(len(o["votes"]) for o in os_), sum(sum(o["votes"].values()) for o in os_))


def fetch_site(url):
    """Yield page text for url plus same-site pages it links to that look like results."""
    from urllib.parse import urljoin, urlparse
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (election-dashboard poller)",
                                               "Cache-Control": "no-cache"},
                     params={"_": int(time.time())})
    r.raise_for_status()
    yield fetch_text_from_html(r.text)
    host = urlparse(url).netloc
    soup = BeautifulSoup(r.text, "html.parser")
    seen, n = {url.rstrip("/")}, 0
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0].split("?")[0]
        if urlparse(href).netloc != host or href.rstrip("/") in seen:
            continue
        if not FOLLOW_RE.search(a.get_text(" ") + " " + href) or re.search(r"\.(pdf|jpe?g|png)$", href, re.I):
            continue
        seen.add(href.rstrip("/"))
        try:
            rr = requests.get(href, timeout=20, headers={"User-Agent": "Mozilla/5.0 (election-dashboard poller)"},
                              params={"_": int(time.time())})
            if rr.ok:
                yield fetch_text_from_html(rr.text)
        except Exception as e:  # noqa: BLE001
            print("follow failed:", href, e, file=sys.stderr)
        n += 1
        if n >= MAX_FOLLOW:
            break


def fetch_text_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    texts = [html_to_text(html)]
    for fr in soup.find_all("iframe"):
        src = fr.get("src") or fr.get("data-src") or ""
        if "docs.google.com" in src:
            try:
                texts.append(html_to_text(requests.get(src, timeout=30).text))
            except Exception as e:  # noqa: BLE001
                print("iframe fetch failed:", src, e, file=sys.stderr)
    return normalize("\n".join(texts))


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):  # keep table rows on one line
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(soup.new_string("\n" + " | ".join(cells) + "\n"))
    return soup.get_text("\n")


def normalize(text):
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def votes_in(segment, cands):
    """Candidate name followed (within 40 non-digit chars) by a number."""
    seg = strip_percents(segment)
    out = {}
    for c in cands:
        m = re.search(c["pattern"] + r"[^\d\n]{0,40}?(?:\n[^\d\n]{0,10})?" + NUM, seg, re.I)
        if m:
            out[c["id"]] = num(m.group(1))
    return out


def order_meta(segment):
    seg = strip_percents(segment)
    meta = {}
    m = CAST_RE.search(seg)
    if m:
        meta["totalCast"] = num(m.group(1) or m.group(2))
    m = NEEDED_RE.search(seg)
    if m:
        meta["needed"] = num(m.group(1))
    return meta


def parse_section(sec):
    """Return {'clergy': {...}, 'laity': {...}} or None."""
    lines = sec.split("\n")
    # Row mode: "Cardoza  20  70" with a header naming the order columns.
    rows = {}
    for c in CANDIDATES:
        for ln in lines:
            hits = sum(bool(re.search(o["pattern"], ln, re.I)) for o in CANDIDATES)
            if hits == 1 and re.search(c["pattern"], ln, re.I):
                nums = re.findall(NUM, strip_percents(re.sub(c["pattern"], " ", ln, flags=re.I)))
                if len(nums) >= 2:
                    rows[c["id"]] = [num(n) for n in nums[:2]]
                break
    if len(rows) >= 2:
        clergy_first = True
        for ln in lines:
            cm, lm = CLERGY_RE.search(ln), LAY_RE.search(ln)
            if cm and lm:
                clergy_first = cm.start() < lm.start()
                break
        ci, li = (0, 1) if clergy_first else (1, 0)
        res = {"clergy": {"votes": {k: v[ci] for k, v in rows.items()}},
               "laity": {"votes": {k: v[li] for k, v in rows.items()}}}
        for order, rx in (("clergy", CLERGY_RE), ("laity", LAY_RE)):
            for ln in lines:
                if rx.search(ln) and not (CLERGY_RE.search(ln) and LAY_RE.search(ln)):
                    res[order].update(order_meta(ln))
        return res

    # Segment mode: a Clergy block and a Lay block.
    cm, lm = CLERGY_RE.search(sec), LAY_RE.search(sec)
    res = {}
    if cm and lm:
        a, b = sorted([("clergy", cm.start()), ("laity", lm.start())], key=lambda x: x[1])
        segs = {a[0]: sec[a[1]:b[1]], b[0]: sec[b[1]:]}
    elif cm:
        segs = {"clergy": sec[cm.start():]}
    elif lm:
        segs = {"laity": sec[lm.start():]}
    else:
        return None
    for order, seg in segs.items():
        v = votes_in(seg, CANDIDATES)
        if len(v) >= 2:
            res[order] = {"votes": v, **order_meta(seg)}
    return res or None


def parse(text):
    marks = []
    for m in BALLOT_RE.finditer(text):
        n = int(m.group(1)) if m.group(1) else ORDINALS[m.group(2).lower()]
        marks.append((m.start(), n))
    rounds = {}
    for i, (pos, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(text), pos + 3000)
        sec = text[pos:end]
        parsed = parse_section(sec)
        if not parsed:
            continue
        r = rounds.setdefault(n, {"round": n})
        for k, v in parsed.items():
            if k not in r or len(v["votes"]) > len(r[k]["votes"]):
                r[k] = v
    # Withdrawals named anywhere on the page.
    withdrawn = set()
    for c in CANDIDATES:
        if re.search(c["pattern"] + r"[^.\n]{0,80}withdr|withdr[^.\n]{0,80}" + c["pattern"], text, re.I):
            withdrawn.add(c["id"])
    return [rounds[k] for k in sorted(rounds)], withdrawn


def load():
    if os.path.exists(DATA):
        with open(DATA) as f:
            return json.load(f)
    return {}


def finalize(d):
    """Derive majority flags, withdrawals, status. Returns elected id or None."""
    d.setdefault("candidates", [{k: c[k] for k in ("id", "name", "short")} for c in CANDIDATES])
    elected = None
    prev_ids = None
    for r in sorted(d.get("rounds", []), key=lambda x: x["round"]):
        ids = set()
        for order in ("clergy", "laity"):
            o = r.get(order)
            if not o:
                continue
            ids |= set(o["votes"])
            total = o.get("totalCast") or sum(o["votes"].values())
            o["totalCast"] = total
            o.setdefault("needed", int(total // 2) + 1)
            o["majority"] = [k for k, v in o["votes"].items() if v > total / 2]
        if prev_ids:
            r["withdrawnBefore"] = sorted(set(r.get("withdrawnBefore", [])) | (prev_ids - ids))
        prev_ids = ids or prev_ids
        both = set(r.get("clergy", {}).get("majority", [])) & set(r.get("laity", {}).get("majority", []))
        r["elected"] = next(iter(both), None)
        if r["elected"] and not elected:
            elected = r["elected"]
    d["elected"] = elected
    if elected:
        d["status"] = "final"
    elif d.get("status") != "final":
        d["status"] = "in_progress" if d.get("rounds") else "waiting"
    return elected


def merge(d, parsed, withdrawn):
    existing = {r["round"]: r for r in d.get("rounds", [])}
    for p in parsed:
        cur = existing.get(p["round"])
        if cur and cur.get("source") == "manual":
            continue  # manual entry wins
        new = {"round": p["round"], "source": "scrape",
               "detectedAt": cur["detectedAt"] if cur else now_iso()}
        for order in ("clergy", "laity"):
            if order in p:
                new[order] = {"votes": p[order]["votes"],
                              **{k: p[order][k] for k in ("totalCast", "needed") if k in p[order]}}
        existing[p["round"]] = new
    d["rounds"] = [existing[k] for k in sorted(existing)]
    if withdrawn and d["rounds"]:
        last = d["rounds"][-1]
        on_ballot = set(last.get("clergy", {}).get("votes", {})) | set(last.get("laity", {}).get("votes", {}))
        still = withdrawn & on_ballot  # withdrew after the latest ballot
        if still:
            last["withdrawnAfter"] = sorted(set(last.get("withdrawnAfter", [])) | still)


def content_hash(d):
    core = {k: v for k, v in d.items() if k not in ("lastChecked", "lastError")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def save(d):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)


def commit_push(msg):
    git("add", "data/results.json")
    if not git("diff", "--cached", "--quiet").returncode:
        return
    git("commit", "-m", msg)
    if git("push").returncode == 0:
        return
    # A manual entry landed meanwhile: drop our commit; the next poll rebuilds on top of it.
    git("fetch")
    git("reset", "--hard", "@{u}")
    print("push rejected; reset to remote, will retry next poll", file=sys.stderr)


def once(commit=False, force_commit=False):
    if commit:
        git("fetch")
        git("reset", "--hard", "@{u}")
    d = load()
    before = content_hash(d)
    best, withdrawn, errors = {}, set(), []
    for url in SOURCES:
        try:
            for page_text in fetch_site(url):
                rounds, wd = parse(page_text)
                withdrawn |= wd
                for r in rounds:
                    if completeness(r) > completeness(best.get(r["round"], {})):
                        best[r["round"]] = r
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {type(e).__name__}: {e}"[:200])
            print("fetch/parse error:", errors[-1], file=sys.stderr)
    if best or withdrawn:
        merge(d, [best[k] for k in sorted(best)], withdrawn)
    if len(errors) == len(SOURCES):
        d["lastError"] = " | ".join(errors)[:400]
    else:
        d.pop("lastError", None)
    d["source"] = SOURCE_URL
    d["sources"] = SOURCES
    d["lastChecked"] = now_iso()
    elected = finalize(d)
    changed = content_hash(d) != before
    save(d)
    if commit and (changed or force_commit):
        n = len(d.get("rounds", []))
        commit_push(f"results: {n} ballot(s){' — ELECTED ' + elected if elected else ''}"
                    if changed else "heartbeat")
    return d, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--max-minutes", type=int, default=345)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--parse-file", help="parse a local text/html file and print JSON (testing)")
    a = ap.parse_args()

    if a.parse_file:
        raw = open(a.parse_file).read()
        rounds, wd = parse(normalize(html_to_text(raw) if "<" in raw else raw))
        print(json.dumps({"rounds": rounds, "withdrawn": sorted(wd)}, indent=2))
        return

    if load().get("status") == "final":
        print("already final; exiting")
        return
    if not a.loop:
        d, ch = once(commit=a.commit)
        print(json.dumps(d, indent=2))
        return

    deadline = time.time() + a.max_minutes * 60
    last_commit = 0.0
    while time.time() < deadline:
        t0 = time.time()
        hb = t0 - last_commit > HEARTBEAT_MIN * 60
        d, changed = once(commit=a.commit, force_commit=hb)
        if changed or hb:
            last_commit = t0
        print(f"{d['lastChecked']} status={d['status']} rounds={len(d.get('rounds', []))} changed={changed}",
              flush=True)
        if d.get("status") == "final":
            print("final result; stopping")
            if a.commit:
                commit_push("final")
            return
        time.sleep(max(5, a.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
