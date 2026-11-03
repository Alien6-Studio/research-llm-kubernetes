"""The pinned, narrow HTTP-status classification task, shared by both paths."""
MODEL = 'qwen3.6:latest'
DIGEST = '07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522'
OPTIONS = {'temperature': 0, 'seed': 20261112, 'num_ctx': 8192, 'num_predict': 32, 'num_thread': 8}


def model_request(item):
    return {'model': MODEL, 'prompt':
            'Classify the HTTP status in the last line. Status 200 is ok; all other '
            'statuses are error. Return JSON with the label only.\n' + item['prompt_text'] +
            f"\nHTTP status: {item['task_status']}",
            'stream': False, 'think': False,
            'format': {'type': 'object', 'properties': {'label': {'type': 'string', 'enum': ['ok', 'error']}},
                       'required': ['label'], 'additionalProperties': False},
            'options': dict(OPTIONS), 'keep_alive': '30m'}


def correct(raw, item):
    import json
    try:
        return json.loads(raw['response']) == {'label': 'ok' if item['task_status'] == 200 else 'error'}
    except (KeyError, ValueError, TypeError):
        return False
