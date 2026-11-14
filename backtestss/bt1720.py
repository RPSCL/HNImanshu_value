"""Rough backtest, Jan 2017 - Feb 2020, of the HNImanshu PF Builder rules (30% Index ETFs / 50% Value / 20% Momentum),
from eod2_data (split / bonus adjusted NSE daily prices; only stocks still listed today -> survivorship bias).
Momentum: re-pick every week, the site's score (1M/3M/6M/12-1M percentiles + stair), screen = momentum >= 70, circuit days <= 5,
non-PSU, smallest 20 first (size = median traded value, no historical market cap), hold: buffer 10% (momentum >= 63) and rank
within top 40, never sell momentum >= 70, never sell a winner unless 25% below its high.
Index ETFs: 7 equal slots, JUNIORBEES, MON100, HNGSNGBEES, FMCG, ALPHA always + 2 pair slots (IT vs Gold, Alpha vs Gold;
composite 0.5*6M + 0.5*12M; challenger needs 0.8 x its momentum to beat Gold), checked weekly. Index stand-ins where an ETF did not exist.
Run (repo root):  git clone --depth 1 https://github.com/BennyThadikaran/eod2_data.git
                  python backtests/bt1720.py            (or the "Backtest 2017-2020" workflow on GitHub: Actions -> Run workflow)
Value: no fundamentals for 2017 -> stand-in = Nifty Smallcap 250 (+1.5% a year dividends). Charges 0.15% a side (stocks),
0.05% (ETFs); 20% tax on each calendar year's net realised gain."""
import os, json, sys, pathlib
import numpy as np, pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent                 # the repo
D = os.environ.get('EOD2_DIR', str(ROOT / 'eod2_data' / 'daily'))
OUT = pathlib.Path(os.environ.get('BT_OUT', str(ROOT / 'backtests')))
sys.path.insert(0, str(ROOT))
START, END, END2 = '2017-01-06', '2020-02-28', '2020-03-27'
LOAD_FROM = '2015-06-01'

eq = pd.read_csv(ROOT / 'EQUITY_L.csv')
eq.columns = [c.strip() for c in eq.columns]
syms = sorted(set(eq.loc[eq['SERIES'].str.strip().isin(['EQ', 'BE']), 'SYMBOL'].str.strip()))
try:                                               # the site's PSU list (fundamentals.PSU): left out of Momentum, as on the site
    import fundamentals
    PSU = set(fundamentals.PSU)
except Exception as e:
    print('PSU list not loaded (' + str(e) + '): PSU stocks are not left out', file=sys.stderr); PSU = set()

