"""Example usage for tokenpak_agents.crewai."""

from tokenpak_agents.crewai import TokenPakContext, TokenPakCrew, TokenPakHandoff


ctx = TokenPakContext(total_budget=8000)
ctx.record_usage("researcher", 320)

crew = TokenPakCrew(agents=["researcher", "writer"], tasks=["research", "draft"], budget=8000)
print(crew.kickoff(topic="TokenPak"))

handoff = TokenPakHandoff()
wire = handoff.prepare_handoff(
    state={"topic": "TokenPak", "status": "researched"},
    from_agent="researcher",
    to_agent="writer",
    what_was_done="researched topic",
    whats_next="write draft",
)

print(handoff.receive_handoff_wire(wire)["prompt"])
