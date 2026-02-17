"""
Setup script for BigQuery data warehouse
Run this once to initialize the BigQuery dataset and tables
"""
import asyncio
import logging
import os
from bigquery_utils import setup_bigquery_schema

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def main():
    """Main setup function"""
    project_id = os.getenv("GCP_PROJECT", "arquitetodadivulgacao")
    
    logger.info(f"Starting BigQuery setup for project: {project_id}")
    logger.info("This will create:")
    logger.info("  - Dataset: eixa")
    logger.info("  - Tables: user_interactions, tasks, emotional_memories, projects, routines")
    
    try:
        await setup_bigquery_schema(project_id)
        logger.info("✅ BigQuery setup complete!")
        logger.info("Next steps:")
        logger.info("1. Update main.py to initialize BigQuery on startup")
        logger.info("2. Update eixa_orchestrator.py to log interactions")
        logger.info("3. Deploy to Cloud Run")
    except Exception as e:
        logger.error(f"❌ Setup failed: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
