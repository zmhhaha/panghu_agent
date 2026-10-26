"""Container-only test launcher: changes database name without displaying credentials."""
import os,sys
from sqlalchemy.engine import make_url
import pytest
if __name__=='__main__':
    os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
    sys.exit(pytest.main(['-q','/opt/ppt-service/tests/server','--tb=short']))
