# Splits and the hidden test set

`house_sim/eval/splits.py` implements section A of [EVAL_CONTRACTS.md](EVAL_CONTRACTS.md). Standard library only,
Python 3.9+, no network access. Nothing in this module prints, logs or uploads the key, the hidden seeds or the
private phrasings.

## The three splits

| split | seeds | perception | instruction |
|---|---|---|---|
| train | `train_seeds(cid, n, start=0)`: unlimited, in `[TRAIN_BASE, PRIVATE_BASE)` = `[1_000_000, 900_000_000)` | spec's own (caller may force) | public phrasings |
| dev | `DEV_SEEDS = (0, 1, 2, 41, 97)` (the verification seeds) + published `DEV_EXTRA = (3, 8, 13, 24, 55)` | spec's own (caller may force) | public phrasings |
| test | hidden: k per entry (default 10) in `[PRIVATE_BASE, 2**31 - 1)` | always `realistic` | held-out phrasings |

* **Train** seeds use a per-entry offset (the SHA-256 of the catalog id) and run consecutively from there, so every
  entry gets different houses and `seed % len(variants)` covers every variant. They stay distinct while
  `start + n <= 899_000_000`.
* **Dev** seeds are the same for every entry. `episodes("dev", ...)` uses `DEV_SEEDS`. `seeds_per_entry > 5` or the
  split name `"dev_extra"` adds `DEV_EXTRA`.
* **Test** candidates are `HMAC-SHA256(key, f"{catalog_id}:{i}")`. The first 8 bytes, as a big-endian integer modulo
  the size of the private range, are added to `PRIVATE_BASE`, for `i = 0, 1, 2, ...`. A collision is skipped.
  Candidates become hidden seeds only once they are verified (see below).

## The key

* It is read from env `HOUSE_AI_SPLIT_KEY` (hex, at least 16 bytes) first, then from
  `%USERPROFILE%\.house_ai\split_key` (`Path.home() / ".house_ai" / "split_key"`).
* To create it, run `python -m house_sim --init-split-key`. This writes `secrets.token_bytes(32)` as hex, with
  owner-only permissions where the OS supports them. It refuses to overwrite an existing key unless you pass
  `--force`, because replacing the key makes manifests built with the old key unusable.
* The CLI prints only the key's SHA-256 digest (`key_digest`). That digest is public: the commitment publishes it.
* If anything needs the key and none is present, `SplitKeyMissing` is raised. This covers test candidates, the test
  split, test seed tags and manifest builds. The error message says how to create a key.
* Private files are never written inside the repository: `init_key` and `save_manifest` refuse such paths. The
  repo's `.gitignore` and `MANIFEST.in` also exclude `split_key`, `test_manifest.json`, `test_phrasings.json`,
  `results/`, `.house_ai/` and `private_results/` directories, and `*__PRIVATE.*` files (the viewer's private pages
  are `<name>__PRIVATE.html`). The packaging audit (below) catches what these name rules cannot, such as a test
  replay copied into `reports/` under an ordinary name.

## Building the hidden test set

```
python -m house_sim --build-test-manifest [--k 10] [--workers N] [--only IDS/SECTIONS/FILES/DOMAINS ...]
                    [--file tasks/authored/X.json ...] [--specs verified|candidates] [--verify]
                    [--perception realistic] [--max-candidates M] [--merge] [--manifest PATH] [--commitment PATH]
```

1. **Spec selection.** The default, `--specs verified`, keeps only specs with *current* passing evidence: a record
   in `reports/verification.jsonl` with `passed`, `implementation_hash == house_sim.audit.fingerprint()` and a
   matching `spec_hash`. That evidence goes stale whenever code changes. In that case, pass `--verify` to verify the
   selected specs on `DEV_SEEDS` now, in each spec's own perception mode, with
   `house_sim.tasks.verify.verify_many`. `--specs candidates` takes every structurally valid authored spec; each
   hidden seed is still verified individually. Unverified specs are never treated as verified.
2. **Candidate verification.** For each entry, candidates are walked in order. Each one runs
   `house_sim.tasks.verify.run_trial(spec, seed, perception)` in the spec's own mode: `"realistic"` when the spec
   declares it, or for every entry with `--perception realistic`. A candidate is kept only if the trial passes.
   A failing candidate is skipped and recorded in `skipped` with `"reason": "verification_failed"` and the failing
   checks.
3. **Variant coverage.** When a spec has variants, every variant `seed % len(variants)` must appear. A candidate
   whose variant is already covered is passed over (`"reason": "variant_quota"`, not verified) when the remaining
   slots are needed for uncovered variants. If k is smaller than the number of variants, it is raised to that
   number (`k_requested` keeps the asked value). `k` must be at least 1; the CLI warns about entries without
   variants that end up with a single hidden seed (`weak_entries(manifest)`), since one seed gives a noisy score.
