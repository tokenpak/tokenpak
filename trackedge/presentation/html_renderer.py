"""
TrackEdge Presentation Layer — HTML renderer for race output.
"""
import os
from typing import List, Dict, Optional


def render_html(
    races: List[Dict],
    output_path: str = "output/betting_slip.html",
    title: str = "TrackEdge — Race Analysis",
) -> str:
    """
    Render race data to an HTML betting slip.

    Args:
        races: List of race dicts with horses and scores.
        output_path: File path for the generated HTML.
        title: Page title for the HTML output.

    Returns:
        The output_path where the file was written.
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    rows = []
    for race in races:
        race_name = race.get("name", race.get("race_name", "Unknown Race"))
        horses = race.get("horses", [])
        rows.append(f"<h2>{race_name}</h2>")
        if horses:
            rows.append("<table border='1' cellpadding='4'><tr><th>Horse</th><th>Power Score</th><th>Win Prob</th></tr>")
            for h in horses:
                name = h.get("name", "?")
                score = h.get("power_score", h.get("score", "—"))
                prob = h.get("win_probability", h.get("probability", "—"))
                if isinstance(prob, float):
                    prob = f"{prob:.1%}"
                rows.append(f"<tr><td>{name}</td><td>{score}</td><td>{prob}</td></tr>")
            rows.append("</table>")
        else:
            rows.append("<p><em>No horse data.</em></p>")

    body = "\n".join(rows) if rows else "<p>No race data provided.</p>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: sans-serif; padding: 1rem; }}
    h1 {{ color: #333; }}
    h2 {{ color: #555; margin-top: 1.5rem; }}
    table {{ border-collapse: collapse; margin-bottom: 1rem; }}
    th {{ background: #444; color: #fff; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {body}
</body>
</html>
"""

    with open(output_path, "w") as f:
        f.write(html)

    return output_path
