import json
import httpx
import pytest
from pact.config import Settings
from pact.model import Model
from pact.schema import extraction_schema


def test_openai_compatible_adapter():
    def handler(request):
        assert str(request.url)=='https://provider.example/v1/chat/completions'
        assert request.headers['authorization']=='Bearer test-only'
        body=json.loads(request.content)
        assert body['response_format']['json_schema']['strict'] is True
        content={'items':[{'kind':'renewal_date','clause_id':'c0','quote':'Renews June 30, 2027.','value':'June 30, 2027'}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)}}]})
    settings=Settings(mode='test',provider='openai-compatible',model_url='https://provider.example/v1',model_api_key='test-only')
    result=Model(settings,httpx.MockTransport(handler)).call(extraction_schema(['renewal_date']),'extract',[])
    assert result.items[0].value=='June 30, 2027'


def test_provider_error_does_not_generate_fallback_evidence():
    model=Model(Settings(mode='test'),httpx.MockTransport(lambda _:httpx.Response(429,json={'error':'rate limited'})))
    with pytest.raises(httpx.HTTPStatusError):
        model.call(extraction_schema(['notice_period']),'extract',[])


def test_invalid_structured_response_rejected():
    model=Model(Settings(mode='test'),httpx.MockTransport(lambda _:httpx.Response(200,json={'message':{'content':'not JSON'}})))
    with pytest.raises(ValueError):
        model.call(extraction_schema(['notice_period']),'extract',[])
