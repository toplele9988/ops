import asyncio
import json
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from fixtures import Fixture

try:
    from console import v3_quotes_api as api
    from starlette.responses import JSONResponse
    from starlette.datastructures import QueryParams
except ModuleNotFoundError as error:
    if error.name != 'starlette' and not (error.name or '').startswith('starlette.'):
        raise
    api=None


def request(*,fields=None,form=None,body=b'',query=None,ids=None,principal=True,authenticated=True,recheck=True,headers=None):
    async def read_body():
        return body
    if headers is None:
        headers={'content-length':str(len(urlencode(fields if fields is not None else form,doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated),password_recheck=lambda:recheck,
        form={k:[v] for k,v in fields.items()} if fields is not None else form),
        path_params=ids or {},query_params=query or {},headers=headers,body=read_body)


@unittest.skipIf(api is None,'starlette not installed')
class ApiTests(Fixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app=SimpleNamespace(error_response=lambda req,code:JSONResponse({'error':'请先完成身份认证'},status_code=code))
        self.routes=api.build(self.app,service=self.quotes)
        self.handlers={(r.path.removeprefix('/api/v1/'),'POST' if 'POST' in r.methods else 'GET'):r.endpoint for r in self.routes}

    def call(self,path,method='GET',**kw):
        return asyncio.run(self.handlers[path,method](request(**kw)))

    def data(self,path,method='GET',**kw):
        r=self.call(path,method,**kw)
        self.assertEqual(r.status_code,200,r.body.decode())
        self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertEqual(r.headers['x-content-type-options'],'nosniff')
        return json.loads(r.body)

    def test_all_routes_auth_post_recheck_before_write_and_threadpool(self):
        self.assertEqual(len(self.routes),22)
        for route in self.routes:
            for denied in ({'principal':False},{'authenticated':False},{'recheck':False}):
                r=asyncio.run(route.endpoint(request(ids={'id':'1','cid':'1','lid':'1'},fields={},**denied)))
                self.assertEqual(r.status_code,403,route.path)
                self.assertEqual(r.headers['cache-control'],'no-store')
                self.assertEqual(r.headers['x-content-type-options'],'nosniff')
        self.assertFalse(self.path.exists())
        req=request(ids={'id':'1'},recheck=False)
        async def no_body():
            self.fail('body before recheck')
        req.body=no_body
        with patch.object(self.quotes,'write',side_effect=AssertionError('write before recheck')):
            self.assertEqual(asyncio.run(self.handlers['deals/{id}/quotes','POST'](req)).status_code,403)
        did=self.deals.write('create',None,{},'fictional-owner')['id']
        events=[]
        original=self.quotes.write
        def write(*args):
            events.append('write')
            self.assertNotEqual(threading.get_ident(),main_thread)
            return original(*args)
        main_thread=threading.get_ident()
        req=request(ids={'id':did},fields={})
        req.state.password_recheck=lambda:events.append('recheck') or True
        with patch.object(self.quotes,'write',write):
            self.assertEqual(asyncio.run(self.handlers['deals/{id}/quotes','POST'](req)).status_code,200)
        self.assertEqual(events,['recheck','write'])

    def test_forms_validation_recheck_errors_fixed_503(self):
        did,rid,*_=self.setup_round()
        ids={'id':rid}
        self.data('quotes/{id}/update','POST',ids=ids,body=urlencode({'notes':'fictional + & %'}).encode())
        req=request(ids=ids,fields={'notes':'fictional cached','csrf':'fictional-csrf'})
        async def no_body():
            self.fail('must not reread consumed stream')
        req.body=no_body
        self.assertEqual(asyncio.run(self.handlers['quotes/{id}/update','POST'](req)).status_code,200)
        for kw in ({'form':{'notes':['a','b']}},{'form':{'notes':'a'}},{'body':b'notes=\xff'},
            {'body':b'notes=a&notes=b'},{'body':b'notes='+b'x'*8192},{'fields':{'notes':'字'*2001}},
            {'fields':{'status':'sent'}},{'fields':{},'headers':{}},{'fields':{},'headers':{'content-length':'8193'}}):
            self.assertEqual(self.call('quotes/{id}/update','POST',ids=ids,**kw).status_code,400)
        for fields in ({'currency':'usd'},{'fx_rate':'0.00001'},{'target_margin':'90.01'},
                       {'target_margin':'-1'},{'valid_until':'2030-02-31'},{'incoterm':'bad'}):
            self.assertEqual(self.call('quotes/{id}/update','POST',ids=ids,fields=fields).status_code,400)
        for query in ({'lang':'bad'},QueryParams('lang=en&lang=zh'),{'unknown':'1'},
                      {'mode':'spaceship'},QueryParams('mode=air&mode=sea')):
            self.assertEqual(self.call('quotes/{id}/text',ids=ids,query=query).status_code,400)
        for query in ({'mode':'spaceship'},QueryParams('mode=air&mode=sea'),{'unknown':'1'}):
            self.assertEqual(self.call('quotes/{id}/freight-options',ids=ids,query=query).status_code,400)
        self.assertEqual(self.call('quotes/{id}',ids={'id':'-1'}).status_code,400)
        self.assertEqual(self.call('quotes/{id}',ids={'id':'9999'}).status_code,404)
        self.assertEqual(self.call('quotes/{id}',ids={'id':'-1'},recheck=False).status_code,403)
        for method,op in [('GET','read'),('POST','write')]:
            with patch.object(self.quotes,op,side_effect=RuntimeError('/fictional/private/path')):
                r=self.call('quotes/{id}' if method=='GET' else 'quotes/{id}/update',method,ids=ids,fields={})
                self.assertEqual(r.status_code,503)
                self.assertNotIn('private',r.body.decode())

    def test_freight_options_compare_choose_is_idempotent_and_logs_reason(self):
        did,rid,*_ = self.setup_round(freight=False)
        freight_id = self.freight(channel='platform_alibaba', transit_days_min='3', transit_days_max='5')
        options = self.data('quotes/{id}/freight-options', ids={'id':rid})
        item = next(option for option in options['items'] if option['id'] == freight_id)
        self.assertTrue(item['calculable'])
        self.assertEqual(item['amount_cny'], '71.29')
        comparison = self.data('freight/compare', query={'destination_country':'ZZ','mode':'air'})
        self.assertEqual(comparison['items'][0]['channel'], 'platform_alibaba')
        fields = {'confirm':'1','freight_quote_id':str(freight_id),'reason_type':'fastest',
                  'reason':'虚构时效符合交期','csrf':'fictional-host-token'}
        first = self.data('quotes/{id}/freight-options/choose', 'POST', ids={'id':rid}, fields=fields)
        second = self.data('quotes/{id}/freight-options/choose', 'POST', ids={'id':rid}, fields=fields)
        self.assertEqual(first['cost_id'], second['cost_id'])
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        detail = self.data('quotes/{id}', ids={'id':rid})
        self.assertEqual(len(detail['costs']), 1)
        self.assertEqual(detail['costs'][0]['amount'], item['amount_cny'])
        self.assertEqual(len(detail['negotiations']), 1)
        note = detail['negotiations'][0]['note']
        self.assertIn('选择了 FICTIONAL-SECRET-FORWARDER（来源 阿里国际站平台物流）', note)
        self.assertIn('原因：最快；虚构时效符合交期', note)
        self.assertNotIn('fictional-host-token', note)
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM deal_events WHERE summary LIKE '%选择了 %运价 #%' ").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM quote_costs WHERE round_id=?", (rid,)).fetchone()[0], 1)
        self.assertEqual(self.call('quotes/{id}/freight-options/choose', 'POST', ids={'id':rid},
                                   fields={'confirm':'0','freight_quote_id':str(freight_id)}).status_code, 400)
        self.assertEqual(self.call('quotes/{id}/freight-options/choose', 'POST', ids={'id':rid},
            fields={'confirm':'1','freight_quote_id':str(freight_id),'reason':'虚构'*101}).status_code, 400)

    def test_all_business_endpoints_locks_negotiation_and_fx(self):
        pid,offer=self.product()
        did=self.deals.write('create',None,{},'fictional-owner')['id']
        rid=self.data('deals/{id}/quotes','POST',ids={'id':did},fields={})['id']
        ids={'id':rid}
        lid=self.data('quotes/{id}/lines','POST',ids=ids,fields=dict(product_id=str(pid),qty='2',sell_unit_price='99.13'))['id']
        self.data('quotes/{id}/lines/{lid}/update','POST',ids=dict(ids,lid=lid),fields={'qty':'3'})
        self.data('quotes/{id}/update','POST',ids=ids,fields=dict(destination_country='ZZ'))
        freight=self.freight()
        cid=self.data('quotes/{id}/costs','POST',ids=ids,fields=dict(kind='freight',freight_quote_id=str(freight)))['id']
        self.data('quotes/{id}/costs/{cid}/recalc','POST',ids=dict(ids,cid=cid),fields={})
        self.data('quotes/{id}/update','POST',ids=ids,fields=dict(valid_until='2030-01-30',fx_rate='7.1234',target_margin='15',destination_country='ZZ'))
        self.data('quotes/{id}/negotiations','POST',ids=ids,fields=dict(side='customer',note='fictional',price='80.01',currency='EUR'))
        self.data('quotes/{id}/terms','POST',ids=ids,fields=dict(confirm='1',payment='30_70',delivery_scope='虚构交付范围'))
        self.assertTrue(self.data('quotes/{id}/text',ids=ids,query={'lang':'zh'})['ready'])
        self.assertEqual(self.data('fx')['usd_cny'],'7.1235')
        d=self.data('quotes/{id}',ids=ids)
        self.assertEqual(d['negotiations'][0]['price'],'80.01')
        self.assertEqual(d['lines'][0]['cost_unit'],'123.47')
        self.assertNotIn('_fen',json.dumps(d))
        self.assertEqual(len(self.data('deals/{id}/quotes',ids={'id':did})['items']),1)
        self.data('quotes/{id}/status','POST',ids=ids,fields=dict(action='send',confirm='1'))
        r=self.call('quotes/{id}/lines/{lid}/update','POST',ids=dict(ids,lid=lid),fields={'qty':'4'})
        self.assertEqual(r.status_code,409)
        self.assertEqual(json.loads(r.body)['error'],'这一轮已发出，请新建下一轮')
        self.data('quotes/{id}/status','POST',ids=ids,fields=dict(action='accept'))
        new=self.data('deals/{id}/quotes','POST',ids={'id':did},fields={})['id']
        d=self.data('quotes/{id}',ids={'id':new})
        self.data('quotes/{id}/costs/{cid}/remove','POST',ids={'id':new,'cid':d['costs'][0]['id']},fields={})
        self.data('quotes/{id}/lines/{lid}/remove','POST',ids={'id':new,'lid':d['lines'][0]['id']},fields={})
        self.assertFalse(self.data('quotes/{id}/text',ids={'id':new})['ready'])

    def test_builder_signature_get_read_then_recheck_and_stream_limit(self):
        routes=api.build(self.app,self.path,today=lambda:self.today,fx_path=self.fx_path,clock=lambda:1893456000)
        self.assertEqual(len(routes),22)
        events=[]
        req=request(ids={'id':'1'})
        req.state.password_recheck=lambda:events.append('recheck') or False
        with patch.object(self.quotes,'read',lambda *args:events.append('read') or {}):
            self.assertEqual(asyncio.run(self.handlers['quotes/{id}','GET'](req)).status_code,403)
        self.assertEqual(events,['read','recheck'])
        req=request(ids={'id':'1'})
        async def stream():
            yield b'notes='
            yield b'x'*8192
        req.stream=stream
        self.assertEqual(asyncio.run(self.handlers['quotes/{id}/update','POST'](req)).status_code,400)

    def test_terms_preview_review_and_language_quota_use_host_contract(self):
        from console.lang import RateLimited
        _, rid, *_ = self.setup_round()
        ids = {'id': rid}
        self.data('quotes/{id}/terms', 'POST', ids=ids, fields=dict(confirm='1', payment='30_70',
            delivery_scope='虚构港到港范围', seller_name='虚构开票单位', seller_address='虚构示例地址'))
        preview = self.data('quotes/{id}/pi', ids=ids)
        self.assertTrue(preview['ready'])
        self.assertIn('非正式单据', preview['text'])
        doc = self.data('quotes/{id}/text', ids=ids, query={'lang': 'zh'})
        self.data('quotes/{id}/review', 'POST', ids=ids, fields=dict(confirm='1', source_hash=doc['source_hash'],
            lang='zh', text=doc['text']))
        self.assertEqual(self.detail(rid)['review']['text'], doc['text'])
        with patch.object(self.quotes, 'write', side_effect=RateLimited(25)):
            result = self.call('quotes/{id}/native', 'POST', ids=ids, fields={'conversation_key': 'fictional-key'})
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers['retry-after'], '25')
        self.assertIn('翻译额度已用完', json.loads(result.body)['error'])
