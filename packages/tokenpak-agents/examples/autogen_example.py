"""Example usage for tokenpak_agents.autogen."""

from tokenpak_agents.autogen import TokenPakAssistant, TokenPakGroupChat


alice = TokenPakAssistant(name="alice", budget=4000)
bob = TokenPakAssistant(name="bob", budget=4000)

alice.receive_message("Please research memory compression.", sender_name="user")
wire = alice.prepare_handoff(to_agent="bob", what_was_done="researched", whats_next="summarize")
bob.apply_handoff_wire(wire)

chat = TokenPakGroupChat(agents=[alice, bob], budget=8000)
chat.add_message({"role": "alice", "content": "research complete"})
chat.add_message({"role": "bob", "content": "preparing summary"})

print(bob.generate_reply())
print(chat._compress_history())
