"""Example: CrewAI crew with TokenPak context management."""

from tokenpak_agents.crewai import TokenPakContext, TokenPakHandoff, TokenPakCrew

# Example: Research + Writing workflow

# Mock agent definitions
agents = [
    {"name": "researcher", "role": "Research Agent"},
    {"name": "writer", "role": "Writing Agent"},
]

# Mock task definitions
tasks = [
    {"id": "research_task", "agent": "researcher"},
    {"id": "write_task", "agent": "writer"},
]

# Create crew with TokenPak
crew = TokenPakCrew(
    agents=agents,
    tasks=tasks,
    context_budget=8000,
    compaction_mode="balanced",
    verbose=True,
)

# Execute crew
result = crew.kickoff()
print(f"Crew execution result: {result['status']}")
print(f"Context used: {result['context_used']} tokens")
