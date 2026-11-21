using UnityEngine;

/// <summary>
/// Moves the EvacuationAgent GameObject along the path dictated by the signage
/// currently assigned to each node. Reads LEFT/RIGHT/STOP just like the simulation's
/// GetNextNodeIndex rule (stair nodes route upstairs/downstairs on LEFT or RIGHT).
/// Used for inference-only visualisation of the learned policy.
/// </summary>
public class AgentPathWalker : MonoBehaviour
{
    [Header("References")]
    public EvacuationAgent agent;

    [Header("Settings")]
    [Tooltip("How fast the agent walks between nodes (m/s).")]
    public float walkSpeed = 8f;
    [Tooltip("Distance from a node considered 'arrived'.")]
    public float nodeArriveDistance = 0.5f;
    [Tooltip("Seconds to pause at each node to 'read' its signage.")]
    public float pauseAtNode = 0.15f;
    [Tooltip("Vertical offset so the agent floats just above the floor/corridor.")]
    public float heightOffset = 0.5f;
    [Tooltip("Max nodes traversed before declaring a LOOP (safety against infinite walks).")]
    public int maxMoveSteps = 50;

    [Header("Colors")]
    public Color walkingColor = Color.cyan;
    public Color readingColor = Color.yellow;
    public Color exitColor = Color.green;
    public Color deadEndColor = Color.red;

    // Runtime state
    private int targetNodeIdx = -1;
    private int currentNodeIdx = -1;
    private int walkStartIdx = 0;
    private int stepsTaken = 0;
    private float pauseRemaining = 0f;
    private bool readingSign = false;
    private bool everLeftStart = false;
    private Renderer bodyRenderer;

    // Stats
    [HideInInspector] public int totalWalks = 0;
    [HideInInspector] public int exitReachedWalks = 0;
    [HideInInspector] public string lastStatus = "waiting for signage...";
    [HideInInspector] public string currentSign = "";

    void Start()
    {
        bodyRenderer = GetComponentInChildren<Renderer>();
        if (bodyRenderer == null)
            bodyRenderer = GetComponent<Renderer>();

        if (agent == null || agent.nodes == null || agent.nodes.Length == 0)
        {
            lastStatus = "ERROR: agent / nodes not assigned";
            enabled = false;
            return;
        }
        if (agent.nodes.Length == 0) return;

        // Snap to the first start node
        walkStartIdx = Mathf.Clamp(walkStartIdx, 0, agent.nodes.Length - 1);
        SnapToNode(walkStartIdx);
        currentNodeIdx = walkStartIdx;
        stepsTaken = 0;
        everLeftStart = false;
        SetColor(readingColor);
        lastStatus = $"starting from Node{agent.nodes[walkStartIdx].nodeID}";
    }

    void Update()
    {
        if (agent == null || agent.nodes == null || agent.nodes.Length == 0) return;

        // While paused to read a sign, just drain the pause timer.
        if (readingSign)
        {
            pauseRemaining -= Time.deltaTime;
            if (pauseRemaining <= 0f)
                readingSign = false;
            return;
        }

        // If we have no valid target, pick one from the current signage.
        if (targetNodeIdx < 0)
        {
            if (!TryGetTargetFromCurrentSignage(out int nextIdx))
            {
                // At the start node with STOP signage = still waiting for the model
                // to produce actions (occurs right after an episode reset). Wait, don't fail.
                if (!everLeftStart && currentNodeIdx == walkStartIdx)
                {
                    lastStatus = $"waiting for signage at Node{agent.nodes[currentNodeIdx].nodeID}...";
                    return;
                }
                HandleTerminal("DEAD END / STOP");
                return;
            }
            targetNodeIdx = nextIdx;
            SetColor(walkingColor);
        }

        // Move toward the target node
        Vector3 targetPos = TargetPosition(agent.nodes[targetNodeIdx]);
        Vector3 toTarget = targetPos - transform.position;

        if (toTarget.sqrMagnitude <= nodeArriveDistance * nodeArriveDistance)
        {
            // Arrived at the target node — commit position exactly.
            SnapToNode(targetNodeIdx);
            stepsTaken++;

            // Loop safety: if we've moved through too many nodes without reaching an exit.
            if (stepsTaken > maxMoveSteps)
            {
                HandleTerminal("LOOP");
                return;
            }

            // At this node: handle exit, then blocked, then pause to read the sign.
            if (IsExitNode(targetNodeIdx))
            {
                HandleTerminal("EXIT REACHED");
                return;
            }
            if (agent.nodes[targetNodeIdx].isBlocked)
            {
                HandleTerminal("BLOCKED");
                return;
            }

            // Enter the reading state: pause, then re-evaluate signage next cycle.
            currentNodeIdx = targetNodeIdx;
            targetNodeIdx = -1;
            readingSign = true;
            pauseRemaining = pauseAtNode;
            everLeftStart = true;
            SetColor(readingColor);
            return;
        }

        // Walk toward target
        transform.position = Vector3.MoveTowards(
            transform.position, targetPos, walkSpeed * Time.deltaTime);

        // Face the direction of travel
        Vector3 dir = toTarget; dir.y = 0f;
        if (dir.sqrMagnitude > 0.0001f)
            transform.rotation = Quaternion.LookRotation(dir, Vector3.up);
    }

