"""
news_dedup.py — spot the same story told twice, for one stock ("₹59 crore" = "Rs 59 cr", "22%" = "22 per cent").
Used by fetch_news.py (keeps multi_stock_news.csv clean) and build.py (the news file the website loads).

How two headlines of the same stock are compared:
  1. The " - Source" tail is dropped, money / units are normalised (₹ / Rs / INR, crore / cr, per cent / %),
     digit groups joined (12,000 → 12000), plurals folded (profits → profit).
  2. Words that appear in most of that stock's headlines (its own name, "share", "price"…) are ignored,
     so two stories are not called the same just because both say "Tata Consultancy Services".
  3. Key figures = the numbers in the headline, leaving out years (2024–2030) and quarter / FY labels.
  Same story when:
     • they share 2+ key figures and some wording (≥ 20% of words), or
     • they share 1 key figure and ≥ 40% of the wording, or
     • no figures needed: ≥ 60% of the wording is the same.
  The newest headline of each story is kept.
"""
import re
from collections import Counter

STOP = {"the", "a", "an", "in", "of", "to", "for", "and", "or", "is", "are", "on", "at", "with", "by", "from", "its", "as",
        "this", "that", "was", "be", "has", "have", "had", "it", "s", "co", "ltd", "limited", "inc", "after", "over", "into",
        "up", "down", "via", "amid", "says", "said", "report", "reports", "news", "today", "latest", "live", "update", "updates"}
UNIT = [(r"₹|\brs\.?\b|\binr\b", " rs "), (r"\bcrores?\b|\bcr\.?\b", " cr "), (r"\blakhs?\b|\blacs?\b", " lakh "),
        (r"\bper\s*cent\b|\bpercent\b|\bpct\b|%", " pct "), (r"\bbillion\b|\bbn\b", " bn "), (r"\bmillion\b|\bmn\b", " mn ")]


def source_of(title):
    """'Headline - Reuters' → ('Headline', 'Reuters'); no dash → (title, '')."""
    t = str(title).strip()
    if " - " in t:
        head, src = t.rsplit(" - ", 1)
        if 0 < len(src) <= 40:
            return head.strip(), src.strip()
    return t, ""


def _tokens(title):
    t = source_of(title)[0].lower()
    for pat, rep in UNIT:
        t = re.sub(pat, rep, t)
    while re.search(r"(\d),(\d)", t):
        t = re.sub(r"(\d),(\d)", r"\1\2", t)
    t = re.sub(r"\b(q[1-4]|h[12]|fy\s?\d{2,4})\b", " ", t)            # quarter / FY labels are not key figures
    nums = {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", t)}
    nums = {n for n in nums if not re.fullmatch(r"20[2-3]\d", n) and n not in ("", "0")}
    words = {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[a-z]+", t) if w not in STOP and len(w) > 1}
    return words, nums


def _same(a, b):
    (wa, na), (wb, nb) = a, b
    j = len(wa & wb) / max(1, len(wa | wb))
    shared = len(na & nb)
    return (shared >= 2 and j >= 0.20) or (shared >= 1 and j >= 0.40) or j >= 0.60


def keep_mask(titles):
    """titles of ONE stock, newest first → list of True (keep) / False (repeat of a newer headline)."""
    toks = [_tokens(t) for t in titles]
    if len(toks) >= 4:                                                   # this stock's own common words don't count
        df = Counter(w for ws, _ in toks for w in ws)
        common = {w for w, c in df.items() if c >= 0.6 * len(toks)}
        toks = [(ws - common, ns) for ws, ns in toks]
    kept, out = [], []
    for tk in toks:
        dup = any(_same(tk, k) for k in kept)
        out.append(not dup)
        if not dup:
            kept.append(tk)
    return out


def dedup_frame(df, sym_col="stockname", title_col="news", time_col=None):
    """pandas DataFrame → same frame without repeated stories (per stock, newest kept). Sorts newest first if time_col."""
    if df.empty:
        return df
    if time_col:
        df = df.sort_values(time_col, ascending=False)
    keep = []
    for _, g in df.groupby(sym_col, sort=False):
        keep += [i for i, k in zip(g.index, keep_mask(list(g[title_col]))) if k]
    return df[df.index.isin(set(keep))]
