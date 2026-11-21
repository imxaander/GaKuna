using UnityEngine;

public class DebugAgent : MonoBehaviour
{
    public EvacuationAgent agent;

    void Start()
    {

        Debug.unityLogger.logEnabled = false;
        if (agent == null)
        {
            Debug.LogError("EvacuationAgent reference is null!");
            return;
        }

        if (agent.nodes == null || agent.nodes.Length == 0)
            Debug.LogError("Nodes array is empty or null!");

        if (agent.exits == null || agent.exits.Length == 0)
            Debug.LogError("Exits array is empty or null!");

        if (agent.fireSource == null)
            Debug.LogError("FireSource is null!");

        // Check each node's neighbors
        foreach (var node in agent.nodes)
        {
            if (node == null)
            {
                Debug.LogError("A node in the array is null!");
                continue;
            }

            if (node.leftNeighbor == null && node.rightNeighbor == null)
                Debug.LogWarning($"Node {node.nodeID} has NO neighbors assigned - agent will always get -1 and loop forever");
        }

        Debug.Log($"Setup OK � {agent.nodes.Length} nodes, {agent.exits.Length} exits");
    }
}