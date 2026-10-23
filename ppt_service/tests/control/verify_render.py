"""Only rendering/transport and related controller/publication gate regressions."""
import os,sys
import pytest
from sqlalchemy.engine import make_url
if __name__=='__main__':
    os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
    sys.exit(pytest.main(['/tests/test_control.py','/tests/test_publisher_gate.py','/tests/test_render_validation.py','-q','--tb=short','-p','no:cacheprovider']))
