---
name: Standard TokenPak Value Proof
model: claude-sonnet-4-6
provider: anthropic
system: |
  You are a senior software engineer. Write clean, well-tested Python code.
  Be thorough but concise in your explanations. Include type hints.
max_tokens: 4096
---

## Turn 1: Architecture review

Explain the key differences between REST and GraphQL APIs.
Cover these aspects in detail with code examples for each:
- Request/response patterns and endpoint design
- Over-fetching and under-fetching problems
- Type safety and schema validation
- Caching strategies and cache invalidation
- Error handling patterns
- Pagination approaches

Include a concrete Python code example for both a REST endpoint
and a GraphQL resolver serving the same data: a list of users
with id, name, email, and their recent posts.

## Turn 2: Implementation

Based on your explanation above, write a complete Python FastAPI server
that implements both a REST API and a GraphQL API serving the same data.

Requirements:
- Use Pydantic models for User and Post with proper type hints
- REST: GET /users, GET /users/{id}, GET /users/{id}/posts
- GraphQL: query users, query user(id), nested posts resolution
- In-memory data store with at least 5 sample users and 10 posts
- Proper error handling (404, validation errors)
- Include CORS middleware configuration

## Turn 3: Testing

Write comprehensive pytest tests for the server you built.

Requirements:
- Use httpx.AsyncClient for async testing
- Test all REST endpoints (happy path + error cases)
- Test GraphQL queries (single user, all users, nested posts)
- Test edge cases: non-existent user, empty posts, invalid input
- Use pytest fixtures for the test client and sample data
- At least 12 test functions total
