import json
from types import SimpleNamespace
import pytest
from ppt_service.control.chart_oracle import ChartOracle
from ppt_service.control.execution import ControlError

def test_default_chart_free_expectation_cannot_be_changed_by_agent():
    a=SimpleNamespace(snapshot={'hash':'a'*64,'assets':[], 'expected_charts':[{'invented':True}]})
    assert ChartOracle()(a)=={'expected_charts':[], 'source_sha256':[]}

def test_reviewed_oracle_binds_input_and_authorized_sources(tmp_path):
    entry={'expected_charts':[{'series':[1,2,3]}], 'source_sha256':['b'*64]}
    path=tmp_path/'oracle.json'
    path.write_text(json.dumps({'schema_version':1,'snapshots':{'a'*64:entry}}))
    oracle=ChartOracle(path)
    a=SimpleNamespace(snapshot={'hash':'a'*64,'assets':[{'sha256':'b'*64}]})
    assert oracle(a)==entry
    oracle(a)['expected_charts'].clear()
    assert oracle(a)==entry
    a.snapshot['assets']=[]
    with pytest.raises(ControlError,match='oracle_source_mismatch'):oracle(a)
    a.snapshot['hash']='c'*64
    assert oracle(a)['expected_charts']==[]

@pytest.mark.parametrize('entry',[
    {'expected_charts':[{}],'source_sha256':[]},
    {'expected_charts':[],'source_sha256':['not-a-hash']},
    {'expected_charts':[],'source_sha256':['b'*64,'b'*64]},
])
def test_invalid_deployment_oracle_rejected(tmp_path,entry):
    path=tmp_path/'oracle.json'
    path.write_text(json.dumps({'schema_version':1,'snapshots':{'a'*64:entry}}))
    with pytest.raises(ControlError):ChartOracle(path)
