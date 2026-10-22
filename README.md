# CASTLE Artifact

This repository contains the contracts, experiment drivers, data, and plotting
scripts for **CASTLE: Latency-Aware Containment of Cloud Failures in
Shared-Collateral Cross-Chain Solvers**.

CASTLE studies a shared-collateral solver whose destination liquidity is spent
before source settlement completes. The artifact evaluates whether reliable
failure evidence reaches an order before its remaining slack expires and whether
soft quarantine bounds new exposure after it takes effect at the shared hub.
Admissions between fault occurrence and hub activation are a separate term in
the degradation bound. The prototype does not enforce an always-active rate cap.

Evidence generation is a replaceable trust layer. A BFT committee, optimistic
verifier, light client, or restaked AVS determines whether evidence is sound and
contributes to finalization/proof delay. CASTLE starts from sound evidence and
tests the separate containment question: whether it arrives before each
deadline and what exposure remains when it does not.

### Prototype implementation

Destination breakers verify signatures from a configured committee signing
address. The prototype hub uses an owner-authorized controller and trusts its
failure inputs to satisfy the paper's finalized-evidence assumption.
`HubController.handleFailure` retains an unused signature parameter; its access
check is owner authorization, not independent attestation verification. Slashing
updates the hub vault's collateral accounting. Contracts enforce admission
budgets and recipient solvency and AZ-label checks; deadline checks and recipient
selection run off-chain. The concurrency fee is analyzed and evaluated as a
model, separately from the contract implementation. The contracts store explicit
intent records, without a proof-root or incremental-claim interface.

## Artifact scope

The artifact supports the four research questions in the paper:

| Question | Main evidence | Script |
|---|---|---|
| RQ1: evidence latency vs. deadline slack | Fig. 4(a)--(b) | `exp_code/rq1_preemption.py` |
| RQ2: timeout/local-pause baselines | Fig. 4(c)--(d) | `exp_code/rq2_baselines.py` |
| RQ3: staggered destination freezes | Fig. 4(e)--(f) | `exp_code/rq3_stagger.py` |
| RQ4: one-hop containment and pricing | Fig. 4(g)--(h) | `exp_code/rq4_nonamp_pricing.py` |
| Normal-path cost, matched-goodput B3$-$, and scaling | Fig. 5 | `exp_code/rq_overhead_scaling.py` |

The repository includes the numeric results used for the paper figures. The live
testnet campaign requires testnet funds and may not reproduce the same latency
values because public RPC and testnet conditions change over time.

Figure 4 keeps paired evidence for RQ1--RQ4: each pair separates the main safety
result from its coverage, recovery, ablation, or pricing check. Figure 5 remains
separate because it measures implementation overhead, the matched-goodput
B3-minus delay sweep, and local scaling rather than another live-testnet RQ.

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
The released live campaigns use 10--20 repetitions because public-testnet RPC
quotas, confirmation time, and test-token availability constrain repeated runs.
The local overhead campaign uses 100 transactions per mode, and scaling executes
up to 1000 admissions on a private Anvil node.

## Quick start: reproduce figures from released data

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r exp_code/requirements.txt

# Recreate Fig. 4(a)--(h) from the released JSON files.
python exp_code/plot_from_data.py

