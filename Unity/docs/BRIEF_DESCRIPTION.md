# GaKuna — Evacuation Guidance System

## Project Overview

GaKuna is an IoT-based dynamic evacuation guidance system that provides context-aware, multisensory evacuation directions during single and compound emergency scenarios.

- Issues dynamic visual cues via LED signages (LEFT / RIGHT / STOP) at corridor nodes and audio instructions via voice-guided speakers.
- A network of ESP32 sensor nodes continuously monitors environmental parameters: **temperature**, **vibration (seismic)**, and **pathway blockages** (via ESP32-CAM).
- The decision-making core uses **Proximal Policy Optimization (PPO)** reinforcement learning, trained in a 3D Unity ML-Agents simulation replicating **two floors and five corridor nodes** of Colegio de Muntinlupa's second building.
- The AI agent acts as a **guidance controller**: it learns to assign an optimal signage state to each node by trial and error, per episode.

---

## Simulation Environment (Unity)

### Node graph
- **5 nodes**, arranged as a directed graph across two floors. Each node has:
  - `leftNeighbor`, `rightNeighbor` (hallway neighbours on the same floor).
  - `stairNeighbor` (the node on the opposite floor, for stair nodes), flagged by `isStairNode`.
  - `isExitNode` — node located at / next to an exit.
- Node IDs follow the pattern `1X` (floor 1) and `2X` (floor 2): `11, 12, 21, 22, 23`.

### Floor positions (Y)
> **Scene truth:** Corridor floor meshes sit at `y = -0.1715` (floor 1) and `y = 4.52` (floor 2). Nodes are placed above the corridor meshes at `y ≈ 4.5` (floor 1) and `y ≈ 9.5` (floor 2). The `floor1Y`/`floor2Y` fields in `EvacuationAgent` default to `-0.1715` / `4.52`, which are the corridor-mesh Y values used for fire spawn positioning.

### Agent action space (signage)
- Discrete branch per node. For 5 nodes there are **5 discrete action branches**, each with 3 options (`LEFT`, `RIGHT`, `STOP`).
- Action masking (`WriteDiscreteActionMask`) disables invalid choices:
  - `LEFT` disabled if no left neighbor.
  - `RIGHT` disabled if no right neighbor.
  - `STOP` disabled on exit nodes.
- Signage pathing rule in `GetNextNodeIndex`:
  - **On a stair node** (`isStairNode`) with a `stairNeighbor`: `LEFT` or `RIGHT` routes to the stair neighbor (upstairs/downstairs). `STOP` halts.
  - Otherwise: `LEFT` → left neighbor; `RIGHT` → right neighbor; `STOP` → dead end (stop sign / no onward route).

### Observation space (`CollectObservations`)
Per node (8 values × 5 nodes = 40):
1. temperature (`/ 1000`)
2. seismic level (`/ 7`)
3. isBlocked (0/1)
4. isFloor2 (0/1)
5. isStairNode (0/1)
6. hasLeftNeighbor (0/1)
7. hasRightNeighbor (0/1)
8. isExitNode (0/1)

Global (4 values):
9. fire X (`/ 15`)
10. fire Y (`/ 10`)
11. fire intensity (`/ 1000`)
12. blocked-node count ratio (`blocked / total`)

**Total observation vector size: 44.**

---

## Episode Initialisation (Hazard Randomization)

At the start of each episode (`OnEpisodeBegin`), the environment resets and randomizes hazards:

1. **Reset:** fire deactivated; every node's `temperature = 25`, `seismicLevel = 0`, `isBlocked = false`, `_heatBuffer = 0`, `signage = STOP`.
2. **Blockage:** each node independently has a 30% chance of being blocked, **capped at 2 blocked nodes** per episode (guarantees a viable route usually remains discoverable while maximizing hazard coverage).
3. **Seismic:** *(currently DISABLED — see "Known Deviations / Open Items" below; the code branch exists but is excluded from validation scope)*.
4. **Fire:** with 50/50 floor choice, a fire source is placed at a random corridor coordinate (`X ∈ [-12, 12]`, `Z ∈ [-2, 2]`) on floor 1 (`Y = -0.1715`) or floor 2 (`Y = 4.52`), with intensity randomized in `[300, 800]`.

### Fire / heat model
- `FireSource.Update(cached nodes)` writes heat into each node via `AddHeat()` when within `spreadRadius = 5`.
- Heat at a node: `heat = intensity / (1 + distance)`.
- `Nodes.Update()` (**each frame**) leavens current temperature toward a 25 °C ambient using `heatDecayRate = 0.15` (`Lerp`), then blends in the accumulated heat buffer (`temperature = max(temp, 25 + heat)`).
- `ResetNode()` clears all transient state, so each episode starts from a clean 25 °C baseline.

