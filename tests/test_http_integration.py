"""Actual HTTP against the checked-in Postman simulation; run with POSTMAN_SIM_URL."""
import os
import time
import pytest
from src.config import Settings
from src.http_client import CachedClient, BackoffError
from src.parse import parse_result

BASE=os.getenv('POSTMAN_SIM_URL')
pytestmark=pytest.mark.skipif(not BASE,reason='Start Postman simulation and set POSTMAN_SIM_URL')

def client(tmp_path):
    return CachedClient(Settings(contact='test@example.com',data_dir=tmp_path))

def test_actual_http_conditional_cache_and_parser(tmp_path):
    c=client(tmp_path)
    url=BASE+'/oficial/ele2026/fixture/dados/br/result.json'
    first=c.fetch(url)
    second=c.fetch(url)
    assert first.status==200 and second.status==304
    assert first.payload_hash==second.payload_hash
    assert first.changed and not second.changed and second.bytes_received==0
    assert parse_result(second.payload)[1]['valid_votes']==335743
    assert len(list((tmp_path/'cache').glob('*.json')))==2

@pytest.mark.parametrize('code',[403,429,500])
def test_actual_html_error_backoff_blocks_other_paths(tmp_path,code):
    c=client(tmp_path)
    c.session.headers['x-mock-response-code']=str(code)
    with pytest.raises(BackoffError) as exc:
        c.fetch(BASE+'/oficial/error')
    assert exc.value.status==code
    assert exc.value.retry_at-time.monotonic()>=6.5
    if code==403: assert exc.value.retry_at-time.monotonic()>599
    requests=c.requests_total
    with pytest.raises(BackoffError): c.fetch(BASE+'/oficial/other')
    assert c.requests_total==requests

def test_actual_retry_after_recovers(tmp_path):
    c=client(tmp_path)
    c.session.headers['x-mock-response-code']='500'
    with pytest.raises(BackoffError) as exc: c.fetch(BASE+'/oficial/recovery')
    wait=max(0,exc.value.retry_at-time.monotonic())
    del c.session.headers['x-mock-response-code']
    time.sleep(wait+.05)
    assert c.fetch(BASE+'/oficial/recovery').status==200

def test_connection_failure_enters_cooldown(tmp_path):
    c=client(tmp_path)
    with pytest.raises(BackoffError): c.fetch('http://127.0.0.1:1/unreachable')
    requests=c.requests_total
    with pytest.raises(BackoffError): c.fetch('http://127.0.0.1:1/another')
    assert c.requests_total==requests
