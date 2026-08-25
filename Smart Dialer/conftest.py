"""
Pytest configuration and fixtures.
"""
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Initialize database for each test session
import pytest
from app.db import init_db

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Initialize database before running tests."""
    init_db()
    yield
    # Cleanup happens automatically via SQLite file