4. **Limits.** There are at most `--max-candidates` candidates per entry (default `3k + 10`). An entry is marked
   `"complete": false` with an `"error"` in either case below. The CLI then exits 1, and the test split never uses
   that entry.
   * A needed variant (or the whole entry) fails 8 times without a single pass.
   * The pool runs out.
5. **Determinism.** The verifier is deterministic, so the same key, specs and code always give the same seeds,
   variants, skipped candidates and phrasings. Only each entry's `nonce` (fresh random, see the commitment below)
   differs between builds. Parallel workers only process separate entries. Workers receive the derived candidate
   seeds, never the key.

### Held-out phrasings

The private paraphrase pool lives with the key in `%USERPROFILE%\.house_ai\test_phrasings.json`. It must be written
by a person. Paraphrases must never be generated with an external model.

```json
{"T-0242c820d56e7e-01": ["The office printer has run dry of paper; please top up its tray.", "..."],
 "T-dde0975c7a26b8-01": {"0": ["phrasings for variant 0"], "1": ["phrasings for variant 1"]}}
```

* **Plain lists** apply to every variant. If an entry's variants have *different* public instructions, give its
  phrasings per variant (`"0"`, `"1"`, ... or `"*"` for a default). Otherwise the build refuses the list.
* **Rotation.** Phrasings are assigned to the hidden seeds round-robin per variant. `phrasing_index` runs parallel
  to `seeds`.
* **Placeholders.** Phrasings may use the spec's `{placeholders}`. The build checks that they bind on every hidden
  seed.
* **No private phrasings.** If the file is absent, or has no entry for a spec, the entry records the spec's public
  phrasings with `"public_phrasings": true` (also set at manifest level). Its episodes get `phrasing: None`, so the
  instruction is the public phrasing that `runner.build` chose.

### Files written

**Private** `%USERPROFILE%\.house_ai\test_manifest.json`:

```
{"format": "house_ai.test_manifest/1", "key_digest", "created_utc", "public_phrasings",
 "entries": {catalog_id: {"seeds", "variants" (parallel; null without variants), "n_variants",
                          "phrasings", "phrasing_index", "public_phrasings", "skipped": [{"index", "seed",
                          "variant", "reason", "detail"}], "candidates_tried", "candidates_considered", "k",
                          "k_requested", "complete", "error"?, "perception", "spec_hash", "fingerprint",
                          "nonce"}}}
```

* `nonce` is 32 random bytes (hex, `secrets.token_hex`) that blind the entry's public hash. It is private, like
  the rest of the entry, and is revealed with it when the commitment is opened. `save_manifest` adds one (in place)
  to any entry that lacks it before hashing.

* `load_manifest()` returns the `entries` mapping, which is the contract shape. `load_manifest(with_meta=True)`
  returns the whole document.
* `--merge` rebuilds only the selected entries and keeps the others. The existing manifest must have been built
  with the same key.

**Public** `reports/test_commitment.json`:

```
{"format": "house_ai.test_commitment/1", "algorithm", "created_utc", "key_digest", "per_entry": {cid: sha256},
 "root", "k", "seeds_total", "incomplete", "public_phrasings", "blinded"}
```

* `per_entry[cid] = sha256(canonical({"catalog_id": cid, "entry": entry}) + "|" + key_digest)`, where
  `canonical = json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`, and `entry` includes its
  random `nonce`.
* **Why the nonce.** Apart from the seed, an entry is low-entropy and mostly public: the spec's public phrasings,
  small counters, the spec hash and the code fingerprint (which every `run.json` publishes). `key_digest` is public
  too. Without a secret blinding value, anyone could rebuild the entry for each candidate seed and hash the ~30-bit
  private range in a couple of minutes on a laptop. That is how a k=1 entry's hidden seed was recovered blind before
  nonces were added. With 256 random bits in the hashed entry this is infeasible. `"blinded": true` marks
  commitments written this way. `--verify-commitment` warns about manifest entries without a nonce (built before
  nonces existed); rebuild and republish those.
* The Merkle tree is built as follows:
  * leaves: `sha256(0x00 + utf8(cid) + 0x00 + per_entry_bytes)` in sorted catalog-id order;
  * nodes: `sha256(0x01 + left + right)`;
  * an unpaired node is promoted;
  * an empty set gives `sha256("")`.
* The file reveals no seed, phrasing or key, as long as every entry is blinded. It does reveal which entries are in
  the test set, as intended.
