using UnityEngine;
using System.Collections;
using System.Collections.Generic;

public class AgentVisualizer : MonoBehaviour
{
    [Header("References")]
    public EvacuationAgent agent;       // drag your Agent GameObject here
    public Transform[] exits;           // same exits array as agent

    [Header("Settings")]
    public float moveSpeed = 2f;        // how fast sphere moves between nodes
    public float pauseAtNode = 0.3f;    // seconds to pause at each node
    public int startNodeIndex = 0;      // which node to start from each run

    [Header("Colors")]
    public Color safeColor = Color.green;
    public Color dangerColor = Color.red;
    public Color movingColor = Color.cyan;
    public Color exitColor = Color.yellow;

    private Renderer sphereRenderer;
    private bool isMoving = false;
    private List<Vector3> currentPath = new List<Vector3>();
    private bool reachedExit = false;
    private int pathNodeCount = 0;
    private float lastRunTime = 0f;

    // Stats
    [HideInInspector] public int totalRuns = 0;
    [HideInInspector] public int successfulRuns = 0;
    [HideInInspector] public float lastPathLength = 0;
    [HideInInspector] public string lastResult = "Waiting...";
    [HideInInspector] public string currentSign = "";

    void Start()
    {
        sphereRenderer = GetComponentInChildren<Renderer>();
        if (sphereRenderer == null)
            sphereRenderer = GetComponent<Renderer>();

        // Start visualizing every 2 seconds
        InvokeRepeating(nameof(RunVisualization), 1f, 2f);
    }

    void RunVisualization()
    {
        if (isMoving) return;
        if (agent == null || agent.nodes == null || agent.nodes.Length == 0) return;

        StartCoroutine(FollowSignage(startNodeIndex));
    }

    IEnumerator FollowSignage(int startIdx)
    {
        isMoving = true;
        totalRuns++;
        reachedExit = false;
        pathNodeCount = 0;
        currentPath.Clear();

        int currentIdx = startIdx;
        bool[] visited = new bool[agent.nodes.Length];
        int maxSteps = agent.nodes.Length * 4;

        // Teleport to start node
        transform.position = agent.nodes[startIdx].position + Vector3.up * 0.5f;
        SetColor(movingColor);

        int steps = 0;
        while (steps < maxSteps)
        {
            if (currentIdx < 0 || currentIdx >= agent.nodes.Length) break;
            if (visited[currentIdx])
            {
                lastResult = "LOOP DETECTED";
                break;
            }

            visited[currentIdx] = true;
            Nodes current = agent.nodes[currentIdx];
            pathNodeCount++;
            currentPath.Add(current.position);

            // Show which sign we're reading
            currentSign = $"Node{current.nodeID} → {current.signage}";

            // Color based on danger
            float danger = current.GetDangerScore();
            Color nodeColor = danger > 100 ? dangerColor : movingColor;
            SetColor(nodeColor);

            // Move to this node
            yield return StartCoroutine(MoveTo(current.position + Vector3.up * 0.5f));
            yield return new WaitForSeconds(pauseAtNode);

            // Check exit
            if (current.isExitNode || IsNearExit(current.position, 3f))
            {
                reachedExit = true;
                successfulRuns++;
                lastResult = $"EXIT REACHED in {pathNodeCount} steps";
                lastPathLength = pathNodeCount;
                SetColor(exitColor);
                yield return new WaitForSeconds(0.5f);
                break;
            }

            // Get next node from signage
            int nextIdx = GetNextIdx(currentIdx);
            if (nextIdx == -1)
            {
                lastResult = $"DEAD END at Node{current.nodeID} ({current.signage})";
                SetColor(dangerColor);
                yield return new WaitForSeconds(0.5f);
                break;
            }

            currentIdx = nextIdx;
            steps++;
        }

        if (!reachedExit && lastResult.StartsWith("EXIT") == false)
        {
            lastResult = $"FAILED — {pathNodeCount} steps";
        }

        lastRunTime = Time.time;
        isMoving = false;
    }

    IEnumerator MoveTo(Vector3 target)
    {
        float elapsed = 0f;
        Vector3 start = transform.position;
        float duration = Vector3.Distance(start, target) / moveSpeed;

        while (elapsed < duration)
        {
            elapsed += Time.deltaTime;
            transform.position = Vector3.Lerp(start, target, elapsed / duration);
            yield return null;
        }

        transform.position = target;
    }

    void SetColor(Color c)
    {
        if (sphereRenderer != null)
            sphereRenderer.material.color = c;
    }

    int GetNextIdx(int currentIdx)
    {
        Nodes current = agent.nodes[currentIdx];
        switch (current.signage)
        {
            case SignageState.Left:
                if (current.leftNeighbor != null)
                    return System.Array.IndexOf(agent.nodes, current.leftNeighbor);
                break;
            case SignageState.Right:
                if (current.rightNeighbor != null)
                    return System.Array.IndexOf(agent.nodes, current.rightNeighbor);
                break;
            case SignageState.Stop:
            default:
                return -1;
        }
        return -1;
    }

    bool IsNearExit(Vector3 pos, float threshold)
    {
        foreach (var exit in exits)
            if (Vector3.Distance(pos, exit.position) <= threshold)
                return true;
        return false;
    }
}