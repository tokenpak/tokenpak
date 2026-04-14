from .context import TokenPakContext
from .handoff import TokenPakHandoff


class TokenPakCrew:
    def __init__(self, crew, total_budget=8000):
        self.crew = crew
        self.total_budget = total_budget
        self.context = TokenPakContext(total_budget=total_budget)
        self.handoff = TokenPakHandoff(budget=total_budget // 4)

    def kickoff(self, inputs=None):
        kwargs = {"inputs": inputs} if inputs else {}
        try:
            return self.crew.kickoff(**kwargs)
        except TypeError:
            return self.crew.kickoff()

    @property
    def budget_status(self):
        return {
            "total_budget": self.total_budget,
            "handoff_budget": self.handoff.budget,
        }
