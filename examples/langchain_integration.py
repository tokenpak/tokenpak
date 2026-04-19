#!/usr/bin/env python3
"""
LangChain Integration

What this example shows:
- Using TokenPak with LangChain agents
- Routing LLM calls through the proxy
- Building chains and agents with caching
- Integration with LangChain tooling

When to use this:
- Building AI agents with LangChain
- Optimizing costs for complex chains
- Using LangChain with caching benefits
"""

import os
from datetime import datetime, timezone
import json


def main():
    """Demonstrate LangChain integration with TokenPak."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    
    if not api_key:
        api_key = "sk-test"
    
    print("=" * 60)
    print("LANGCHAIN INTEGRATION")
    print("=" * 60)
    print()
    
    print("TokenPak works seamlessly with LangChain by redirecting requests")
    print("to the proxy while maintaining full LangChain functionality.")
    print()
    
    print("=" * 60)
    print("Example 1: Simple LLM Chain")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
from langchain_anthropic import ChatAnthropic
from langchain.prompts import ChatPromptTemplate

# Create LLM with TokenPak proxy
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    api_key="sk-...",
    # Note: TokenPak doesn't need explicit proxy config for v1/chat/completions
)

# Create a simple chain
prompt = ChatPromptTemplate.from_messages([
    ("human", "What is {topic}?")
])

chain = prompt | llm

# Run the chain
result = chain.invoke({"topic": "machine learning"})
print(result.content)
    """)
    
    print()
    print("=" * 60)
    print("Example 2: Agent with Tools")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.tools import tool
from langchain.prompts import ChatPromptTemplate

# Define tools
@tool
def get_weather(location: str) -> str:
    '''Get the current weather for a location.'''
    return f"The weather in {location} is sunny."

@tool
def calculate(expression: str) -> str:
    '''Evaluate a mathematical expression.'''
    return str(eval(expression))

tools = [get_weather, calculate]

# Create LLM
llm = ChatAnthropic(model="claude-sonnet-4-6", api_key="sk-...")

# Create agent
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("human", "{input}"),
])

agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Run agent
result = executor.invoke({
    "input": "What's the weather in NYC and what's 5+3?"
})
    """)
    
    print()
    print("=" * 60)
    print("Example 3: Retrieval-Augmented Generation (RAG)")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
from langchain.vectorstores import FAISS
from langchain.embeddings import OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain.chains import RetrievalQA

# Create embeddings
embeddings = OpenAIEmbeddings()

# Create vector store (with your documents)
vectorstore = FAISS.from_documents(documents, embeddings)

# Create retriever
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

# Create LLM
llm = ChatAnthropic(model="claude-sonnet-4-6", api_key="sk-...")

# Create RAG chain
rag = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    retriever=retriever,
)

# Query
answer = rag.run("What does TokenPak do?")
    """)
    
    print()
    print("=" * 60)
    print("Cost Optimization with TokenPak + LangChain")
    print("=" * 60)
    print()
    
    print("Scenario: Running 100 AI agent tasks with shared context")
    print()
    
    # Simulate costs
    shared_context_tokens = 5000  # Documents, system prompt
    per_task_tokens = 500         # User input + agent response
    tasks = 100
    
    # Pricing
    price_input = 0.003 / 1000
    price_output = 0.015 / 1000
    price_cache_read = 0.0003 / 1000
    
    # Without caching
    cost_no_cache = (shared_context_tokens + per_task_tokens) * price_input * tasks + (per_task_tokens * 0.2) * price_output * tasks
    
    # With caching (load context once, reuse 99 times)
    cost_with_cache = (
        (shared_context_tokens + per_task_tokens) * price_input +  # First task
        shared_context_tokens * price_cache_read * (tasks - 1) +    # Cached reads
        per_task_tokens * price_input * (tasks - 1) +               # New input per task
        (per_task_tokens * 0.2) * price_output * tasks              # Output
    )
    
    print(f"Without caching:  ${cost_no_cache:.2f}")
    print(f"With caching:     ${cost_with_cache:.2f}")
    print(f"Savings:          ${cost_no_cache - cost_with_cache:.2f} ({100*(cost_no_cache - cost_with_cache)/cost_no_cache:.1f}%)")
    print()
    
    print("Strategy: Load shared documents/context once, reuse across tasks")
    print()
    
    print("=" * 60)
    print("Best Practices")
    print("=" * 60)
    print()
    print("1. Create LLM once, reuse across chains")
    print("   ❌ Don't: Create new ChatAnthropic() for each chain")
    print("   ✅ Do:   llm = ChatAnthropic(...); chain1 | llm; chain2 | llm")
    print()
    print("2. Structure agents for cache hits")
    print("   ❌ Avoid: Including full document in every message")
    print("   ✅ Use:  Load document once, reference by ID")
    print()
    print("3. Monitor token usage")
    print("   • Use TokenPak dashboard to track LangChain costs")
    print("   • Compare with direct API spending")
    print()
    print("4. Batch similar requests")
    print("   ✅ Process 10 similar queries (cache benefits)")
    print("   ❌ Mix unrelated queries (less cache reuse)")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
