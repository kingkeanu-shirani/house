# Verification hardening: decisions on the open questions (VERIFY_HARDENING.md section 27)

Decided by the main session under the owner's standing instruction (free rein, quality first; 2026-10-22). Items
marked (owner) need the owner's explicit answer before they take effect.

1. Evidence reuse: yes. A commit whose replay leaves every reference, idle and observation digest unchanged keeps a
   spec's HARDENED evidence for up to 14 days; the rolling full run and its replay-miss audit bound the risk.
2. "Not needed" branch: doing the action anyway is a failure (B8 never/superset enforce it).
3. Harms of inaction: attributed as the ledger does. Exogenous harms do not fail a task by themselves; a spec whose
   entry is about preventing a harm says so explicitly (a `responses`/`never` condition), and then failing to prevent
   it fails that spec only.
4. Cost: accepted (about 5.3 CPU-minutes per short spec at the full level). The gate may use 6 worker processes on
   this machine.
5. Evidence gating for agents: yes. A correct but unfounded action, decision, report or statement fails.
6. Escapes and exemptions: accepted by an independent reviewer agent (a logged session that never appears in the
   spec's check log) for every category except `other`; the owner spot-checks 10 % and reviews every `other`.
7. Budget factor 3 (time: 3x active + 1.25x waits + survey) and resource factor 2: accepted; recalibrated from the
   LLM pilot once it has run.
8. HARDENED is required for DONE and for the dev and test splits from R4 on.
9. Settle 60 s and look-ahead 6 h where triggered: accepted.
10. (owner) The private stages need the split key. The gate machine is the owner's machine. The key and the
    private-verification store (`%USERPROFILE%\.house_ai\private_verify\`) are created only after telling the owner,
    so they can back up `%USERPROFILE%\.house_ai`. Until then the private stages are implemented and tested with
    throwaway keys and report "not run (no split key)" at the gate.
