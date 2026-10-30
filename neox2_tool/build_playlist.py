#!/usr/bin/env python3
"""Construit un M3U curate (France + Sport) depuis des sources publiques legales,
teste chaque flux, et classe OK / geo-restreint / a-tester / mort.
Parse robuste des #EXTINF (virgules dans les attributs) et conserve les
#EXTVLCOPT (user-agent / referrer) necessaires a certains flux."""
import re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


def split_extinf(rest):
    """Separe la section attributs du nom au 1er virgule HORS guillemets."""
    inq = False
    for i, ch in enumerate(rest):
        if ch == '"':
            inq = not inq
        elif ch == ',' and not inq:
            return rest[:i], rest[i + 1:]
    return rest, ""


def attr(attrs, key):
    m = re.search(rf'{key}="([^"]*)"', attrs)
    return m.group(1) if m else ""


def parse(path):
    """Renvoie [(attrs, name, url, opts)] ; opts = lignes #EXTVLCOPT/#EXTGRP."""
    out, cur_attrs, cur_name, opts = [], None, None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#EXTINF:"):
                cur_attrs, cur_name = split_extinf(line[len("#EXTINF:"):])
                opts = []
            elif line.startswith("#EXTVLCOPT") or line.startswith("#EXTGRP"):
                if cur_attrs is not None:
                    opts.append(line)
            elif line and not line.startswith("#") and cur_attrs is not None:
                out.append((cur_attrs, cur_name.strip(), line.strip(), opts))
                cur_attrs = cur_name = None
                opts = []
    return out


# --- Selection -------------------------------------------------------------
SPORT_KW = re.compile(r"l.?equipe|bein sports xtra|eurosport|sport en france|"
                      r"automoto|trace sport|edge sport|fast.?&.?fun|red ?bull|"
                      r"motorvision|fuel tv|ginx|dazn|olympic", re.I)


def select():
    chosen = {}  # url -> dict
    def add(group, attrs, name, url, opts, override=False):
        rec = {"group": group, "name": name, "logo": attr(attrs, "tvg-logo"),
               "tvgid": attr(attrs, "tvg-id"), "opts": opts}
        if override or url not in chosen:
            chosen[url] = rec
    for attrs, name, url, opts in parse("fr.m3u"):
        add("TV France", attrs, name, url, opts, override=True)
    for attrs, name, url, opts in parse("freetv.m3u8"):
        if attr(attrs, "group-title").lower().startswith("france"):
            add("TV France", attrs, name, url, opts)
    for attrs, name, url, opts in parse("sports.m3u"):
        tvgid = attr(attrs, "tvg-id")
        if tvgid.lower().endswith(".fr") or SPORT_KW.search(name):
            add("Sport", attrs, name, url, opts)
    for u in list(chosen):
        if "youtube.com" in u or "youtu.be" in u:
            del chosen[u]
    return chosen


# --- Test de chaque flux ---------------------------------------------------
def test(url):
    try:
        p = subprocess.run(
            ["curl", "-sL", "-m", "9", "-A", UA, "-o", "-", "-w", "\n@@@%{http_code}",
             "--max-filesize", "200000", url],
            capture_output=True, text=True, errors="replace", timeout=14)
        out = p.stdout
        code = out.rsplit("@@@", 1)[-1].strip() if "@@@" in out else "000"
        body = out.rsplit("@@@", 1)[0] if "@@@" in out else out
        if code == "200":
            if url.lower().split("?")[0].endswith(".m3u8") and "#EXTM3U" not in body:
                return "DEAD"
            return "OK"
        if code in ("403", "451"):
            return "GEO"
        return "DEAD"
    except Exception:
        return "DEAD"


NOTABLE = re.compile(r"france ?[2345]\b|franceinfo|france info|arte|tf1|\bm6\b|"
                     r"\bc8\b|cnews|bfm|tmc|\bw9\b|gulli|6ter|rmc|tfx|cstar|l.?equipe", re.I)


def extinf(group, rec):
    a = []
    if rec["tvgid"]:
        a.append(f'tvg-id="{rec["tvgid"]}"')
    if rec["logo"]:
        a.append(f'tvg-logo="{rec["logo"]}"')
    a.append(f'group-title="{group}"')
    lines = [f'#EXTINF:-1 {" ".join(a)},{rec["name"]}']
    lines += rec["opts"]
    return "\n".join(lines) + "\n"


def main():
    chosen = select()
    urls = list(chosen)
    print(f"Candidats (dedup, sans YouTube): {len(urls)}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=24) as ex:
        statuses = dict(zip(urls, ex.map(test, urls)))

    buckets = {"OK": [], "GEO": [], "RETRY": [], "DEAD": []}
    for url in urls:
        rec = chosen[url]
        st = statuses[url]
        if st == "DEAD" and NOTABLE.search(rec["name"]):
            st = "RETRY"
        buckets[st].append((url, rec))
    for k in buckets:
        buckets[k].sort(key=lambda e: (e[1]["group"], e[1]["name"].lower()))

    with open("NeoX2.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for url, rec in buckets["OK"]:
            f.write(extinf(rec["group"], rec)); f.write(url + "\n")
        for url, rec in buckets["GEO"]:
            f.write(extinf(f'{rec["group"]} (FR uniquement)', rec)); f.write(url + "\n")
        for url, rec in buckets["RETRY"]:
            f.write(extinf("A tester depuis la France", rec)); f.write(url + "\n")

    tv = sum(1 for u, r in buckets["OK"] if r["group"] == "TV France")
    sp = sum(1 for u, r in buckets["OK"] if r["group"] == "Sport")
    print(f"OK={len(buckets['OK'])} (TV France {tv} / Sport {sp})  "
          f"GEO={len(buckets['GEO'])}  RETRY={len(buckets['RETRY'])}  "
          f"DEAD={len(buckets['DEAD'])}", file=sys.stderr)
    f3 = [r["name"] for b in ("OK", "GEO", "RETRY") for u, r in buckets[b]
          if re.search("france ?3", r["name"], re.I)]
    print("France 3 presentes:", f3 or "AUCUNE", file=sys.stderr)


if __name__ == "__main__":
    main()
