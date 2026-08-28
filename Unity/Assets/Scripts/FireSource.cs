using UnityEngine;

public class FireSource : MonoBehaviour
{
    public float intensity = 500f;
    public float spreadRadius = 5f;

    private bool isActive = false;

    public void Activate(Vector3 spawnPosition, float newIntensity)
    {
        transform.position = spawnPosition;
        intensity = newIntensity;
        isActive = true;
    }

    public void Deactivate()
    {
        isActive = false;
    }

    void Update()
    {
        if (!isActive) return;

        Nodes[] sensors = FindObjectsByType<Nodes>(FindObjectsSortMode.None);
        foreach (var sensor in sensors)
        {
            float distance = Vector3.Distance(transform.position, sensor.position);
            if (distance < spreadRadius)
            {
                float heatAtSensor = intensity / (1f + distance);
                sensor.temperature = 25f + heatAtSensor;
            }
        }
    }
}