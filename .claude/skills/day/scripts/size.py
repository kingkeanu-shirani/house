"""Position sizing and reward:risk. Usage: size.py ACCOUNT RISK_PCT ENTRY STOP TARGET [FEES_PER_SHARE]"""
import math
import sys


def main(argv):
    if len(argv) < 6:
        print(__doc__)
        return 1
    account, risk_pct, entry, stop, target = map(float, argv[1:6])
    fees = float(argv[6]) if len(argv) > 6 else 0.0
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        print("Entry and stop must differ.")
        return 1
    max_risk = account * risk_pct / 100
    shares = math.floor(max_risk / (risk_per_share + fees))
    reward = abs(target - entry) - fees
    rr = reward / (risk_per_share + fees)
    print(f"Max risk:        ${max_risk:,.2f}")
    print(f"Risk per share:  ${risk_per_share:,.4f}")
    print(f"Shares:          {shares}  (notional ${shares * entry:,.2f})")
    print(f"Net reward:risk: {rr:.2f}" + ("  <-- below 2:1" if rr < 2 else ""))
    print(f"If stopped: -${shares * (risk_per_share + fees):,.2f}   If target: +${shares * reward:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
