"""Logging configuration for ContractOS.

Provides structured logging for the application.
"""

import logging
import sys


def setup_logging(environment: str = "development") -> None:
    """Configure application logging.

    Args:
        environment: 'development' or 'production'
    """
    level = logging.DEBUG if environment == "development" else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Suppress noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger instance."""
    return logging.getLogger(name)
