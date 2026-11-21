using Unity.MLAgents;
using Unity.MLAgents.Actuators;
using Unity.MLAgents.Policies;
using Unity.MLAgents.Sensors;
using UnityEngine;

public class EvacuationAgent : Agent
{
    public Nodes[] nodes;
    public Transform[] exits;
    public FireSource fireSource;

    // Actual corridor Y positions in the Unity scene (used for fire spawn)
    public float floor1Y = -0.1715f;   // Y position of floor 1 corridor
    public float floor2Y = 4.52f;      // Y position of floor 2 corridor


    [Tooltip("Set true for the inference visual demo: keep the scenario stable and " +
             "do NOT end/reset the episode mid-walk. Also auto-enables when a model " +
             "is loaded with no training communicator active.")]
    public bool inferenceVisualMode = false;

    private const int SignageOptions = 3;
    private const int MaxStepsPerEpisode = 50;
    private int stepCount = 0;

    // Reward accumulator — give full reward at episode end only
    private float episodeReward = 0f;

    // Tracks whether the current hazard scenario has been set up (inference demo)
    private bool scenarioInitialized = false;

    public override void OnEpisodeBegin()
    {
        stepCount = 0;
        episodeReward = 0f;
        fireSource.Deactivate();

        foreach (var node in nodes)
            node.ResetNode();

        // In inference-visual mode, randomize the hazard scenario only once and keep
        // it stable so the walker can complete a route without the fire/signage resetting
        // mid-demo. Re-entering Play (or toggling the flag) restarts a fresh scenario.
        bool inferenceDemo = inferenceVisualMode || IsInferenceDemo();
        if (inferenceDemo && scenarioInitialized)
            return;

        // Random blockages — each node has a 30% chance, capped at 2 blocked nodes
        // to guarantee a viable route usually remains discoverable.
        const int maxBlocked = 2;
        int blockedCount = 0;
        for (int i = 0; i < nodes.Length; i++)
        {
            if (blockedCount >= maxBlocked) break;
            if (Random.value <= 0.3f)
            {
                nodes[i].isBlocked = true;
                blockedCount++;
            }
        }

        // Random seismic
        if (Random.value > 0.5f)
        {
            int idx = Random.value > 0.4f
                ? Random.Range(2, nodes.Length)
                : Random.Range(0, 2);
            nodes[idx].seismicLevel = Random.Range(3f, 7f);
        }

        // Randomly spawn fire on floor 1 OR floor 2
        bool spawnOnFloor2 = Random.value > 0.5f;
        float fireY = spawnOnFloor2 ? floor2Y : floor1Y;
        Vector3 firePos = new Vector3(
            Random.Range(-12f, 12f),
            fireY,
            Random.Range(-2f, 2f)
        );
        fireSource.Activate(firePos, Random.Range(300f, 800f));

        scenarioInitialized = true;
    }

    bool IsInferenceDemo()
    {
        var bp = GetComponent<BehaviorParameters>();
        return bp != null && bp.Model != null && !Academy.Instance.IsCommunicatorOn;
    }

    public override void CollectObservations(VectorSensor sensor)
    {
        // Per-node observations: temp, seismic, blocked, floor, stair, signage, blocked-neighbor
        foreach (var node in nodes)
        {
            sensor.AddObservation(node.temperature / 1000f);
            sensor.AddObservation(node.seismicLevel / 7f);
            sensor.AddObservation(node.isBlocked ? 1f : 0f);
            sensor.AddObservation(node.floor == 2 ? 1f : 0f);
            sensor.AddObservation(node.isStairNode ? 1f : 0f);

            // Connectivity: does this node have left/right neighbors
            sensor.AddObservation(node.leftNeighbor != null ? 1f : 0f);
            sensor.AddObservation(node.rightNeighbor != null ? 1f : 0f);
            sensor.AddObservation(node.isExitNode ? 1f : 0f);
        }

        // Global observations
        // Fire position normalized
        Vector3 firePos = fireSource.transform.position;
        sensor.AddObservation(firePos.x / 15f);
        sensor.AddObservation(firePos.y / 10f);

        // Fire intensity normalized
        sensor.AddObservation(fireSource.intensity / 1000f);

        // Total blocked count
        int blockedCount = 0;
        foreach (var node in nodes)
            if (node.isBlocked) blockedCount++;
        sensor.AddObservation((float)blockedCount / nodes.Length);
    }

    public override void OnActionReceived(ActionBuffers actions)
    {
        stepCount++;

        // Apply signage from PPO
        for (int i = 0; i < nodes.Length; i++)
        {
            int action = Mathf.Clamp(actions.DiscreteActions[i], 0, SignageOptions - 1);
            nodes[i].signage = (SignageState)action;
        }

        // In inference-visual mode, keep the episode open so the walker can follow the
        // (stable) signage all the way to the exit without hazards/signs resetting.
        bool inferenceDemo = inferenceVisualMode || IsInferenceDemo();
        if (inferenceDemo)
            return;

        // End episode — give single evaluation reward
        if (stepCount >= MaxStepsPerEpisode)
        {
            float finalReward = EvaluateCurrentSignage();
            AddReward(finalReward);
            EndEpisode();
        }
    }