# Recreate Fig. 5 from its released JSON (no node required).
python exp_code/rq_overhead_scaling.py --plot-only
```

Generated files are written to `figure/`. To copy them to a local paper tree,
set `CASTLE_PAPER_EXP` to that directory before running a plotting command.
Figure 4(e) uses mean event offsets from the released RQ3 results; the paper's
text reports the corresponding medians. Newly exported PDF plots embed TrueType
fonts. Existing manuscript images are preserved in this repository update.

## Contract tests

```bash
cd castle_framework
forge test -vv
```

The tests cover collateral accounting, admission and freeze behavior, and the
single-shock rerouting condition used by the one-hop result.
`QuarantineBudget.t.sol` also checks that admissions before activation are not
counted against the episode budget, that destinations share that budget, and
that elapsed time does not replenish it. A haircut example checks that reserves
and the budget count the same supplied risk-adjusted amount.

The current vault accepts reroutes only when both AZ labels are known and
different and the recipient has sufficient free collateral. Label `0` means
unknown and is rejected. In the RQ4 error-injection runs, a truly correlated
recipient is assigned the incorrect nonzero label `3` while the failed LP has
label `1`. This models an incorrect classification, separately from missing
metadata. Ground-truth correlation remains unchanged.

The pricing example uses `eta = 1`, so the selected concurrency is bounded by
collateral. The unconstrained response and the capped choice are recorded
separately. With late-race fractions 0.30 and 0.70, the switch probabilities are
0.03125 and 0.030303, respectively. Figure 4(h) plots the capped choice.

To check these conditions and regenerate the local RQ4 results and panels:

```bash
python -m unittest discover -s exp_code -p test_rq4_policy.py -v
python exp_code/rq4_nonamp_pricing.py --anvil
python exp_code/plot_from_data.py --only g,h
```

These local checks use the revised vault. The released public-testnet traces
retain their original measurements and deployment provenance. The reported
pause/attestation gas probe measures destination-breaker calls, whose code is
unchanged by the hub rerouting check.

## Camera-ready checks and same-budget comparison

The following command uses only the Python standard library and writes a new
file, `data/rq_camera_ready.json`. It preserves the released measurements:

```bash
python3 exp_code/rq_camera_ready.py
```

The supplementary replay compares B2+ with an absolute-budget policy and B3.
The latter two have the same 20-unit budget and differ only in deadline
filtering. Each of 30 seeded runs offers 1000 equal-notional requests, uniformly
spaced over a configured post-activation interval of 20, 60, 120, or 240 seconds.
Slack is sampled uniformly from 30, 60, 120, and 300 seconds. B3 uses the configured
freeze-delay bound plus a 5-second margin. The model releases admitted work
at the last freeze, allows one second for execution, and assumes sufficient
recipient capacity. All modes admit the same offered stream with one-second
execution when fault controls are disabled.

The output includes admitted exposure, modeled on-time completions, modeled
useful goodput, total rejection count, and rejected requests that could have
finished on time. It isolates policy behavior under the stated assumptions;
it does not measure contract execution, RPC latency, or real service throughput.
At 240 seconds, both budget policies admit 20 units. Mean modeled on-time
completions are 4.93 (95% CI half-width 0.58) without filtering and 20 with B3;
B2+ admits none. Both budget policies reject 980 of 1000 offers.

The script also sweeps the conditional label false-negative probability from
0 to 1. Half of 1000 equal-notional candidates are truly correlated, safe
candidates pass, and correlated candidates pass with probability epsilon. The
population share of correlated routed mass is epsilon/(1+epsilon), which is
different from the conditional false-negative probability. A common secondary
shock occurs with probability 0.4 and loses the leaked mass. These are sensitivity
assumptions, not estimates of production label accuracy.

The timing reanalysis uses existing RQ1 and RQ3 logs. RQ3 records separate first
freeze, hub activation, and last freeze offsets. The hub offset uses host time
after receipt; clocks were not synchronized. RQ1 includes setup/admission time
and injected delays. The RQ1 budget probe ran separately from its timing test;
RQ3 submits its admission probe after hub activation. Neither measures admission
between the fault and hub activation, nor fault-specific latency components.

### Budget semantics

Exposure, episode budgets, and rate bounds in the analysis use risk-adjusted
collateral units, w_i = h_i v_i. The prototype experiments use h_i = 1, so the
contract input `notional` equals both v_i and w_i. For a fixed haircut above one,
the admission controller supplies w_i to the accounting interface; the contract
counts the supplied amount for both reserves and the admission budget. Haircut
calculation is outside the released contracts. The correlation leakage share
alpha is weighted by the same risk-adjusted amounts.

The Solidity field `rateLimitPerSec` is retained for compatibility, but it stores
an absolute accounting budget per soft-quarantine episode. It does not refill each
second. A destination hard freeze rejects further local admission, while a hub
soft quarantine permits admission up to its remaining episode budget. The trusted
controller performs deadline filtering before contract submission; the vault
enforces collateral and cumulative budget checks.

Calling `setSoftQuarantine(..., true, ...)` resets the counter. The controller
must therefore deduplicate notifications and must not restart an active fault's
budget. This operational requirement is not enforced by a fault-ID guard in the
released contracts. The bounds assume the cumulative cap is preserved.

For tolerated degradation Dmax and conservative upper estimates Hbar and
Aprebar, choose Q <= Dmax - Hbar - Aprebar, with Q >= 0. A nonpositive remainder
requires a hard stop; existing exposure can still exceed the target. A bound on
all admissions from fault occurrence requires a separate always-active rate
cap. Quantile-based latency estimates give probabilistic coverage.

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

# Contract-level normal-path overhead and scaling to 1000 intents.
# This starts a private Anvil node and records 100 transactions per breaker.
python exp_code/rq_overhead_scaling.py --reps 100 --sizes 20,100,500,1000

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

- **RQ1:** tested intents with `L < S` are preempted before their deadlines.
  The separate budget probe checks the cap after activation, not a constant
  end-to-end admission budget from fault occurrence.
- **RQ2:** B3 lowers induced shortfall and admissions during the evidence window
  relative to B1 and B2. Similar unfreeze time indicates containment rather than
  faster recovery.
- **RQ3:** global soft quarantine limits admissions on a destination that has not
  yet completed its hard freeze.
- **RQ4:** correct correlation labels produce no second-hop shortfall in the
  single-shock experiment, and race-coupled pricing reduces concurrency as late
  evidence becomes more common.
- **Normal path:** compare B1 and B3 throughput, median/p95 receipt latency, and
  gas on the same private node. Local receipt polling uses a 1 ms interval.
- **Matched B3$-$:** the original delay sweep is an algebraic post-activation
  budget calculation: fixed-rate exposure grows as rL and an absolute budget
  remains capped. It does not isolate deadline filtering. The supplementary
  equal-budget replay above provides that comparison under explicit assumptions.
- **Scaling:** at every requested $N$, reserved collateral must equal admitted
  notional. Gas and latency are contract measurements; exposure ratios are
  calculated by a separate accounting replay, not injected into the scaling run.
  The compiler layout assigns six storage slots (192 bytes) to each intent, so
  state grows linearly to 187.5 KiB at 1000 intents.

## Limitations

- Testnet latency and finality are not production-tail measurements.
- The committee proof path assumes an honest threshold.
- RQ4 uses Foundry/Anvil plus a Python cross-check; the false-freeze,
  label-error, and static-cap probes are local rather than live multi-region RPC
  campaigns.
- Throughput and receipt latency from the private Anvil node are implementation
  microbenchmarks, not production-chain capacity or finality measurements.
- Watcher/proof CPU and memory, relay traffic, alternative proof backends, and
  full recovery costs have not been profiled. Pause/attestation gas is a per-fault
  cost; the 16.7% admission gas overhead is a separate normal-path measurement.
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
