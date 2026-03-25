---
name: pipeline-worker
description: Backend Kotlin/Spring Boot worker for pipeline implementation features
---

# Pipeline Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for all pipeline implementation features: domain models, port interfaces, adapter implementations, agents, orchestrator, controllers, configuration, database migrations, and tests.

## Work Procedure

### 1. Read Context

- Read `AGENTS.md` for conventions and boundaries
- Read `.factory/library/architecture.md` for package structure
- Read the tech spec at `.docs/specs/multi-agent-pipeline-tech-spec-v2.md` for detailed design
- Read existing code patterns: `UserDao.kt`, `UserRepositoryDB.kt`, `SupabaseProperties.kt`, `UserApiImpl.kt` for reference

### 2. Write Tests First (Red)

For every component you implement:
1. Create the test file FIRST (`*Test.kt` for unit, `*IT.kt` for integration)
2. Write test cases covering the expected behavior from the feature description
3. Use `MockK` for mocking, `TestContainers` for DB, `@AutoConfigureWireMock` for HTTP
4. For LLM-dependent tests, use `MockLlmAdapter` with fixture JSON files
5. Verify tests compile but fail (red phase)

### 3. Implement (Green)

1. Create Kotlin source files following package structure from `architecture.md`
2. Follow naming conventions from AGENTS.md strictly
3. Implement until all tests pass
4. For adapters: implement the port interface, mark class with `@Component`/`@Repository`
5. For config: use `@ConstructorBinding` + `@ConfigurationProperties`
6. For jOOQ: follow `UserDao`/`UserRepositoryDB` pattern (separate Dao + Repository)

### 4. Verify

1. Run `cd stayposted-backend && ./gradlew compileKotlin compileTestKotlin` — must pass
2. Run `cd stayposted-backend && ./gradlew test` — all tests must pass (existing + new)
3. Run `cd stayposted-backend && ./gradlew ktlintCheck` — must pass (fix with `ktlintFormat` if needed)
4. Review your own code: check for missing `@Timed` on controllers, missing error handling, leaked secrets

### 5. Fixture Files (when applicable)

For features involving LLM agents, create fixture JSON files in `src/test/resources/fixtures/llm/`:
- Each fixture has `{"scenario": "...", "output": {...}}` format
- Output must match the exact data class structure (camelCase field names)
- Create at least a success fixture for each agent

## Example Handoff

```json
{
  "salientSummary": "Implemented PlannerAgent, ResearcherAgent, CriticAgent, ComposerAgent with PromptLoader. Each agent loads its system prompt from classpath, constructs a user prompt from input, and invokes LlmPort. Created 4 prompt .txt files and 4 success fixture JSONs. All 12 unit tests pass via MockLlmAdapter.",
  "whatWasImplemented": "Four pipeline agents (PlannerAgent, ResearcherAgent, CriticAgent, ComposerAgent) in io.stayposted.pipeline.domain.agent package, each implementing the pattern: load prompt → build user prompt → invoke LlmPort → return typed output. PromptLoader component with ConcurrentHashMap caching. Prompt text files in src/main/resources/prompts/. Fixture JSONs in src/test/resources/fixtures/llm/.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "cd stayposted-backend && ./gradlew compileKotlin compileTestKotlin", "exitCode": 0, "observation": "Compilation successful including new agent classes and tests"},
      {"command": "cd stayposted-backend && ./gradlew test", "exitCode": 0, "observation": "All 28 tests pass (16 existing + 12 new agent tests)"},
      {"command": "cd stayposted-backend && ./gradlew ktlintCheck", "exitCode": 0, "observation": "No lint violations"}
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": [
      {"file": "src/test/kotlin/io/stayposted/pipeline/domain/agent/PlannerAgentTest.kt", "cases": [
        {"name": "should produce PlannerOutput when given valid input", "verifies": "Agent constructs prompt and parses LLM response"},
        {"name": "should include previous brief in prompt when available", "verifies": "Previous brief TL;DR and delta included in user prompt"},
        {"name": "should work without previous brief", "verifies": "First-run scenario with null previousBrief"}
      ]},
      {"file": "src/test/kotlin/io/stayposted/pipeline/domain/agent/PromptLoaderTest.kt", "cases": [
        {"name": "should load prompt from classpath", "verifies": "Prompt text loaded correctly"},
        {"name": "should cache loaded prompts", "verifies": "Second call returns same instance"},
        {"name": "should throw when prompt file missing", "verifies": "IllegalArgumentException for unknown agent"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- A port interface you need doesn't exist yet and isn't part of your feature
- The OpenAPI code generation produces compilation errors you can't resolve
- An existing test is broken before your changes (pre-existing failure)
- The jOOQ generated classes don't include the new tables (Flyway migration needs to run first)
- You need clarity on a domain model field or behavior not specified in the tech spec
