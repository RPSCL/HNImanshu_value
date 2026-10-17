# PF Builder: prime members, shared price store, new rules

## 1. Make someone a prime member (one-time setup, then per person)
Firebase console -> Firestore Database:
1. Create a collection `hni_prime`. For each prime member add a document whose ID is the member's UID
   (Authentication -> Users -> copy the "User UID"). Any field is fine, e.g. `name: "Rahul"`.
2. Firestore rules: add this next to your `hni_admins` rule (keep your admin check exactly as you have it for hni_admins):

    match /hni_prime/{uid} {
      allow read:  if request.auth != null && (request.auth.uid == uid || <same admin test as hni_admins>);
      allow write: if request.auth != null && <same admin test as hni_admins>;
    }

   A member can read only their own document; only admins can add or remove members.
3. Remove a member by deleting their document.
Prime members see the PF Builder button on Fundamentals and can open the page and request detailed prices. Admins can too.
The Worker checks the same document, so hiding the button is not the only protection.

## 2. Many users, one folder (why nothing is lost now)
Market prices are the same for everybody, so there is ONE shared store (`etf_prices/`), not one folder per user.
- A run no longer deletes anything. It fetches only the asked stocks that are not already saved with fresh prices (plus the ETFs and NIFTY 500),
  and merges them into `daily_close.csv`. Other users' stocks stay.
- `requested.json` records when each stock was last asked for. Stocks nobody asked for in 60 days (or beyond 300 stocks, oldest first) are dropped.
  Change `KEEP_ASKED_DAYS` / `MAX_STOCKS` at the top of fetch_prices.py.
- Raw candle files are kept for the ETFs and NIFTY 500 only (old per-stock raw files are removed on the next run).
- Runs go one at a time. If someone asks while a run is going, the Worker says "another chart is being prepared, try again in a few minutes"
  and the page re-enables the button (the request is not queued, so nobody waits for prices that will never come).
- Each user's choices (split, lists, sort) live in their own browser, so they never touch each other.

## 3. Rules added
- Refresh chart is disabled ("Preparing...") while a request from this computer is pending, also after a reload and when the lists change.
  It frees itself when the prices arrive, the request fails, or after 45 minutes.
- No recent IPOs: a stock needs at least ceil(years back) whole years of prices before the date it is judged on
  (Today: 1 year; 1-year-back list: 1 year before that date). It uses the first price day in prices.csv (LISTED_FROM), now carried in the
  fundamentals data as `lf`. Prices are fetched from a fixed start (about 2.5 years back), so a stock first seen on that start day is treated
  as older, which is what it is. This applies to Value and Momentum, today and in the backtest. It needs one build (Actions -> Smart Build & Deploy, run mode -1)
  so the data carries `lf`; until then nothing is excluded.
- Number boxes: click in, then ArrowUp / ArrowDown move by 5 (ETF %, Value %, Top). Fund value moves by 50,000.