### Danger scoring (`Nodes.GetDangerScore`)
- Temperature > 60 °C: `+(temp − 60) × 2`.
- Seismic > 4: `+(seismic − 4) × 50`.
- Blocked: `+1000` (marked impassable).
- Stair node + seismic > 3: `+seismic × 20` *(seismic currently disabled; retained for future use)*.

---

## Reward Model

Reward is computed as a **single end-of-episode value** (see `EvaluateCurrentSignage` / `SimulateFromNode`). It is assigned once via `AddReward()` when the episode ends after `MaxStepsPerEpisode = 50` steps. **Do not add per-step shaping here — the single-episode reward is the intended design.**

The reward is the **mean over viable start nodes** (blocked start nodes are excluded from the mean since their outcome is uncontrollable) of a simulated evacuation that follows the currently assigned signage:

- For each start node, simulate evacuee movement following signage up to `maxSteps = nodes.Length` steps:
  - **Routing into a blocked node from a viable start** → `-4` (learnable mistake — agent should route around blockages).
  - **Sign points to null neighbor (dead end)** → `-3`.
  - **Exit reached** → base `+1` plus:
    - speed bonus `+ max(0, (maxSteps − steps) × 0.1)` → faster routes rewarded (range `[0, 2]`).
    - safety bonus `+ max(0, 3 − totalDanger × 0.005)` → lower cumulative danger rewarded (range `[0, 3]`).
  - **No exit reached within step budget** → `-1 − totalDanger × 0.002`.

Resulting per-node reward range is roughly `[-4, +6]`; the episode reward is the mean across the viable (unblocked) start nodes.

**Design intent:** guide the agent away from blocked / dead-end routing and toward short, safe paths to exits, with a smooth gradient for learning.

---

## Training Hyperparameters (`TrainerConfig/gakuna_training.yaml`)

| Parameter | Value | Notes |
|---|---|---|
| trainer_type | PPO | |
| learning_rate | 3.0e-4 | linear schedule to 0 |
| beta | 0.005 | linear schedule |
| epsilon | 0.2 | linear schedule |
| lambd (GAE) | 0.95 | |
| num_epoch | 3 | |
| batch_size | 1024 | |
| buffer_size | 10240 | |
| hidden_units | 256 | 3 layers |
| normalize | true | input normalization |
| gamma | 0.95 | short (< 50-step) episodes |
| strength | 1.0 | extrinsic only |
| time_horizon | 55 | ≥ episode length (50) |
| max_steps | 5,000,000 | ~6–8 h expected |
| summary_freq | 500 | |
| checkpoint_interval | 500,000 | `keep_checkpoints = 5` |
| num_envs | 1 | **must be 1** when training in-editor without `env_path` |
| time_scale | 20 | |

> Environment is the `mlagents` conda env. After a model save, ensure compatible stack: `torch 2.1.2+cpu`, `onnx 1.15.0`, `numpy 1.23.5`, `protobuf 3.20.3`. Avoid upgrading these — mlagents 1.1.0 breaks with newer onnx/pytorch.

---

## Known Deviations / Open Items

1. **Seismic hazard currently disabled.** The code path exists in `OnEpisodeBegin`, but per project decision ("ignore seismic for now") it is EXCLUDED from the current validation scope. When re-enabled, re-verify the `Nodes.GetDangerScore` stair multiplier and the `isStairNode` routing behavior.
2. **Blockage semantics (resolved).** Each node now has an independent 30% chance of being blocked, capped at 2 blocked nodes — the recommended approach for best hazard coverage.
3. **Stair routing (resolved).** Both `LEFT` and `RIGHT` on a stair node now route to the stair neighbor; `STOP` halts. Implemented consistently in `EvacuationAgent.GetNextNodeIndex` and `AgentVisualizer.GetNextIdx`.
4. **Floor Y (resolved).** `floor1Y`/`floor2Y` updated to scene-truth `-0.1715` / `4.52`; no fire-Y offset remains.

---

## Validation & Acceptance Criteria (Agentic)

Use this section to objectively judge a training run. An evaluating agent MUST be able to answer every check from logs/scene alone.

### 0. Inference & visual verification (`AgentPathWalker`)

After **retraining and re-exporting** (see notes below), load the exported `Evacuationer.onnx` into the `Behavior Parameters` Model slot (win behavior / `Heuristic Only` off) and add the **`AgentPathWalker`** component to the **`EvacuationAgent` GameObject**. In Play mode, the Agent GameObject should **walk** the path dictated by the inferred signage:

