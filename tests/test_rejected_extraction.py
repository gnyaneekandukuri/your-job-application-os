from threading import RLock
from types import SimpleNamespace
import pytest
from app.job_requirements import extract, key_for, source_hash, ExtractRequirements
from app.retrieval import RetrievalError


@pytest.mark.parametrize('error,cleared',[
    ('Credits exhausted. (HTTP 429).',True),
    ('API key rejected. (HTTP 401).',True),
    ('Connection failed; billing unknown.',False),
])
def test_only_definite_rejections_allow_an_explicit_retry(error,cleared):
    data={};calls=[]
    def paid(*args):
        calls.append(1)
        raise RetrievalError(error,502)
    engine=SimpleNamespace(lock=RLock(),get=data.get,put=lambda k,v:data.update({k:v}),paid=paid)
    job=SimpleNamespace(id='test',description='Build a data platform.')
    with pytest.raises(RetrievalError):
        extract(engine,job,ExtractRequirements(consent_to_send=True,source_sha256=source_hash(job)))
    assert len(calls)==1
    assert (data[key_for(job)+':attempt'] is None)==cleared
