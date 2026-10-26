"""Test-only environment wrapper; never prints or persists DB credentials."""
import os,sys
import pytest
from sqlalchemy.engine import make_url
os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
sys.exit(pytest.main(['/tests/control/test_control.py','-q','--tb=short','-p','no:cacheprovider']))
