"""Scoped planning compatibility and related controller/API regression launcher."""
import os,sys
import pytest
from sqlalchemy.engine import make_url
if __name__=='__main__':
    os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
    sys.exit(pytest.main(['/tests/test_control.py','/tests/test_publisher_gate.py','/tests/test_planning.py','/opt/ppt-service/tests/server','-q','--tb=short','-p','no:cacheprovider']))
