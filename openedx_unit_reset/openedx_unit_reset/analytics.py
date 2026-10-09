"""Server-owned Quiz metadata for analytics; never changes grading or answers."""
import copy
import functools
import hashlib
import json
import logging
import re

log = logging.getLogger(__name__)
START_EVENT = '/api/unit-reset/v1/quiz-session/start'


def _unit_scope(block):
    seen = set()
    current = block
    for _ in range(20):
        key = str(getattr(current, 'location', '') or '')
        if key in seen:
            break
        seen.add(key)
        if getattr(current, 'category', '') == 'vertical' or '+type@vertical+' in key:
            return key
        get_parent = getattr(current, 'get_parent', None)
        if not callable(get_parent):
            break
        current = get_parent()
        if current is None:
            break
    return None


def _response_definition(block, input_id):
    """Hash actual response XML and its preceding prompt, without generated IDs."""
    from lxml import etree
    for responder in block.lcp.responders.values():
        if input_id not in responder.answer_ids:
            continue
        response = responder.xml
        # CAPA generates responder ID <problem>_<response> and input IDs
        # <problem>_<response>_<input>. Normalize only this known prefix,
        # preserving author-defined identifiers in the actual definition.
        prefix = re.sub(r'_\d+_\d+$', '', str(input_id))
        nodes = [response]
        previous = response.getprevious()
        while previous is not None and not str(previous.tag).endswith('response'):
            nodes.insert(0, previous)
            previous = previous.getprevious()
        parts = []
        for node in nodes:
            normalized = copy.deepcopy(node)
            for child in normalized.iter():
                for attr, value in list(child.attrib.items()):
                    child.set(attr, re.sub(r'(?<![\w])' + re.escape(prefix) + r'(_\d+(?:_\d+)?)(?![\w])',
                                          r'capa\1', value))
            parts.append(etree.tostring(normalized, encoding='unicode', with_tail=False))
        return ''.join(parts)
    return None


def enrich_problem_check(block, event_info):
    data = getattr(block, 'data', None)
    if not isinstance(data, str) or not data.strip():
        return event_info
    payload = copy.deepcopy(event_info)
    version = 'sha256:' + hashlib.sha256(data.encode('utf-8')).hexdigest()
    payload['content_version'] = version
    unit = _unit_scope(block)
    if unit:
        payload['unit_usage_key'] = unit
    detail = payload.get('submission')
    if not isinstance(detail, dict):
        return payload
    for input_id, response in detail.items():
        if not isinstance(response, dict):
            continue
        definition = _response_definition(block, input_id)
        if not definition:
            continue
        slot = re.search(r'(_\d+_\d+)$', str(input_id))
        # Unknown opaque IDs cannot be compared safely across cloned blocks.
        if not slot:
            continue
        identity = {'version': version, 'slot': slot.group(1), 'definition': definition,
                    'variant': str(response.get('variant') or '')}
        response['question_hash'] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return payload


def install_capa_tracking(capa_class=None):
    if capa_class is None:
        from xmodule.capa_block import ProblemBlock
        capa_class = ProblemBlock
    original = capa_class.publish_unmasked
    if getattr(original, '_acms_quiz_analytics', False):
        return False

    @functools.wraps(original)
    def publish(block, title, payload):
        if title == 'problem_check':
            try:
                payload = enrich_problem_check(block, payload)
            except Exception:
                # Telemetry failure must never reject or alter a learner submit.
                log.warning('Quiz analytics metadata unavailable for block %s',
                            getattr(block, 'location', ''), exc_info=True)
        return original(block, title, payload)

    publish._acms_quiz_analytics = True
    capa_class.publish_unmasked = publish
    return True


def emit_quiz_session_start(result, *, emit=None, reset=False):
    if not isinstance(result, dict) or result.get('success') is False:
        return False
    session = result.get('session', result)
    if not isinstance(session, dict) or not all(session.get(k) for k in (
            'id', 'course_id', 'unit_usage_key', 'started_at')):
        return False
    if session.get('requires_reset'):
        return False
    payload = {key: session.get(key) for key in (
        'course_id', 'unit_usage_key', 'sequence_usage_key', 'started_at', 'attempt_no')}
    payload['unit_reset_nonce'] = 'quiz-session:' + str(session['id'])
    if reset:
        payload['reset_request'] = True
    try:
        if emit is None:
            from eventtracking import tracker
            emit = tracker.emit
        emit(START_EVENT, payload)
        return True
    except Exception:
        log.warning('Could not emit scoped Quiz start event', exc_info=True)
        return False