    public override void WriteDiscreteActionMask(IDiscreteActionMask actionMask)
    {
        for (int i = 0; i < nodes.Length; i++)
        {
            if (nodes[i].leftNeighbor == null)
                actionMask.SetActionEnabled(i, (int)SignageState.Left, false);
            if (nodes[i].rightNeighbor == null)
                actionMask.SetActionEnabled(i, (int)SignageState.Right, false);
            if (nodes[i].isExitNode)
                actionMask.SetActionEnabled(i, (int)SignageState.Stop, false);
        }
    }

    /// <summary>
    /// Simulate evacuation from every node following current signage.
    /// Returns average reward across all start positions.
    /// </summary>
    float EvaluateCurrentSignage()
    {
        float totalReward = 0f;
        int scored = 0;

        for (int startIdx = 0; startIdx < nodes.Length; startIdx++)
        {
            // Skip starts that are themselves blocked: the signage cannot help evacuees
            // at an impassable node, so its outcome is out of the agent's control. Including
            // it would swamp the mean with noise and hide the learnable signal. (Routing INTO
            // a blocked node from a viable start is still penalized in SimulateFromNode.)
            if (nodes[startIdx].isBlocked)
                continue;

            totalReward += SimulateFromNode(startIdx);
            scored++;
        }

        return scored > 0 ? totalReward / scored : 0f;
    }

    float SimulateFromNode(int startIdx)
    {
        int currentIdx = startIdx;
        int steps = 0;
        int maxSteps = nodes.Length;
        float totalDanger = 0f;
        bool reachedExit = false;
        bool[] visited = new bool[nodes.Length];

        while (steps < maxSteps)
        {
            if (currentIdx < 0 || currentIdx >= nodes.Length) break;
            if (visited[currentIdx]) break;

            visited[currentIdx] = true;
            Nodes current = nodes[currentIdx];

            // Routing into a blocked node from a viable start is a learnable mistake — penalize
            // moderately (was -8, which dominated the +1..+6 success band and hid any gradient).
            if (current.isBlocked)
                return -4f;

            totalDanger += current.GetDangerScore();

            if (IsNearExit(current.position, 3f) || current.isExitNode)
            {
                reachedExit = true;
                break;
            }

            int nextIdx = GetNextNodeIndex(currentIdx);

            // Sign points to null neighbor = dead end (was -5; softened so the scale stays
            // comparable to the +1..+6 success band and the agent keeps a gradient to explore).
            if (nextIdx == -1)
                return -3f;

            currentIdx = nextIdx;
            steps++;
        }

        if (reachedExit)
        {
            // Normalize reward: speed bonus [0, 2], safety bonus [0, 3], base 1
            float speedBonus = Mathf.Max(0f, (maxSteps - steps) * 0.1f);
            float safetyBonus = Mathf.Max(0f, 3f - totalDanger * 0.005f);
            return 1f + speedBonus + safetyBonus;
        }

        return -1f - (totalDanger * 0.002f);
    }

    int GetNextNodeIndex(int currentIdx)
    {
        Nodes current = nodes[currentIdx];
        // On a stair node, LEFT or RIGHT leads upstairs/downstairs to the stair neighbor
        if (current.isStairNode && current.stairNeighbor != null &&
            current.signage != SignageState.Stop)
            return System.Array.IndexOf(nodes, current.stairNeighbor);

        switch (current.signage)
        {
            case SignageState.Left:
                if (current.leftNeighbor != null)
                    return System.Array.IndexOf(nodes, current.leftNeighbor);
                break;
            case SignageState.Right:
                if (current.rightNeighbor != null)
                    return System.Array.IndexOf(nodes, current.rightNeighbor);
                break;
            case SignageState.Stop:
            default:
                return -1;
        }
        return -1;
    }

    bool IsNearExit(Vector3 position, float threshold)
    {
        foreach (var exit in exits)
            if (Vector3.Distance(position, exit.position) <= threshold)
                return true;
        return false;
    }

    void Start()
    {
        if (nodes == null || nodes.Length == 0)
        { Debug.LogError("GAKUNA: Nodes array empty!"); return; }
        if (exits == null || exits.Length == 0)
        { Debug.LogError("GAKUNA: No exits assigned!"); return; }
        if (fireSource == null)
        { Debug.LogError("GAKUNA: FireSource not assigned!"); return; }

        foreach (var node in nodes)
            if (node.leftNeighbor == null && node.rightNeighbor == null)
                Debug.LogWarning($"GAKUNA: Node {node.nodeID} has NO neighbors!");

        Debug.Log($"GAKUNA: OK — {nodes.Length} nodes, {exits.Length} exit(s)");
    }
}
