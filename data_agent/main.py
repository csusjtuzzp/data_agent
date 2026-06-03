# Copyright (c) Data Agent Team. All rights reserved.
"""Main entry point for the Data Agent service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from data_agent.agent.main_agent import MainAgent
from data_agent.agent.planner import LLMTaskPlanner, OpenAILLMClient
from data_agent.agent.resource_monitor import ResourceMonitor
from data_agent.agent.sub_agents import DocumentParser, QualityValidator
from data_agent.api.routes import router, set_main_agent
from data_agent.config import load_config, AgentConfig, MinerUConfig, LLMConfig
from data_agent.integration import BackendSelector, MinerUClient
from data_agent.skills import SkillRegistry, ParseSkill, FilterSkill, SkillConfig
from data_agent.utils.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Data Agent service...")

    config = load_config()
    agent_config = config["agent"]
    mineru_config: MinerUConfig = config["mineru"]
    llm_config: LLMConfig = config["llm"]

    # Initialize LLM planner
    if not llm_config.api_key or llm_config.provider != "openai":
        raise ValueError("LLM configuration (openai) is required for task planning")

    llm_client = OpenAILLMClient(
        api_key=llm_config.api_key,
        model=llm_config.model,
        api_base=llm_config.api_base or "https://api.openai.com/v1",
    )
    llm_planner = LLMTaskPlanner(llm_client=llm_client, enable_llm=True)
    logger.info(f"LLM Planning enabled: {llm_config.model}")

    # Initialize core components
    backend_selector = BackendSelector()
    mineru_client = MinerUClient(api_url=mineru_config.api_url)
    resource_monitor = ResourceMonitor()

    # Initialize skills (only what's needed for parse -> quality)
    skill_registry = SkillRegistry()
    skill_registry.register(ParseSkill(SkillConfig(name="parse_skill")))
    skill_registry.register(FilterSkill(SkillConfig(name="filter_skill")))

    # Initialize sub-agents
    sub_agents = {
        "document_parser": DocumentParser(
            skill_registry=skill_registry,
            mineru_client=mineru_client,
            backend_selector=backend_selector,
        ),
        "quality_validator": QualityValidator(skill_registry=skill_registry),
    }

    # Initialize main agent
    main_agent = MainAgent(
        sub_agents=sub_agents,
        skill_registry=skill_registry,
        resource_monitor=resource_monitor,
        llm_planner=llm_planner,
    )

    # Set main agent for API routes
    set_main_agent(main_agent)

    logger.info("Data Agent service started successfully")
    logger.info(f"  - Max Concurrency: {agent_config.max_concurrency}")

    yield

    logger.info("Shutting down Data Agent service...")
    await mineru_client.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = load_config()
    configure_logging(level=config["server"].log_level)

    app = FastAPI(
        title="Data Agent",
        description="Document Understanding System: parse -> quality workflow",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


def main() -> None:
    """Main entry point."""
    config = load_config()
    configure_logging(level=config["server"].log_level)

    import uvicorn
    app = create_app()
    uvicorn.run(
        app,
        host=config["server"].host,
        port=config["server"].port,
        log_level=config["server"].log_level.lower(),
    )


if __name__ == "__main__":
    main()