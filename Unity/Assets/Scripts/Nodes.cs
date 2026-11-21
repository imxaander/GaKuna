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

    // Temperature decay: ambient temperature and decay rate
    private const float ambientTemp = 25f;
    private const float heatDecayRate = 0.15f; // temperature units lost per second toward ambient

    // Heat accumulation buffer — FireSource writes here, Update() blends into temperature
    private float _heatBuffer = 0f;

    // Signage
    private SignageState _signage = SignageState.Stop;

    public GameObject SignLeft;
    public GameObject SignRight;
    public GameObject SignCross;

    public Material onMat, offMat;

    public SignageState signage
    {
        get => _signage;
        set { _signage = value; UpdateVisuals(); }
    }

    public Vector3 position => transform.position;

    void Update()
    {
        // Decay temperature toward ambient each frame
        if (Mathf.Abs(temperature - ambientTemp) > 0.01f)
        {
            temperature = Mathf.Lerp(temperature, ambientTemp, heatDecayRate * Time.deltaTime);
        }
        else
        {
            temperature = ambientTemp;
        }

        // Blend in accumulated heat from FireSource
        if (_heatBuffer > 0f)
        {
            temperature = Mathf.Max(temperature, ambientTemp + _heatBuffer);
            _heatBuffer = 0f;
        }
    }

    /// <summary>Called by FireSource each frame to add heat before Update() blends it.</summary>
    public void AddHeat(float heatAmount)
    {
        _heatBuffer = Mathf.Max(_heatBuffer, heatAmount);
    }

    void UpdateVisuals()
    {
        UpdateSignageState(_signage);
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
    }

    public void ResetNode()
    {
        temperature = ambientTemp;
        seismicLevel = 0f;
        isBlocked = false;
        _heatBuffer = 0f;
        signage = SignageState.Stop;
    }

    public float GetDangerScore()
    {
        float score = 0f;
        if (temperature > 60f) score += (temperature - 60f) * 2.0f;
        if (seismicLevel > 4f) score += (seismicLevel - 4f) * 50f;
        if (isBlocked) score += 1000f;
        if (isStairNode && seismicLevel > 3f) score += seismicLevel * 20f;
        return score;
    }
}