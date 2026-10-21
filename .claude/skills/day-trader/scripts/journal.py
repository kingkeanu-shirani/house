"""Trade journal stats. Usage: journal.py LOG.csv
CSV columns: date,symbol,side,qty,entry,exit,fees[,notes]. side is long or short."""
import csv
import sys
from collections import defaultdict


def main(path):
    pnls, by_day = [], defaultdict(float)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            qty, entry, exit_, fees = float(r["qty"]), float(r["entry"]), float(r["exit"]), float(r.get("fees") or 0)
            sign = 1 if r["side"].strip().lower() == "long" else -1
            pnl = sign * (exit_ - entry) * qty - fees
            pnls.append(pnl)
            by_day[r["date"]] += pnl
    if not pnls:
        print("No trades.")
        return 1
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    gross_l = -sum(losses)
    equity = peak = dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    print(f"Trades: {len(pnls)}  Net P&L: {sum(pnls):,.2f}")
    print(f"Win rate: {len(wins) / len(pnls):.1%}  Avg win: {avg_w:,.2f}  Avg loss: {avg_l:,.2f}")
    print(f"Expectancy/trade: {sum(pnls) / len(pnls):,.2f}  Profit factor: {sum(wins) / gross_l:.2f}" if gross_l else "Profit factor: n/a (no losses)")
    print(f"Max drawdown: {dd:,.2f}")
    print(f"Best day: {max(by_day.items(), key=lambda x: x[1])}  Worst day: {min(by_day.items(), key=lambda x: x[1])}")
    if len(pnls) < 30:
        print("Note: small sample; treat stats as noisy.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else print(__doc__) or 1)