    bool TryGetTargetFromCurrentSignage(out int nextIdx)
    {
        nextIdx = -1;
        if (currentNodeIdx < 0 || currentNodeIdx >= agent.nodes.Length) return false;

        Nodes current = agent.nodes[currentNodeIdx];
        currentSign = $"Node{current.nodeID} -> {current.signage}";

        // Stair rule: LEFT or RIGHT on a stair node routes upstairs/downstairs
        if (current.isStairNode && current.stairNeighbor != null &&
            current.signage != SignageState.Stop)
        {
            int si = System.Array.IndexOf(agent.nodes, current.stairNeighbor);
            if (si >= 0) { nextIdx = si; return true; }
        }

        switch (current.signage)
        {
            case SignageState.Left:
                if (current.leftNeighbor != null)
                {
                    int li = System.Array.IndexOf(agent.nodes, current.leftNeighbor);
                    if (li >= 0) { nextIdx = li; return true; }
                }
                return false;
            case SignageState.Right:
                if (current.rightNeighbor != null)
                {
                    int ri = System.Array.IndexOf(agent.nodes, current.rightNeighbor);
                    if (ri >= 0) { nextIdx = ri; return true; }
                }
                return false;
            case SignageState.Stop:
            default:
                return false;
        }
    }

    bool IsExitNode(int idx)
    {
        if (agent.nodes[idx].isExitNode) return true;
        foreach (var exit in agent.exits)
        {
            if (exit == null) continue;
            if (Vector3.Distance(agent.nodes[idx].position, exit.position) <= 3f)
                return true;
        }
        return false;
    }

    void HandleTerminal(string status)
    {
        lastStatus = status;
        if (status == "EXIT REACHED")
        {
            exitReachedWalks++;
            SetColor(exitColor);
        }
        else
        {
            SetColor(deadEndColor);
        }

        // Brief hold, then restart the walk from the next start node.
        Invoke(nameof(RestartWalk), 0.6f);
        enabled = false;
        readingSign = false;
        targetNodeIdx = -1;
        currentNodeIdx = -1;
    }

    void RestartWalk()
    {
        totalWalks++;
        walkStartIdx = (walkStartIdx + 1) % agent.nodes.Length;
        SnapToNode(walkStartIdx);
        stepsTaken = 0;
        readingSign = false;
        targetNodeIdx = -1;
        currentNodeIdx = walkStartIdx;
        everLeftStart = false;
        currentSign = "";
        SetColor(readingColor);
        lastStatus = $"starting from Node{agent.nodes[walkStartIdx].nodeID}";
        enabled = true;
    }

    void SnapToNode(int nodeIdx)
    {
        transform.position = TargetPosition(agent.nodes[nodeIdx]);
    }

    Vector3 TargetPosition(Nodes node)
    {
        return node.position + Vector3.up * heightOffset;
    }

    void SetColor(Color c)
    {
        if (bodyRenderer != null)
            bodyRenderer.material.color = c;
    }
}
