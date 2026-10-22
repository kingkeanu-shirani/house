#!/usr/bin/env bash
# Re-run the four offline baselines on the 11-entry dev subset (55 episodes) and rebuild reports/leaderboard.*
# with in-process verification. A throwaway verifier key comes from the environment so nothing is written to
# ~/.house_ai (the user's rule: never create files there silently). No network, no LLM.
set -u
# the project root, wherever the checkout lives (this script is tools/orchestration/regen_leaderboard.sh)
cd "$(dirname "$0")/../.." || exit 1
export HOUSE_AI_VERIFIER_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
unset HOUSE_AI_SPLIT_KEY
IDS="T-5fccf30a680f1b-01 T-dd0bf4a1707041-01 T-089c40fb507289-01 T-6af2ea41755bbe-01 T-d8355138bcf9fb-01 A-8910a09f802fbf-01 X-d734ea3e299b16-01 X-fee35ef3aac659-01 T-0e1edbe2e88e09-01 T-d25af2202bc025-01 T-a853f28cce9c86-01"
for a in random search oracle mock; do
  rm -rf "results/base-$a-dev"
  python -m house_sim --run-agent $a --split dev --ids $IDS --limit 55 --workers 4 --out "results/base-$a-dev" \
    --run-id "base-$a-dev" --verify-on-the-fly --verify-workers 4 --quiet || echo "RUN FAILED: $a"
done
python -m house_sim --leaderboard results/base-random-dev results/base-search-dev results/base-oracle-dev results/base-mock-dev \
  --verify --out reports --workers 4 || echo "LEADERBOARD FAILED"
echo "absolute home paths in reports/leaderboard.json: $(grep -c "Users/" reports/leaderboard.json)"
ls "$USERPROFILE/.house_ai" 2>&1 | head -1
