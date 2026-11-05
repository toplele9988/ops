import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from console.operator.model import AiTask, ModelUnavailable
from console.v3_operator_api import build


def app_for(system):
    app=Starlette(routes=build(SimpleNamespace(error_response=lambda req,code:JSONResponse({'error':'forbidden'},status_code=code)),service=system.op))
    async def host_security(request, call_next):
        request.state.password_principal=SimpleNamespace(username='fictional-owner') if request.headers.get('X-Login')=='yes' else None
        request.state.session=SimpleNamespace(authenticated=True)
        request.state.password_recheck=lambda:request.headers.get('X-Recheck','yes')=='yes'
        if request.method=='POST' and request.headers.get('X-CSRF-Token')!='fictional-csrf':
            return JSONResponse({'error':'csrf'},status_code=403)
        return await call_next(request)
    from starlette.middleware.base import BaseHTTPMiddleware
    app.add_middleware(BaseHTTPMiddleware,dispatch=host_security)
    return app


def test_authenticated_api_csrf_and_idempotent_approval(system):
    s=system
    row=next(a for a in s.op.handle(s.event()) if a['kind']=='reply_customer')
    with TestClient(app_for(s)) as client:
        assert client.get('/api/v1/operator').status_code==403
        h={'X-Login':'yes','X-CSRF-Token':'fictional-csrf'}
        assert client.get('/api/v1/operator',headers=h).status_code==200
        assert client.get('/api/v1/operator',headers={**h,'X-Recheck':'no'}).status_code==403
        assert client.post('/api/v1/operator/settings',data={}).status_code==403
        endpoint='/api/v1/operator/settings'
        for i,(key,value) in enumerate((('enabled','1'),('dry_run','0'))):
            assert client.post(endpoint,headers=h,data=dict(scope='master',key=key,enabled=value,operation_key=f'api-setting-{i}')).status_code==200
        body=dict(operation_key='api-approve-1')
        endpoint=f"/api/v1/operator/actions/{row['id']}/approve"
        assert client.post(endpoint,headers=h,data=body).status_code==200
        assert client.post(endpoint,headers=h,data=body).status_code==200
        assert len(s.bridge.calls)==1
        assert client.post(endpoint,headers=h,json=body).status_code==400
        assert client.get('/api/v1/operator?unknown=1',headers=h).status_code==400
        result=client.get('/api/v1/operator',headers=h)
        assert result.headers['Cache-Control']=='no-store'


def test_model_entry_stdin_free_pool_and_metadata_only(monkeypatch):
    calls=[]
    def run(command,**kw):
        calls.append((command,kw))
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='ok',task_id='fake-task',provider='dashscope',
            requested_model='qwen-fictional',actual_model='qwen-fictional',usage={'total_tokens':10},
            answer='{"intent":"inquiry","fact_ids":[]}')))
    model=AiTask(runner=run)
    result=model.decide(dict(messages=[{'text':'FICTIONAL INPUT'}]))
    assert calls[0][0]==['ai-task','run','bailian_free','--model','auto']
    assert 'FICTIONAL INPUT' not in str(calls[0][0]) and 'FICTIONAL INPUT' in calls[0][1]['input']
    assert 'shell' not in calls[0][1] and result['model']['actual_model']=='qwen-fictional'
    model.decide({},difficult=True)
    assert calls[-1][0][-1]=='auto'  # free pool only accepts auto


@pytest.mark.parametrize('mode',['unknown','timeout','bad_json','refusal'])
def test_model_unknown_no_retry(mode):
    calls=[]
    def run(*args,**kw):
        calls.append(args)
        if mode=='timeout':raise TimeoutError()
        return SimpleNamespace(returncode=1 if mode=='refusal' else 0,stdout='not-json' if mode=='bad_json' else '{"status":"failed","error":"unknown"}')
    with pytest.raises(ModelUnavailable):AiTask(runner=run).decide({})
    assert len(calls)==1


def test_duplicate_field_rejected(system):
    with TestClient(app_for(system)) as client:
        response=client.post('/api/v1/operator/settings',headers={'X-Login':'yes','X-CSRF-Token':'fictional-csrf','Content-Type':'application/x-www-form-urlencoded'},
            content='scope=master&key=enabled&enabled=1&enabled=0&operation_key=duplicate-fields')
        assert response.status_code==400


def test_gateway_runner_calls_gateway_directly_as_imgw(monkeypatch):
    from console import ai_gateway
    seen = []
    monkeypatch.setattr(ai_gateway, '_is_imgw', lambda: True)
    monkeypatch.setattr(ai_gateway.subprocess, 'run', lambda args, **kw: seen.append((args, kw)) or 'done')
    assert ai_gateway.run(['ai-task', 'run', 'bailian_free', '--model', 'auto'], input='FICTIONAL', timeout=5) == 'done'
    args, kw = seen[0]
    assert args == [ai_gateway.GATEWAY]
    assert json.loads(kw['input']) == dict(action='run', provider='bailian_free', model='auto', task='FICTIONAL')
    monkeypatch.setattr(ai_gateway, '_is_imgw', lambda: False)
    ai_gateway.run(['ai-task', 'run', 'bailian_free', '--model', 'auto'], input='FICTIONAL', timeout=5)
    assert seen[1][0][0] == 'ai-task'


def test_answer_in_code_block_is_parsed():
    def run(args, **kw):
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(status='ok', actual_model='qwen-fictional',
            answer='```json\n{"intent":"routine","fact_ids":[]}\n```')))
    assert AiTask(runner=run).decide({})['plan'] == dict(intent='routine', fact_ids=[])
