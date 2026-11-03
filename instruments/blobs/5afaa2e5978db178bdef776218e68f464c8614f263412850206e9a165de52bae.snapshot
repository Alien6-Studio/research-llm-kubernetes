"""Bind retained calibration to explicitly scoped collection instrumentation."""
import ast
import hashlib
from pathlib import Path

from v2.cpu_campaigns import get_cpu_campaign
from v2.identity import MEASURED_FILES, verify_registered


CAMPAIGN = 'v2-ollama-fra6s004-cpu-long-window'
MODEL_HELPERS = frozenset(('model_request_sha256', 'expected_native_calls',
                           'validate_proxy_state', 'native_completion_certificate'))
COUNTER_IMPORT = ast.dump(ast.parse('from collections import Counter').body[0], include_attributes=False)


def _tree(raw):
    return ast.parse(raw.decode('utf-8'))


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _segment(raw, node):
    return ast.get_source_segment(raw.decode('utf-8'), node).encode('utf-8')


def _model_delta(old, new):
    """Preserve every original AST node and raw body except run_block itself."""
    previous, current = _tree(old), _tree(new)
    prior = [node for node in previous.body if not isinstance(node, ast.FunctionDef) or node.name != 'run_block']
    retained, helpers, imports = [], set(), 0
    for node in current.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'run_block':
            continue
        if isinstance(node, ast.FunctionDef) and node.name in MODEL_HELPERS:
            if node.name in helpers:
                raise ValueError('Duplicate collection-only model helper')
            helpers.add(node.name)
        elif _dump(node) == COUNTER_IMPORT:
            imports += 1
        else:
            retained.append(node)
    if (helpers != MODEL_HELPERS or imports != 1 or len(prior) != len(retained)
            or any(_dump(a) != _dump(b) or _segment(old, a) != _segment(new, b)
                   for a, b in zip(prior, retained, strict=True))):
        raise ValueError('Model calibration nodes changed outside run_block and the explicit collection helper allowlist')
    before = [node for node in previous.body if isinstance(node, ast.FunctionDef) and node.name == 'run_block']
    after = [node for node in current.body if isinstance(node, ast.FunctionDef) and node.name == 'run_block']
    if len(before) != 1 or len(after) != 1 or _dump(before[0].args) != _dump(after[0].args):
        raise ValueError('Collection driver must retain one original run_block interface')
    return sorted(node.name for node in prior if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))


def _forward_calls(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
            and node.value.func.id == 'request']


def _proxy_delta(old, new):
    """Require the original native request expression and unchanged fixture data."""
    previous, current = _tree(old), _tree(new)
    original = next(node for node in previous.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'handle')
    original_calls = _forward_calls(original)
    classes = [node for node in current.body if isinstance(node, ast.ClassDef) and node.name == 'InferenceProxy']
    if len(original_calls) != 1 or len(classes) != 1:
        raise ValueError('Proxy instrumentation does not retain the original native forwarding structure')
    members = {node.name: node for node in classes[0].body if isinstance(node, ast.AsyncFunctionDef)}
    for name in ('handle', '_generate'):
        member = members.get(name)
        calls = _forward_calls(member) if member is not None else []
        if len(calls) != 1 or _dump(calls[0]) != _dump(original_calls[0]):
            raise ValueError('Proxy native request target, payload or timeout changed')
        returns = [node for node in ast.walk(member) if isinstance(node, ast.Return)]
        returned = returns if name == '_generate' else member.body[-1:]
        if (not returned or (name == '_generate' and len(returned) != 1)
                or not all(isinstance(node, ast.Return)
                           and _dump(node.value) == _dump(ast.Name(id='result', ctx=ast.Load())) for node in returned)):
            raise ValueError('Proxy must return its original native result object')
    generated = [node for node in ast.walk(members['handle']) if isinstance(node, ast.Await)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                 and node.value.func.attr == '_generate']
    expected = ast.parse('await self._generate(method, path, payload)', mode='eval').body
    if len(generated) != 1 or _dump(generated[0]) != _dump(expected):
        raise ValueError('Proxy generation bookkeeping changed its original request arguments')
    def padding(tree):
        return [node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'PADDING' for target in node.targets)]
    a, b = padding(previous), padding(current)
    if len(a) != 1 or len(b) != 1 or _segment(old, a[0]) != _segment(new, b[0]):
        raise ValueError('Proxy transport fixture padding changed')
    return hashlib.sha256(_dump(original_calls[0]).encode('utf-8')).hexdigest()


def verify_calibration_compatibility(root, calibration_source_id, collection_source_id):
    """Accept only the exact approved source pair and verify its limited delta."""
    root = Path(root).resolve()
    campaign = get_cpu_campaign(CAMPAIGN)
    if (calibration_source_id != campaign['calibration_source_id']
            or collection_source_id != campaign['collection_source_id']
            or calibration_source_id == collection_source_id):
        raise ValueError('Compatibility requires the exact registered calibration and collection source identities')
    before = verify_registered(root, calibration_source_id)
    after = verify_registered(root, collection_source_id)
    expected = set(MEASURED_FILES)
    if set(before['files']) != expected or set(after['files']) != expected:
        raise ValueError('Calibration compatibility requires both complete eighteen-file source snapshots')
    unchanged, preserved, forward_hash = [], [], None
    for name in sorted(expected):
        old = (root / before['files'][name]['path']).read_bytes()
        new = (root / after['files'][name]['path']).read_bytes()
        if name == 'v2/model.py':
            preserved = _model_delta(old, new)
        elif name == 'v2/inference_proxy.py':
            forward_hash = _proxy_delta(old, new)
        else:
            if old != new:
                raise ValueError('Unexpected source change outside collection orchestration/proxy bookkeeping: ' + name)
            unchanged.append(name)
    return {'calibration_source_id': calibration_source_id, 'collection_source_id': collection_source_id,
            'changed_files': ['v2/inference_proxy.py', 'v2/model.py'],
            'calibration_driver_sha256': before['files']['v2/model.py']['sha256'],
            'collection_driver_sha256': after['files']['v2/model.py']['sha256'],
            'calibration_proxy_sha256': before['files']['v2/inference_proxy.py']['sha256'],
            'collection_proxy_sha256': after['files']['v2/inference_proxy.py']['sha256'],
            'preserved_model_nodes': preserved, 'collection_only_helpers': sorted(MODEL_HELPERS),
            'native_forward_call_ast_sha256': forward_hash, 'unchanged_files': unchanged,
            'scope': 'Retained load calibration; collection adds ledger bookkeeping and terminal-completion certification. Instrumentation overhead is not assumed zero.'}
