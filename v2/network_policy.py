"""Select outbound URLs from operator-owned origins, never from HTTP-supplied hosts."""
import json
import os
import socket
from urllib.parse import urlsplit


class NetworkPolicyError(ValueError):
    pass


def checked_origin(value):
    parsed=urlsplit(value)
    if parsed.scheme!='http' or not parsed.hostname or parsed.port is None or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise NetworkPolicyError('An operator origin must be an explicit HTTP host and port without credentials or suffix')
    return value


def inference_origin():
    return checked_origin(os.environ.get('STUDY_INFERENCE_ORIGIN','http://host.docker.internal:11435'))


def worker_origins():
    configured=os.environ.get('STUDY_WORKER_ORIGINS')
    if configured is not None:
        return [checked_origin(value) for value in json.loads(configured)]
    try:
        addresses=socket.getaddrinfo('workers-observe',8080,family=socket.AF_INET,type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({f'http://{address[4][0]}:8080' for address in addresses})


def choose(value, allowed):
    for known in allowed:
        if value==known: return known  # Return the server-owned string, not the untrusted value.
    raise NetworkPolicyError('Unapproved study network target')


def allowed_url(value):
    provider=checked_origin(os.environ.get('PROVIDER_URL','http://provider:8080'))
    fixed=[provider+suffix for suffix in ['/stats','/configure','/infer','/admit','/reserve','/release','/records']]
    fixed += ['http://workers:8080/work',inference_origin()+'/api/generate',
              'http://127.0.0.1:11435/api/tags']
    fixed += ['http://127.0.0.1:18081'+suffix for suffix in ['/run','/live','/workers']]
    for known in fixed:
        if value==known:return known
    return choose(value,[origin+suffix for origin in worker_origins() for suffix in ['/stats','/configure','/records']])


def normalized_config(payload):
    result=dict(payload)
    if 'ollama_url' in result:
        result['ollama_url']=choose(result['ollama_url'],[inference_origin()])
    if 'provider' in result:
        result['provider']=normalized_config(result['provider'])
    if 'worker_url' in result:
        result['worker_url']=choose(result['worker_url'],['http://workers:8080',*worker_origins()])
    for name in ['worker_addresses','addresses']:
        if name in result:
            known=worker_origins()
            result[name]=[choose(value,known) for value in result[name]]
    return result
