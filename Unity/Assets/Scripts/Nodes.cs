using UnityEngine;

public enum SignageState
{
    Left,
    Right,
    Stop,
}

public class Nodes : MonoBehaviour
{
    public int nodeID;
    public int floor;  // 1 = ground floor, 2 = second floor

    // Hallway neighbors
    public Nodes leftNeighbor;
    public Nodes rightNeighbor;

    // Stair neighbor — the node on the OTHER floor this connects to
    public Nodes stairNeighbor;
    public bool isStairNode = false;  // Mark nodes adjacent to stairs

    // Is this node at or next to the exit?
    public bool isExitNode = false;

    // Sensor readings
    public float temperature = 25f;
    public float seismicLevel = 0f;
    public bool isBlocked = false;

    // Signage
    private SignageState _signage = SignageState.Stop;


    public GameObject SignLeft;
    public GameObject SignRight;
    public GameObject SignCross;

    public Material onMat, offMat;
    // Always reflects actual GameObject position
    public SignageState signage
    {
        get => _signage;
        set { _signage = value; UpdateVisuals(); }
    }

    public Vector3 position => transform.position;

    void UpdateVisuals()
    {
        UpdateSignageState(_signage);

        //Debug.Log($"Node {nodeID} (Floor {floor}): {_signage}");
    }


    public void UpdateSignageState(SignageState state)
    {
        switch (state)
        {
            case SignageState.Left:
                SignLeft.GetComponent<MeshRenderer>().material = onMat;
                SignRight.GetComponent<MeshRenderer>().material = offMat;
                SignCross.GetComponent<MeshRenderer>().material = offMat;
                break;
            case SignageState.Right:
                SignLeft.GetComponent<MeshRenderer>().material = offMat;
                SignRight.GetComponent<MeshRenderer>().material = onMat;
                SignCross.GetComponent<MeshRenderer>().material = offMat;
                break;
            case SignageState.Stop:
                SignLeft.GetComponent<MeshRenderer>().material = offMat;
                SignRight.GetComponent<MeshRenderer>().material = offMat;
                SignCross.GetComponent<MeshRenderer>().material = onMat;
                break;
            default:
                SignLeft.GetComponent<MeshRenderer>().material = offMat;
                SignRight.GetComponent<MeshRenderer>().material = offMat;
                SignCross.GetComponent<MeshRenderer>().material = offMat;
                break;


        }
        ;
    }

    public void ResetNode()
    {
        temperature = 25f;
        seismicLevel = 0f;
        isBlocked = false;
        signage = SignageState.Stop;
    }

    public float GetDangerScore()
    {
        float score = 0f;
        if (temperature > 60f) score += (temperature - 60f) * 2.0f;
        if (seismicLevel > 4f) score += (seismicLevel - 4f) * 50f;
        if (isBlocked) score += 1000f;
        // Stair nodes are slightly riskier during seismic events
        if (isStairNode && seismicLevel > 3f) score += seismicLevel * 20f;
        return score;
    }
}