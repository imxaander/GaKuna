using UnityEngine;

public class StatsDisplay : MonoBehaviour
{
    public EvacuationAgent agent;
    public AgentVisualizer visualizer;

    private GUIStyle boxStyle;
    private GUIStyle titleStyle;
    private GUIStyle goodStyle;
    private GUIStyle badStyle;
    private GUIStyle normalStyle;
    private bool stylesInitialized = false;

    void InitStyles()
    {
        if (stylesInitialized) return;

        boxStyle = new GUIStyle(GUI.skin.box);
        boxStyle.normal.background = MakeTex(2, 2, new Color(0f, 0f, 0f, 0.8f));

        titleStyle = new GUIStyle(GUI.skin.label);
        titleStyle.fontSize = 14;
        titleStyle.fontStyle = FontStyle.Bold;
        titleStyle.normal.textColor = Color.cyan;

        goodStyle = new GUIStyle(GUI.skin.label);
        goodStyle.fontSize = 12;
        goodStyle.normal.textColor = Color.green;

        badStyle = new GUIStyle(GUI.skin.label);
        badStyle.fontSize = 12;
        badStyle.normal.textColor = Color.red;

        normalStyle = new GUIStyle(GUI.skin.label);
        normalStyle.fontSize = 12;
        normalStyle.normal.textColor = Color.white;

        stylesInitialized = true;
    }

    void OnGUI()
    {
        InitStyles();

        // ── SIGNAGE STATE PANEL ──────────────────────
        GUILayout.BeginArea(new Rect(10, 10, 260, 300), boxStyle);
        GUILayout.Label("GAKUNA — NODE SIGNAGE", titleStyle);
        GUILayout.Space(4);

        if (agent != null && agent.nodes != null)
        {
            foreach (var node in agent.nodes)
            {
                string sign = node.signage.ToString();
                string temp = node.temperature > 0 ? $"{node.temperature:F1}°C" : "--";
                string blocked = node.isBlocked ? " [BLOCKED]" : "";
                string floor = $"F{node.floor}";

                // Color code by sign
                GUIStyle style = sign == "Stop" ? badStyle :
                                 sign == "Left" || sign == "Right" ? goodStyle :
                                 normalStyle;

                string arrow = sign == "Left" ? "←" :
                               sign == "Right" ? "→" :
                               sign == "Stop" ? "✕" : "○";

                GUILayout.Label(
                    $"[{floor}] Node{node.nodeID}: {arrow} {sign} | {temp}{blocked}",
                    style
                );
            }
        }

        GUILayout.EndArea();

        // ── VISUALIZER STATS PANEL ───────────────────
        if (visualizer != null)
        {
            GUILayout.BeginArea(new Rect(10, 320, 260, 200), boxStyle);
            GUILayout.Label("AGENT SIMULATION", titleStyle);
            GUILayout.Space(4);

            float successRate = visualizer.totalRuns > 0
                ? (float)visualizer.successfulRuns / visualizer.totalRuns * 100f
                : 0f;

            GUILayout.Label($"Total Runs:    {visualizer.totalRuns}", normalStyle);

            GUIStyle rateStyle = successRate >= 80 ? goodStyle :
                                 successRate >= 50 ? normalStyle : badStyle;
            GUILayout.Label($"Success Rate:  {successRate:F1}%", rateStyle);
            GUILayout.Label($"Last Path:     {visualizer.lastPathLength} nodes", normalStyle);

            GUIStyle resultStyle = visualizer.lastResult.Contains("EXIT") ? goodStyle :
                                   visualizer.lastResult.Contains("FAILED") ? badStyle :
                                   normalStyle;
            GUILayout.Label($"Last Result:   {visualizer.lastResult}", resultStyle);
            GUILayout.Label($"Reading:       {visualizer.currentSign}", normalStyle);

            GUILayout.EndArea();
        }

        // ── FIRE SOURCE PANEL ────────────────────────
        GUILayout.BeginArea(new Rect(10, 530, 260, 100), boxStyle);
        GUILayout.Label("ENVIRONMENT", titleStyle);

        if (agent != null)
        {
            FireSource fire = agent.fireSource;
            if (fire != null)
            {
                GUILayout.Label(
                    $"Fire Pos:  {fire.transform.position}",
                    normalStyle
                );
                GUILayout.Label(
                    $"Intensity: {fire.intensity:F0}",
                    fire.intensity > 600 ? badStyle : normalStyle
                );
            }
        }

        GUILayout.EndArea();
    }

    Texture2D MakeTex(int width, int height, Color col)
    {
        Color[] pix = new Color[width * height];
        for (int i = 0; i < pix.Length; i++) pix[i] = col;
        Texture2D result = new Texture2D(width, height);
        result.SetPixels(pix);
        result.Apply();
        return result;
    }
}
