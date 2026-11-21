using UnityEngine;

public class FireSource : MonoBehaviour
{
    public float intensity = 500f;
    public float spreadRadius = 5f;

    private bool isActive = false;
    private Nodes[] cachedNodes;

    public void Activate(Vector3 spawnPosition, float newIntensity)
    {
        transform.position = spawnPosition;
        intensity = newIntensity;
        isActive = true;

        if (cachedNodes == null)
            cachedNodes = FindObjectsByType<Nodes>(FindObjectsSortMode.None);
    }

    public void Deactivate()
    {
        isActive = false;
    }

    void Update()
    {
        if (!isActive) return;
        if (cachedNodes == null) return;

        foreach (var node in cachedNodes)
        {
            float distance = Vector3.Distance(transform.position, node.position);
            if (distance < spreadRadius)
            {
                float heatAtNode = intensity / (1f + distance);
                node.AddHeat(heatAtNode);
            }
        }
    }
}