* `merkle_proof(per_entry, cid)` and `verify_merkle_proof(...)` let you reveal and check a single entry.
* **Sandboxes.** `commitment_file()` is `reports/test_commitment.json` unless the environment variable
  `HOUSE_AI_COMMITMENT_FILE` names another file. Everything that reads or writes the public commitment honours it:
  `save_manifest`, `load_commitment`, `commitment_root()` (which the harness records as `run.json`
  `commitment_root`), `--verify-commitment`, `--split-info` and the leaderboard's commitment check. Use it with a
  temporary `USERPROFILE`/`HOME` and a throwaway `HOUSE_AI_SPLIT_KEY` to rehearse the whole test-split flow without
  touching the published commitment or your real key:

  ```
  set USERPROFILE=%TEMP%\house_sandbox& set HOME=%TEMP%\house_sandbox
  set HOUSE_AI_COMMITMENT_FILE=%TEMP%\house_sandbox\test_commitment.json
  python -m house_sim --init-split-key                     (writes %TEMP%\house_sandbox\.house_ai\split_key)
  python -m house_sim --build-test-manifest --verify --only T-089c40fb507289-01 T-6af2ea41755bbe-01 --k 2
  python -m house_sim --verify-commitment
  python -m house_sim --run-agent mock --split test --ids T-089c40fb507289-01 T-6af2ea41755bbe-01 --verify-on-the-fly
  ```

### Checking the commitment

```
python -m house_sim --verify-commitment [--manifest PATH] [--commitment PATH]
```

1. It checks that the root matches the per-entry hashes. Anyone can run this check.
2. If the private manifest is present, it recomputes every entry hash and checks the key digest.
3. Without the key, it checks against the commitment's own `key_digest` (for a manifest revealed after an
   evaluation).

Exit codes: 0 means OK, 1 means a mismatch, which is reported per entry without seeds.

In Python, `verify_commitment(manifest, key, public)` returns a bool and `commitment_consistent(public)` checks the
root alone.

## Contamination guards

* `is_private_seed(seed)`: the seed is in the private range. You can always check this; no key is needed.
* `is_test_seed(cid, seed)`: the seed is in the private range, or the private manifest lists it under any entry (a
  seed fixes the house). A manifest that exists but cannot be read raises an error rather than allowing the seed.
* `guard_not_test(cid, seed)` raises `ValueError("test seed refused")`. `episodes()` calls it for every train and
  dev seed, including explicit `seeds=`, and `build_episode()` calls it for non-test episodes.
* `seed_tag(cid, seed, key=None)` returns `"dev:<seed>"`, `"train:<seed>"`, `"test:<12 hex>"`, or `"seed:<seed>"`
  for other public seeds.
  * The test tag is `HMAC-SHA256(key, "tag:<cid>:<seed>")[:12]`. A plain hash of a ~30-bit seed could be
    brute-forced back to the seed, so test tags need the key.
  * `resolve_test_tag(cid, tag)` maps a tag back to its seed when you have the key and the manifest. This is for
    re-scoring.
  * `safe_tag(tag)` replaces `:` so the tag works in Windows file names.

## Episodes

```python
episodes(split, specs, seeds_per_entry=None, seeds=None, perception=None, strict=True, key=None, manifest=None)
# -> [{"catalog_id", "split", "seed", "seed_tag", "phrasing", "perception"}]
```

* `specs`: spec dicts, `candidate_records()`/`verified_specs(records=True)` records, or an authored file's
  `{"specs": [...]}`.
* **train** gives `seeds_per_entry or 1` train seeds per entry. **dev** is described above. Explicit `seeds` override
  both. The perception is the spec's own (`"oracle"` by default) unless `perception=` forces one.
* **test** requires the key (otherwise `SplitKeyMissing`) and the manifest (otherwise `ManifestMissing`, a
  `FileNotFoundError`). The manifest must have been built with this key.
  * Perception is always `"realistic"`; forcing `"oracle"` raises `ValueError`.
  * `phrasing` is the held-out phrasing, or `None` when the entry uses public phrasings.
  * `strict=True` raises for specs that are missing from the manifest, incomplete there, or changed since their seeds
    were validated (spec hash). `strict=False` skips them.
  * `seeds_per_entry` truncates in manifest order.
  * Explicit `seeds` must be hidden seeds of that entry.
* `build_episode(spec, episode)` returns `(config, task, oracle)`. It is `runner.build(spec, seed)` with
  `task["instruction"]` replaced by the episode's phrasing, with placeholders bound. Run the episode with
  `HouseEnv(config, task, seed, perception=episode["perception"], trace=False, observe_on_step=True)`.

