"""Run only controller and publication-gate regression tests, not API/materials suites."""
import os,sys
import pytest
from sqlalchemy.engine import make_url
if __name__=='__main__':
    os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
    sys.exit(pytest.main(['/tests/test_control.py','/tests/test_publisher_gate.py','-q','--tb=short','-p','no:cacheprovider']))
