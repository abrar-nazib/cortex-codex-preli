"""Normalizer service package.

Public surface:
    app.main             FastAPI app entrypoint
    app.pipeline         orchestrator
    app.stage1_cleaner   Stage 1 (LLM)
    app.stage2_investigator  Stage 2 (LLM verdict + code-computed signals)
    app.stage3_reasoner  Stage 3 (LLM classification + drafting)
    app.stage4_safety    Stage 4 (LLM improve + mandatory code enforcement)
    app.config           constants, enums, routing tables, safety phrases
    app.schema           Pydantic models
    app.llm              provider interface + OpenRouter transport
"""
