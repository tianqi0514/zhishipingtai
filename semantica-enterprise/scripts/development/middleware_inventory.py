"""Read-only migration verification. Prints counts/checksums, never secrets."""
import hashlib
import json
import httpx
import redis
from sqlalchemy import inspect, text
from packages.platform.config import get_settings
from packages.platform.database import engine
from packages.platform.storage import object_storage


def inventory():
    settings = get_settings()
    result = {}
    with engine.connect() as connection:
        tables = inspect(connection).get_table_names()
        result['database'] = {table: connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() for table in sorted(tables)}
    objects = sorted((o.object_name, o.size, o.etag) for o in object_storage.client.list_objects(settings.object_store_bucket, recursive=True))
    result['objects'] = {'count': len(objects), 'bytes': sum(o[1] for o in objects), 'checksum': hashlib.sha256(json.dumps(objects).encode()).hexdigest()}
    with httpx.Client(timeout=45) as client:
        response = client.get(settings.opensearch_url + '/_cat/indices?format=json&h=index,docs.count')
        response.raise_for_status()
        result['opensearch'] = sorted((v['index'], v['docs.count']) for v in response.json())
        response = client.get(settings.qdrant_url + '/collections'); response.raise_for_status()
        result['qdrant'] = {}
        for collection in response.json()['result']['collections']:
            name = collection['name']
            response = client.get(settings.qdrant_url + '/collections/' + name); response.raise_for_status()
            result['qdrant'][name] = response.json()['result']['points_count']
    client = redis.Redis(host=settings.falkordb_host, port=settings.falkordb_port, decode_responses=True, socket_timeout=20)
    result['falkordb'] = {}
    for name in sorted(client.execute_command('GRAPH.LIST')):
        nodes = client.execute_command('GRAPH.QUERY', name, 'MATCH (n) RETURN count(n)')
        edges = client.execute_command('GRAPH.QUERY', name, 'MATCH ()-[r]->() RETURN count(r)')
        result['falkordb'][name] = {'nodes': nodes[1][0][0], 'edges': edges[1][0][0]}
    return result


if __name__ == '__main__':
    print(json.dumps(inventory(), sort_keys=True))