cl, op, hi, lo, vol, first = {}, {}, {}, {}, {}, {}
for s in syms:
    f = os.path.join(D, s.lower() + '.csv')
    if not os.path.exists(f):
        continue
    df = pd.read_csv(f, usecols=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'], parse_dates=['Date'])
    if df.empty:
        continue
    first[s] = df['Date'].iloc[0]
    df = df[(df['Date'] >= LOAD_FROM) & (df['Date'] <= '2020-04-30')].set_index('Date')
    if len(df) < 60:
        continue
    cl[s], op[s], hi[s], lo[s], vol[s] = df['Close'], df['Open'], df['High'], df['Low'], df['Volume']
C = pd.DataFrame(cl); O = pd.DataFrame(op); H = pd.DataFrame(hi); L = pd.DataFrame(lo); V = pd.DataFrame(vol)
print('stocks loaded', C.shape, file=sys.stderr)

turn = (C * V).rolling(126, min_periods=60).median()
locked = ((O > 0) & (O == H) & (H == L) & (L == C)).astype(float)
circ = locked.rolling(250, min_periods=20).sum()

wk = C.resample('W-FRI').last()                     # weekly closes (last session of the week)
wdates = [d for d in wk.index if d >= pd.Timestamp('2016-06-01')]
lastday = C.index.to_series().resample('W-FRI').last()   # the real last session of each week
turnW = turn.reindex(lastday.values).set_axis(lastday.index); circW = circ.reindex(lastday.values).set_axis(lastday.index)

def pct_rank(x):                                   # the site's pctl: (below + (equal-1)/2) / (n-1)
    v = x.dropna()
    if len(v) < 2:
        return pd.Series(0.5, index=v.index)
    r = v.rank(method='average') - 1
    return r / (len(v) - 1)

def mom_at(d):
    i = wk.index.get_loc(d)
    w = wk.iloc
    c0 = w[i]; c4 = w[i - 4]; c13 = w[i - 13]; c26 = w[i - 26]; c52 = w[i - 52]
    r1 = (c0 / c4 - 1) * 100; r3 = (c0 / c13 - 1) * 100; r6 = (c0 / c26 - 1) * 100; r121 = (c4 / c52 - 1) * 100
    age_ok = pd.Series({s: first.get(s, d) <= d - pd.Timedelta(days=365) for s in wk.columns})
    t = turnW.loc[d]
    ok = age_ok & c0.notna() & (c0 > 0) & t.notna() & c52.notna()
    uni = t[ok].sort_values(ascending=False).index[:1250]            # "All lists" stand-in: the 1,250 most traded
    cols = [pct_rank(r1[uni]), pct_rank(r3[uni]), pct_rank(r6[uni]), pct_rank(r121[uni])]
    p = pd.concat(cols, axis=1)
    n = p.notna().sum(axis=1)
    sc = p.mean(axis=1) * 100
    r12 = ((1 + r1 / 100) * (1 + r121 / 100) - 1) * 100
    stair = ((r1 > 0) & (r1 > r3)).astype(int) + ((r3 > 0) & (r3 > r6)).astype(int) + ((r6 > 0) & (r6 > r12)).astype(int)
    stair = stair.where(r1.notna() & r3.notna() & r6.notna() & r121.notna(), 0)
    momS = (sc + 10 * stair[uni] / 3).clip(upper=100).where(n >= 2)
    return uni, momS, circW.loc[d].reindex(uni), t.reindex(uni), c0

CH, CH_ETF, TAX = 0.0015, 0.0005, 0.20

class Book:                                         # one part's money: positions, cash, realised gains this year, tax paid
    def __init__(s, F):
        s.cash, s.pos, s.gain, s.taxpaid, s.trades = F, {}, 0.0, 0.0, 0
    def value(s, px):
        return s.cash + sum(p['u'] * (px.get(k, np.nan) if pd.notna(px.get(k, np.nan)) else p['last']) for k, p in s.pos.items())
    def sell(s, k, q, ch):
        p = s.pos.pop(k); v = p['u'] * q; c = v * ch
        s.cash += v - c; s.gain += v - c - p['cost']; s.trades += 1
    def buy(s, k, q, amt, ch):
        c = amt * ch; s.pos[k] = {'u': (amt - c) / q, 'cost': amt, 'peak': q, 'last': q}; s.cash -= amt; s.trades += 1
    def year_end(s):
        t = TAX * max(0.0, s.gain); s.cash -= t; s.taxpaid += t; s.gain = 0.0

# ---------- Momentum part ----------
def run_mom(F, dates):
    B = Book(F); curve = []
    for i, d in enumerate(dates):
        uni, momS, cz, t, px = mom_at(d)
        for k, p in B.pos.items():
            q = px.get(k)
            if pd.notna(q) and q > 0:
                p['last'] = q; p['peak'] = max(p['peak'], q)
        nonpsu = [s for s in uni if s not in PSU]
        size = t.reindex(nonpsu)
        strict = [s for s in size.sort_values().index if momS.get(s, -1) >= 70 and (cz.get(s) if pd.notna(cz.get(s)) else 0) <= 5]
        top = strict[:20]
        buf = [s for s in size.sort_values().index if momS.get(s, -1) >= 63 and (cz.get(s) if pd.notna(cz.get(s)) else 0) <= 5]
        keep = set(buf[:40])
        if i == 0:
            amt = B.cash / len(top)
            for s in top:
                B.buy(s, px[s], amt, CH)
        else:
            sold = 0
            for k in list(B.pos):
                q = px.get(k)
                if not (pd.notna(q) and q > 0) or k in top or k in keep:
                    continue
                p = B.pos[k]; cpu = p['cost'] / p['u']
                if momS.get(k, -1) >= 70:
                    continue                                   # never sell momentum >= 70
                if q > cpu and q > 0.75 * p['peak']:
                    continue                                   # a winner, not 25% off its high
                B.sell(k, q, CH); sold += 1
            new = [s for s in top if s not in B.pos][:sold]
            if new and B.cash > 0:
                amt = B.cash / len(new)
                for s in new:
                    B.buy(s, px[s], amt, CH)
        if i + 1 < len(dates) and dates[i + 1].year != d.year:
            B.year_end()
        curve.append((d, B.value(px), len(B.pos)))
    return B, curve

# ---------- Index ETF part ----------
def series(name):
    df = pd.read_csv(os.path.join(D, name + '.csv'), usecols=['Date', 'Close'], parse_dates=['Date']).set_index('Date')['Close']
    return df.resample('W-FRI').last()
E = {'JUNIORBEES': series('juniorbees'), 'MON100': series('mon100'), 'HNGSNGBEES': series('hngsngbees'),
     'FMCG': series('nifty fmcg'), 'ALPHA': series('nifty alpha 50'), 'IT': series('nifty it'), 'GOLDBEES': series('goldbees')}
def comp(name, d):
    s = E[name].loc[:d].dropna()
    return 0.5 * (s.iloc[-1] / s.iloc[-27] - 1) * 100 + 0.5 * (s.iloc[-1] / s.iloc[-53] - 1) * 100

def run_etf(F, dates):
    B = Book(F); curve = []; slots = ['JUNIORBEES', 'MON100', 'HNGSNGBEES', 'FMCG', 'ALPHA', 'P1', 'P2']
    pair = {'P1': 'IT', 'P2': 'ALPHA'}; hold = {}
    for i, d in enumerate(dates):
        px = {k: E[k].loc[:d].dropna().iloc[-1] for k in E}
        want = {}
        for sl, c in pair.items():
            want[sl] = c if 0.8 * comp(c, d) > comp('GOLDBEES', d) else 'GOLDBEES'
        if i == 0:
            amt = B.cash / len(slots)
            for sl in slots:
                k = want.get(sl, sl); hold[sl] = k; B.buy(sl, px[k], amt, CH_ETF)
        else:
            for sl in pair:
                if want[sl] != hold[sl]:
                    q = px[hold[sl]]; B.sell(sl, q, CH_ETF); k = want[sl]; hold[sl] = k
                    B.buy(sl, px[k], B.cash, CH_ETF)
        for sl in slots:
            B.pos[sl]['last'] = px[hold[sl]]
        if i + 1 < len(dates) and dates[i + 1].year != d.year:
            B.year_end()
        curve.append((d, B.value({sl: px[hold[sl]] for sl in slots}), dict(hold)))
    return B, curve

# ---------- Value stand-in ----------
def run_val(F, dates, name='nifty smallcap 250', dy=0.015):
    s = series(name)
    p0 = s.loc[:dates[0]].dropna().iloc[-1]
    return [(d, F * s.loc[:d].dropna().iloc[-1] / p0 * (1 + dy) ** ((d - dates[0]).days / 365.25)) for d in dates]

def stats(v, dates):
    v = np.array(v); peak = np.maximum.accumulate(v); dd = (v / peak - 1).min() * 100
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = ((v[-1] / v[0]) ** (1 / yrs) - 1) * 100
    by = {}
    s = pd.Series(v, index=dates)
    prev = v[0]
    for y in sorted(set(d.year for d in dates)):
        last = s[s.index.year == y].iloc[-1]; by[y] = (last / prev - 1) * 100; prev = last
    return cagr, dd, by

md = ['# Backtest Jan 2017 - Feb 2020 (rough)', '', 'Momentum + Index ETFs from real prices with your 1Y adj rules; Value = Smallcap 250 stand-in (no 2017 fundamentals).', '']
for end in (END, END2):
    dates = [d for d in wk.index if pd.Timestamp(START) <= d <= pd.Timestamp(end)]
    F = 1_00_00_000
    BM, cm = run_mom(F * 0.20, dates)
    BE, ce = run_etf(F * 0.30, dates)
    cv = run_val(F * 0.50, dates)
    cv2 = run_val(F * 0.50, dates, 'nifty 500', 0.012)
    tot = [a[1] + b[1] + c[1] for a, b, c in zip(cm, ce, cv)]
    tot2 = [a[1] + b[1] + c[1] for a, b, c in zip(cm, ce, cv2)]
    n50 = series('nifty 50'); sc = series('nifty smallcap 250')
    bn = [n50.loc[:d].dropna().iloc[-1] for d in dates]; bs = [sc.loc[:d].dropna().iloc[-1] for d in dates]
    print(f'\n===== {dates[0].date()} -> {dates[-1].date()} =====')
    yrs = sorted(set(d.year for d in dates))
    md += [f'## {dates[0].date()} to {dates[-1].date()}', '', '| | CAGR | Max drawdown | ' + ' | '.join(map(str, yrs)) + ' |', '|---|---|---|' + '---|' * len(yrs)]
    for nm, v in [('PORTFOLIO (Value = Smallcap 250)', tot), ('PORTFOLIO (Value = Nifty 500)', tot2), ('Momentum part', [x[1] for x in cm]),
                  ('Index ETF part', [x[1] for x in ce]), ('Value stand-in (SC250)', [x[1] for x in cv]), ('Nifty 50 (price)', bn), ('Smallcap 250 (price)', bs)]:
        cg, dd, by = stats(v, dates)
        print(f'{nm:34s} CAGR {cg:6.1f}%  maxDD {dd:6.1f}%  ' + '  '.join(f'{y}: {r:+6.1f}%' for y, r in by.items()))
        md.append(f'| {nm} | {cg:+.1f}% | {dd:.1f}% | ' + ' | '.join(f'{by[y]:+.1f}%' for y in yrs) + ' |')
    print(f'momentum trades {BM.trades}, tax paid {BM.taxpaid:,.0f}; ETF trades {BE.trades}; holdings at end {cm[-1][2]}; ETF pairs at end {ce[-1][2]["P1"]}, {ce[-1][2]["P2"]}')
    md += ['', f'Momentum trades {BM.trades}, tax paid Rs {BM.taxpaid:,.0f} (fund Rs 1 crore)', '']
    if end == END:
        OUT.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({'date': dates, 'total': tot, 'mom': [x[1] for x in cm], 'etf': [x[1] for x in ce], 'val': [x[1] for x in cv],
                      'nifty50': bn}).to_csv(OUT / 'bt1720_curve.csv', index=False)
(OUT / 'bt1720_result.md').write_text('\n'.join(md))
if os.environ.get('GITHUB_STEP_SUMMARY'):                      # GitHub: the table on the run's page
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write('\n'.join(md) + '\n')
