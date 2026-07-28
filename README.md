# CASTLE Artifact

This repository contains the contracts, experiment drivers, data, and plotting
scripts for **CASTLE: Latency-Aware Containment of Cloud Failures in Shared-Collateral Cross-Chain Solvers**.

CASTLE studies a shared-collateral solver whose destination liquidity is spent
before source settlement completes. The artifact evaluates whether reliable
failure evidence reaches an order before its remaining slack expires and whether
soft quarantine bounds new exposure when evidence arrives late.

## Artifact scope

The artifact supports the four research questions in the paper:

| Question | Main evidence | Script |
|---|---|---|
| RQ1: evidence latency vs. deadline slack | Fig. 4(a)--(b) | `exp_code/rq1_preemption.py` |
| RQ2: timeout/local-pause baselines | Fig. 4(c)--(d) | `exp_code/rq2_baselines.py` |
| RQ3: staggered destination freezes | Fig. 4(e)--(f) | `exp_code/rq3_stagger.py` |
| RQ4: one-hop containment and pricing | Fig. 4(g)--(h) | `exp_code/rq4_nonamp_pricing.py` |
| Scaling and false-freeze probes | Fig. 5 | `exp_code/rq_scaling_falsefreeze.py` |

The repository includes the numeric results used for the paper figures. The live
testnet campaign requires testnet funds and may not reproduce the same latency
values because public RPC and testnet conditions change over time.

## Repository layout

```text
.
├── castle_framework/       Solidity contracts, deployment script, and tests
│   ├── src/                vault, hub, breaker, and shared types
│   ├── script/             Foundry deployment script
│   └── test/               unit and one-hop containment tests
├── exp_code/               experiment and plotting scripts
├── data/                   paper experiment data in JSON format
├── figure/                 generated PDF/PNG figures
├── .env.example            testnet configuration template
└── requirements.txt        Python dependencies (under `exp_code/`)
```

Foundry build files, transaction broadcasts, private configuration, and local
deployment addresses are intentionally excluded from version control.

## Requirements

- Python 3.10 or later
- Foundry (`forge`, `cast`, and `anvil`) for contract tests and RQ4 Anvil runs
- Testnet RPC endpoints and a fresh test-only account for live experiments

The plotting-only path does not require an RPC endpoint or private key.

## Quick start: reproduce figures from released data

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r exp_code/requirements.txt

# Recreate Fig. 4(a)--(h) from the released JSON files.
python exp_code/plot_from_data.py

# Recreate the local scaling data and Fig. 5.
python exp_code/rq_scaling_falsefreeze.py
```

Generated files are written to `figure/`. To copy them to a local paper tree,
set `CASTLE_PAPER_EXP` to that directory before running a plotting command.

## Contract tests

```bash
cd castle_framework
forge test -vv
```

The tests cover collateral accounting, admission and freeze behavior, and the
single-shock rerouting condition used by the one-hop result.

## Local experiments without testnets

The following commands do not require credentials:

```bash
# RQ1--RQ3 synthetic/dry-run paths
python exp_code/rq1_preemption.py --reps 30
python exp_code/rq2_baselines.py --reps 30
python exp_code/rq3_stagger.py --reps 30

# RQ4: Foundry/Anvil execution and Python cross-check
python exp_code/rq4_nonamp_pricing.py --anvil
python exp_code/rq4_nonamp_pricing.py --sim --reps 30

# Ablations: hard hub stop, missing deadline check, label errors, static cap
python exp_code/rq_midtier_b.py

# Scaling and false-freeze opportunity-cost probes
python exp_code/rq_scaling_falsefreeze.py

# Render Fig. 4 after regenerating data
python exp_code/plot_from_data.py
```

These commands overwrite the corresponding files in `data/`. Preserve the
released JSON files if you want to compare new runs with the paper results.

## Live testnet reproduction

Live execution uses Ethereum Sepolia as the hub/failure origin, BSC Testnet as a
destination, and Base Sepolia as the staggered-freeze destination. The proof
channel is an honest-threshold committee attestation used to isolate timing; it
is not a deployed light client.

1. Create a fresh test-only wallet and fund it with testnet tokens.
2. Copy the environment template and fill in the test-only values:

   ```bash
   cp .env.example .env
   ```

3. Deploy the hub and destination breakers:

   ```bash
   ./castle_framework/deploy.sh hub
   MODE=0 ./castle_framework/deploy.sh bsc   # B1: timeout-only
   MODE=1 ./castle_framework/deploy.sh bsc   # B2: local pause
   MODE=2 ./castle_framework/deploy.sh bsc   # B3: CASTLE
   ./castle_framework/deploy.sh base         # Base B3
   ```

   Deployment addresses are written locally to
   `castle_framework/deployments.json`. This file is ignored because public
   addresses may identify the deployer through funding transactions.

4. Run the paper campaign sizes:

   ```bash
   python exp_code/rq1_preemption.py --live --reps 10 --compact
   python exp_code/rq2_baselines.py --live --reps 20
   python exp_code/rq3_stagger.py --live --reps 20
   python exp_code/rq_n20_workload.py --live --reps 10
   python exp_code/measure_gas.py
   python exp_code/rq4_nonamp_pricing.py --anvil
   ```

5. Render the figures:

   ```bash
   python exp_code/plot_from_data.py
   python exp_code/rq_scaling_falsefreeze.py
   ```

## Output and pass criteria

Each RQ script writes a JSON file under `data/` and prints its main checks. The
default writer removes generation times, testnet addresses, and transaction
hashes. Set `CASTLE_INCLUDE_RUN_METADATA=1` only for private debugging. The
paper uses the following interpretation:

- **RQ1:** every intent with `L < S` is preempted before its deadline; late
  exposure remains within the configured degradation bound.
- **RQ2:** B3 lowers induced shortfall and admissions during the evidence window
  relative to B1 and B2. Similar unfreeze time indicates containment rather than
  faster recovery.
- **RQ3:** global soft quarantine limits admissions on a destination that has not
  yet completed its hard freeze.
- **RQ4:** correct correlation labels produce no second-hop shortfall in the
  single-shock experiment, and race-coupled pricing reduces concurrency as late
  evidence becomes more common.

## Limitations

- Testnet latency and finality are not production-tail measurements.
- The committee proof path assumes an honest threshold.
- RQ4 uses Foundry/Anvil plus a Python cross-check; the scaling, false-freeze,
  label-error, and static-cap probes are local rather than live multi-region RPC
  campaigns.
- Results depend on the released workload, confirmation policy, and admission
  budget. New RPC providers or testnet conditions may change absolute latency.

## Anonymous-use and secret-handling checklist

Before publishing a fork or new run:

1. Never commit `.env`, private keys, seed phrases, provider tokens, or keystores.
2. Do not commit `castle_framework/broadcast/`; it records transaction and
   deployer metadata.
3. Do not commit `castle_framework/deployments*.json` unless the deployment used
   a dedicated anonymous wallet.
4. Check staged files with `git status` and run a secret scanner before pushing.
5. Treat all keys used by this artifact as test-only, even if they were never
   committed.