The harness must not write raw test seeds into results (use `seed_tag`). Note that `HouseEnv.save_replay` embeds the
seed, so replays of test episodes are private files: the harness keeps them in
`%USERPROFILE%\.house_ai\private_results\<run_id>\`, and the packaging audit flags one copied into any packaged
folder, whatever its name.

## Verified specs (for other components)

```python
verified_specs(root=None, verify=False, seeds=DEV_SEEDS, workers=None, only=None, records=False)
filter_verified(items, verify=False, seeds=DEV_SEEDS, workers=None)   # -> (kept, rejected)
candidate_records(root=None, only=None)                                # read-only; compile_specs writes files
```

* **Default.** Specs with current passing evidence: `passed`, `implementation_hash == fingerprint()` and a matching
  `spec_hash`.
* **`verify=True`.** Verifies the selected specs on the dev seeds now, in each spec's own perception mode. Nothing is
  written. Use this in tests and integration while domain code is changing.
* **`records=True`.** Adds `kind`, `section`, `domain`, `source_file`, `title`, `risk_level`, `fidelity`,
  `perception`, `spec_hash`, `evidence` (`"report"` or `"on_the_fly"`) and the verification record.
* **Specs authored twice.** `candidate_records()` reads the authored files with `house_sim.eval.common.load_authored`,
  which leaves out a catalog id that appears in more than one `tasks/authored/*.json` file (this happens for a while
  when an author moves or re-authors a spec) instead of failing for the whole catalog like
  `house_sim.compiler.load_authored`. Such an id is never selected, verified or put in the test set until the
  duplicate is resolved. The harness, leaderboard and viewer use the same loader; the dataset exporter reports such
  ids as `DUPLICATE` and skips them.

## Info and packaging audit

* `python -m house_sim --split-info [--json]` shows:
  * the ranges;
  * whether a key is present, its source and a prefix of its digest;
  * the manifest: number of entries, complete entries, total seeds, entries built with other code, and whether it
    was built with this key;
  * the private phrasing pool;
  * the commitment: its consistency and whether it matches the manifest;
  * the packaging audit.

  It never shows seeds, phrasings or the key, and exits 1 if the packaging audit finds a private file.
* `packaged_files(root)` emulates the sdist file list (setuptools defaults, setup.py's `package_data` read
  statically with `ast`, then the `MANIFEST.in` commands with setuptools' glob rules, where a whole `**` component
  matches any number of directories) and the wheel's extra resources. setup.py's build step copies `catalog/`,
  `tasks/`, `reports/` and `demos/` wholesale; the emulation ignores that step's own ignore patterns, so the audit
  stays conservative. You do not need setuptools installed, and setup.py is never executed.
* `packaging_audit(root)` returns `{"sdist": [...], "wheel": [...], "reasons": {path: reason}}`, the private files
  that would be packaged and why. `private_files_in_package(root)` returns the two lists only; both must be empty.
  A packaged file counts as private:
  * **by name**: `split_key`, `test_manifest.json` or `test_phrasings.json`; anything under `results/`, or under a
    `.house_ai/` or `private_results/` directory at any depth; any path component containing `__PRIVATE`;
  * **by content**, whatever the file is called:
    * it contains the split key (the env key or the key file, compared as hex, case-insensitively);
    * it is a copy of the hidden test manifest (`"format": "house_ai.test_manifest/1"`; not checked in `.md`/`.rst`
      docs, which describe the format);
    * it is a viewer page marked private: its `<title>` starts with `PRIVATE - ` or contains `PRIVATE - test split`,
      or its embedded `episode-data` has `meta.private`, split `"test"` or a `test:` seed tag. The banner text alone
      is in every page's template, hidden unless the page is private, so it does not count;
    * a `"seed"`/`"seeds"` field (also `"..._seed"`, single or double quotes) holds a private-range seed or a seed
      listed in the private manifest. This catches a test-split replay (`HouseEnv.save_replay` embeds the raw seed)
      or a harness record under any name.

  Code files (`.py`) are checked for the key only, since tests spell private-range numbers on purpose. A file that
  cannot be read is reported (fail closed). Reasons never contain a seed, a phrasing or the key.
  `--split-info` prints the lists as `packaging_private_files` and the reasons as `packaging_private_reasons`.
  Private phrasings pasted into another file, and archives, are not detected by content.

## Tests

* `python -m unittest tests/test_eval_splits.py` runs fully offline, in about 30 s.
  * It uses a temporary `HOME`/`USERPROFILE`, a throwaway env key and a redirected commitment path.
  * The real manifest build uses two example specs with k=2.
  * The selection walk is also tested with a fake verifier.
  * The real-`sdist` packaging test runs only when setuptools is installed; the MANIFEST emulation always runs.
  * The packaging tests plant private replays, private viewer pages, a renamed manifest copy, the key and
    `private_results/`/`.house_ai/` directories in a temporary tree, and check `.gitignore` against the same names.
