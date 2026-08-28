using Unity.MLAgents;
using Unity.MLAgents.Actuators;
using Unity.MLAgents.Sensors;
using UnityEngine;

public class EvacuationAgent : Agent
{
    public Nodes[] nodes;
    public Transform[] exits;
    public FireSource fireSource;

    // Set these to match your actual Unity scene Y positions
    public float floor1Y = 1f;    // Y position of floor 1 corridor
    public float floor2Y = 5f;    // Y position of floor 2 corridor

    private const int SignageOptions = 3;
    private const int MaxStepsPerEpisode = 50; // Steps before episode ends
    private int stepCount = 0;

    public override void OnEpisodeBegin()
    {
        stepCount = 0;
        fireSource.Deactivate();

        foreach (var node in nodes)
            node.ResetNode();
        
        // Random blockage
        if (Random.value > 0.7f)
            nodes[Random.Range(0, nodes.Length)].isBlocked = true;

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

        //Debug.Log($"Episode start — Fire on Floor {(spawnOnFloor2 ? 2 : 1)} at {firePos}");
    }



    public override void CollectObservations(VectorSensor sensor)
    {
        foreach (var node in nodes)
        {
            sensor.AddObservation(node.temperature / 1000f);
            sensor.AddObservation(node.seismicLevel / 7f);
            sensor.AddObservation(node.isBlocked ? 1f : 0f);
            sensor.AddObservation(node.floor == 2 ? 1f : 0f);
            sensor.AddObservation(node.isStairNode ? 1f : 0f);
        }
        // 5 × 5 nodes = 25
    }

    public override void OnActionReceived(ActionBuffers actions)
    {
        stepCount++;

        // Apply signage from PPO
        string log = "PPO Actions: ";
        for (int i = 0; i < nodes.Length; i++)
        {
            int action = Mathf.Clamp(actions.DiscreteActions[i], 0, SignageOptions - 1);
            nodes[i].signage = (SignageState)action;
            log += $"Node{nodes[i].nodeID}={(SignageState)action} ";
        }
        Debug.Log(log);

        // Give step reward based on current signage quality
        float reward = EvaluateCurrentSignage();
        AddReward(reward);

        // Only end episode after enough steps
        if (stepCount >= MaxStepsPerEpisode)
        {
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

    // Renamed from SimulateAgentFollowingSigns — now called per step
    float EvaluateCurrentSignage()
    {
        float totalReward = 0f;

        for (int startIdx = 0; startIdx < nodes.Length; startIdx++)
            totalReward += SimulateFromNode(startIdx);

        return totalReward / nodes.Length;
    }
    float SimulateFromNode(int startIdx)
    {
        int currentIdx = startIdx;
        int steps = 0;
        int maxSteps = nodes.Length;
        float totalDanger = 0f;
        bool reachedExit = false;
        bool[] visited = new bool[nodes.Length];
        int nextIdx = 0;
        while (steps < maxSteps)
        {
            if (currentIdx < 0 || currentIdx >= nodes.Length) break;
            if (visited[currentIdx]) break;

            visited[currentIdx] = true;
            Nodes current = nodes[currentIdx];
            totalDanger += current.GetDangerScore();

            if (IsNearExit(current.position, 3f) || current.isExitNode)
            {
                reachedExit = true;
                break;
            }

            nextIdx = GetNextNodeIndex(currentIdx);

            // PENALTY: sign points to null neighbor = dead end
            if (nextIdx == -1)
            {
                return -50f; // Heavy penalty for dead end routing
            }

            currentIdx = nextIdx;
            steps++;
        }

        if (reachedExit)
        {
            float speedBonus = Mathf.Max(0, (maxSteps - steps) * 2f); // reduced from 5
            float safetyBonus = Mathf.Max(0, 100f - totalDanger);      // reduced from 300
            return 10f + speedBonus + safetyBonus;                      // reduced from 100
        }

        // Gentler penalties so reward signal has gradient to climb
        if (nextIdx == -1) return -50f;   // was -500
        return -15f - (totalDanger * 0.01f); // was -150
    }

    int GetNextNodeIndex(int currentIdx)
    {
        Nodes current = nodes[currentIdx];
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