- Attach `AgentPathWalker` to the same GameObject as `EvacuationAgent` and drag the agent into its `agent` slot.
- The walker continuously reads each node's signage (same LEFT/RIGHT/STOP + stair rule as `GetNextNodeIndex`) and moves the Agent GameObject node-to-node at `walkSpeed` (default 8 m/s), pausing briefly at each node to "read" its sign.
- It cycles through the 5 start nodes and reports `EXIT REACHED` / `DEAD END` / `BLOCKED` / `LOOP` via color (green/red) and `lastStatus`.
- If the walk stalls ("waiting for signage"), the loaded model has not yet produced actions — confirm the ONNX model is assigned and no `Communicator` is blocking inference.

**Inference-visual mode (stable scenario):** when a model is loaded with no training communicator active (or `inferenceVisualMode` is checked), `EvacuationAgent`:
- randomizes the hazard scenario **once** and keeps it stable (fire/signage do NOT reset mid-demo);
- does NOT call `EndEpisode`, so the walker can follow signage all the way to an exit without the episode restarting.

**IMPORTANT — observation space is 44 values, not 25.** The scene's `BehaviorParameters.VectorObservationSize` must be **44** to match `CollectObservations`. Any model trained against the older 25-value space will receive garbage inputs (fixed/`RIGHT`-biased actions) and MUST be **retrained and re-exported** before it will follow signs correctly.

**Wiring summary (`EvacuationAgent` GameObject):**
- `EvacuationAgent` (ML-Agents) — receives model actions, sets node signage each decision step.
- `AgentPathWalker` — reads that signage and moves the GameObject along the path for visual verification.
- `BehaviorParameters` — Model = fresh `Evacuationer.onnx`, `VectorObservationSize` = 44, `DecisionRequester` sets a decision cadence.

### 1. Environment integrity (pre-training sanity)
- [ ] Scene contains exactly **5 nodes** across 2 floors; IDs follow `1X` / `2X`.
- [ ] Floor 1 nodes (1A=11, 1B=12) have `floor: 1`; floor 2 nodes (2A=21, 2B=22, 2C=23) have `floor: 2`.
- [ ] Node_1A is `isExitNode: true` and Exit_1 is within 3 m.
- [ ] Node_2C is `isStairNode: true` with `stairNeighbor` pointing to Node_1B (floor 1).
- [ ] No cross-floor `rightNeighbor` or `leftNeighbor` connections (floor-2 endpoints have `null` right/left).
- [ ] Fire-spawn Y uses `floor1Y`/`floor2Y`; heat reaches nodes near the fire and decays to 25 °C ambient.
- [ ] `num_envs: 1` (training from Editor).

### 2. Training stability (TensorBoard metrics)
- [ ] **Mean Reward is finite and not NaN** from the first summaries.
- [ ] Reward **trends upward** and converges; random policy should start negative and improve toward the positive range.
- [ ] `Policy Loss`, `Value Loss`, and `Entropy` stay bounded (no divergence/spikes).
- [ ] `Learning Rate` decays linearly to ~0 across `max_steps`.
- [ ] No persistent `Communicator has exited` restarts blocking progress.

### 3. Quality-of-learned-policy checks
- [ ] Run-total success rate (via `AgentVisualizer`: `successfulRuns / totalRuns`) reaches **≥ 80%**.
- [ ] No signage configuration produces a **loop** (LOOP DETECTED) or **dead end** (DEAD END) in the visualizer.
- [ ] Signage routes evacuees **around** blocked nodes (BLOCKED should not appear as a terminal failure in a solved policy).
- [ ] Fire-side nodes are **not** the preferred route when a safer path exists.

### 4. Export & deployment
- [ ] On Ctrl+C (interrupt), the trainer saves a `.onnx` model without the `onnxscript` / `ModuleNotFoundError` failure (requires the pinned torch 2.1.2 stack).
- [ ] The exported `.onnx` completes `mlagents` ONNX export and loads in the Unity inference config.
- [ ] Observation size of the exported model matches the current 44-value observation space.

### 5. Acceptance gate (summary)

| Metric | Target |
|---|---|
| Mean Reward (converged) | positive, stable (suggest ≥ +1.0 mean over last summaries) |
| Visualizer success rate | ≥ 80% |
| Loops / dead ends / blocked-terminal failures | 0 |
| ONNX export | succeeds, loads in Unity |
| Training steps | full `max_steps` or early-stop only after gate reached |

> **Early-stop rule:** if the policy reaches all quality targets (3) before `max_steps`, training may be stopped early. Otherwise, allow the run to complete.

---

*Revision status: aligned with current code. Last reviewed: node graph corrected (Node_1A is exit, Node_2C is stair node, no cross-floor neighbors), floor values set for all nodes, reward rebalanced (blocked-starts excluded, -4/-3 penalties vs +6 success band), seismic disabled, single end-of-episode reward, 44-value observation space, 5-node/2-floor graph, blockage capped at 2.*
