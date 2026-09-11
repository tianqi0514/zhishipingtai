"""Real post-migration app smoke test; creates only a labelled test space.
Run inside the local API container. No credentials or documents are printed.
"""
import io
import json
import uuid
from scripts.miaobi.demo_client import DemoClient


def verify():
    api = DemoClient()
    results = {}
    spaces = api.get('/spaces')
    target = next(s for s in spaces if s['id'] == 'd81860ed-e236-4b49-a90b-d57981cbdc3e')
    for channel in ('keyword', 'vector', 'graph'):
        response = api.post('/search', {'query': '安置点A 供水站C 供水中断', 'space_ids': [target['id']], 'top_k': 10,
                                       'use_keyword': channel == 'keyword', 'use_vector': channel == 'vector',
                                       'use_graph': channel == 'graph', 'use_reranker': False})
        assert response['items'], f'{channel} returned no real results'
        results[channel] = len(response['items'])
    doc = api.get('/writing/documents/3ddf3045-f8ac-46e7-9054-8f986ef1934b')
    assert doc['current_version']['content']
    results['writing_restored'] = True
    routes = api.get('/model-routing-policies/resolved')
    assert routes
    results['model_routes_restored'] = True
    models = api.get('/model-configs')
    model = next(m for m in models if m['model_kind'] == 'llm' and m['enabled'] and m['is_default'])
    tested = api.post(f"/model-configs/{model['id']}/test")
    assert tested['status'] == 'success', 'Default model real request failed (secret details omitted)'
    results['default_model'] = {'name': model['name'], 'status': tested['status'], 'elapsed_ms': tested['elapsed_ms']}
    suffix = uuid.uuid4().hex[:8]
    space = api.post('/spaces', {'code': f'remote-dev-check-{suffix}', 'name': f'远端开发中间件验收 {suffix}', 'enabled': True})
    results['test_space_id'] = space['id']
    content = '# 开发环境验收\n\n这是明确标记的自动验收材料，不代表真实业务。\n开发环境校验编号：REMOTEDEV-' + suffix + '\n本次验证对象存储、数据库、消息队列、加工任务与检索索引联通。\n'
    response = api.client.post('/documents/upload', data={'space_id': space['id'], 'knowledge_processing_mode': 'vector'},
                               files={'file': ('远端开发中间件验收.md', io.BytesIO(content.encode()), 'text/markdown')})
    api._raise(response)
    uploaded = response.json()
    parsed = api.wait_job(uploaded['job']['id'], timeout=600)
    processed = api.wait_knowledge_job(uploaded['version']['id'], timeout=600)
    chunks = api.get(f"/versions/{uploaded['version']['id']}/chunks", limit=20)['items']
    assert chunks and any('REMOTEDEV-' + suffix in chunk['text'] for chunk in chunks)
    results['uploaded_document_id'] = uploaded['document']['id']
    results['parse_status'] = parsed['status']
    results['process_status'] = processed['status']
    results['chunks'] = len(chunks)
    response = api.post('/search', {'query': 'REMOTEDEV-' + suffix, 'space_ids': [space['id']], 'use_keyword': True,
                                   'use_vector': True, 'use_graph': False, 'use_reranker': False, 'top_k': 5})
    assert response['items']
    assert response['channel_counts']['keyword'] > 0 and response['channel_counts']['vector'] > 0
    results['new_upload_retrieval'] = len(response['items'])
    results['new_upload_channel_counts'] = response['channel_counts']
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    verify()
