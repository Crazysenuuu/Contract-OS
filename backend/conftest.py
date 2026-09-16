"""Pytest fixtures for ContractOS tests."""
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.group_by.return_value.all.return_value = []
    return db


@pytest.fixture
def sample_translation_item():
    """Sample translation queue item for testing."""
    return {
        "id": "test-item-123",
        "source_type": "agreement",
        "source_id": "agreement-456",
        "target_language": "si",
        "source_language": "en",
        "source_content": "This is a confidentiality agreement.",
        "source_title": "Test Agreement",
        "status": "pending",
        "priority": "normal",
        "source": "auto",
        "attempts": 0,
        "queued_at": datetime.utcnow(),
    }


@pytest.fixture
def sample_language():
    """Sample language for testing."""
    return {
        "code": "si",
        "name": "Sinhala",
        "native_name": "සිංහල",
        "locale": "si-LK",
        "direction": "ltr",
        "currency": "LKR",
    }


@pytest.fixture
def sample_glossary_term():
    """Sample glossary term for testing."""
    return {
        "source_term": "Confidentiality",
        "translations": {
            "si": "රහස්‍යභාවය",
            "ta": "இரகசியம்",
            "zh": "保密",
        },
        "category": "legal",
        "definition": "The state of keeping information secret",
    }


# Integration test fixtures are in tests/conftest.py
