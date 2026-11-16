"""Conservative read-only motion labels bound to exact rendered source.

This small inspector recognizes admitted absolute-clock authoring shapes; it is
not a JavaScript interpreter or a visual-quality judge. Unknown syntax stays
unknown. A missing library record never establishes native motion.
"""
from html.parser import HTMLParser
import posixpath
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_create import source_sha256
from .animation_authoring_tools import observed_authoring_tools


GSAP_VERSION = '3.14.2'
GSAP_SHA256 = 'c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_NAME = r'[A-Za-z_$][\w$]*'
_LITERAL = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
_CALL = re.compile(r'\b(' + _NAME + r'(?:\s*\.\s*' + _NAME + r')*)\s*\(')
_VISUAL = re.compile(r'\.(?:setAttribute|drawImage|fillRect|clearRect|strokeRect|moveTo|lineTo|arc|ellipse|'
                     r'bezierCurveTo|quadraticCurveTo|translate|rotate|scale|transform|setTransform|fillText|'
                     r'strokeText|render|(?:position|rotation|scale)\.set)\Z')


def _masked(code):
    def replace(match):
        text = match.group()
        if text.startswith('`'):
            chars = [' '] * len(text)
            for expression in re.finditer(r'\$\{([^{}]*)\}', text):
                chars[expression.start(1):expression.end(1)] = expression.group(1)
            return ''.join(chars)
        return ''.join('\n' if char == '\n' else ' ' for char in text)
    return _LITERAL.sub(replace, code)


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.references, self.inline, self.active = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        attributes = dict(attrs)
        if attributes.get('type', '') not in ('', 'module', 'text/javascript', 'application/javascript'):
            return
        if attributes.get('src'):
            self.references.append(attributes['src'])
        else:
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.inline.append(data)


def _source_program(source):
    files = source.get('files')
    if (type(files) is not dict or not files or len(files) > 16
            or any(type(k) is not str or type(v) is not str for k, v in files.items())
            or sum(map(len, files.values())) > 2 * 1024 * 1024):
        return '', set()
    parser = _Scripts()
    parser.feed(files.get('scene.html', ''))
    code = list(parser.inline)
    pending = [(name, '') for name in parser.references]
    references, seen = set(), set()
    while pending:
        name, base = pending.pop()
        if '://' in name or '?' in name or '#' in name or '\\' in name:
            continue
        path = posixpath.normpath(posixpath.join(base, name)).lstrip('/')
        if path.startswith('../') or path in seen:
            continue
        seen.add(path)
        references.add('/' + path)
        text = files.get(path)
        if text is None or not path.endswith(('.js', '.mjs')):
            continue
        code.append(text)
        for match in re.finditer(r'\b(?:import\s*\(\s*|from\s*|import\s*)[\'\"]([^\'\"]+)', text):
            pending.append((match.group(1), posixpath.dirname(path)))
    return _masked('\n'.join(code)), references


def _closing(code, start, opening='(', closing=')'):
    level = 0
    for index in range(start, len(code)):
        if code[index] == opening:
            level += 1
        elif code[index] == closing:
            level -= 1
            if not level:
                return index
    return None


def _parts(text):
    start, stack, parts = 0, [], []
    for index, char in enumerate(text):
        if char in '([{':
            stack.append(char)
        elif char in ')]}' and stack:
            stack.pop()
        elif char == ',' and not stack:
            parts.append(text[start:index])
            start = index + 1
    return parts + [text[start:]]


def _functions(code):
    result = {}
    patterns = (
        re.compile(r'\bfunction\s+(' + _NAME + r')\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(' + _NAME + r')\s*(?:=|:)\s*(?:async\s*)?function\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(seek)\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(' + _NAME + r')\s*(?:=|:)\s*(?:async\s*)?(?:\(([^()]*)\)|(' + _NAME + r'))\s*=>\s*'),
    )
    for pattern in patterns:
        for match in pattern.finditer(code):
            name = match.group(1)
            parameters = match.group(2) or (match.group(3) if match.lastindex == 3 else '')
            parameters = [p.strip() for p in parameters.split(',') if re.fullmatch(_NAME, p.strip())]
            start = match.end() - 1 if code[match.end() - 1] == '{' else match.end()
            if start < len(code) and code[start] == '{':
                end = _closing(code, start, '{', '}')
                body = code[start + 1:end] if end is not None else ''
            else:
                end = code.find(';', start)
                body = code[start:end] if end >= 0 else code[start:]
            if parameters and len(result) < 256:
                result[name] = (parameters, body)
    return result


def _roots(code, functions):
    if not re.search(r'\bwindow\s*\.\s*__animationExperiment\s*=', code):
        return set()
    roots = {'seek'} if 'seek' in functions else set()
    aliases = ['window.__animationExperiment']
    aliases.extend(re.findall(r'window\s*\.\s*__animationExperiment\s*=\s*(' + _NAME + r')\s*[;,]', code))
    aliases.extend(re.findall(r'\b(' + _NAME + r')\s*=\s*window\s*\.\s*__animationExperiment\s*[;,]', code))
    for alias in aliases:
        roots.update(re.findall(re.escape(alias) + r'\s*\.\s*seek\s*=\s*(' + _NAME + r')\b', code))
    roots.update(re.findall(r'\bseek\s*:\s*(' + _NAME + r')\s*[,}]', code))
    return roots & functions.keys()


def _has_native_motion(code):
    functions = _functions(code)
    roots = _roots(code, functions)
    taints = {name: {functions[name][0][0]} for name in roots}
    for _ in range(32):
        changed = False
        for name in list(taints):
            parameters, body = functions[name]
            known = taints[name]
            has_clock = lambda text: any(re.search(r'(?<![\w$])' + re.escape(key) + r'(?![\w$])', text) for key in known)
            for statement in re.split(r'[;\n]', body):
                for part in _parts(statement):
                    assignment = re.search(r'(?:^|[{}])\s*(?:(?:const|let|var)\s+)?(' + _NAME + r')\s*=\s*(?!=)(.*)', part)
                    if assignment and has_clock(assignment.group(2)) and assignment.group(1) not in known:
                        known.add(assignment.group(1))
                        changed = True
            for call in _CALL.finditer(body):
                end = _closing(body, call.end() - 1)
                if end is None:
                    continue
                target = re.sub(r'\s+', '', call.group(1))
                visual_target = ('member.' if call.start() and body[call.start() - 1] == '.' else '') + target
                arguments = _parts(body[call.end():end])
                if _VISUAL.search(visual_target) and any(has_clock(arg) for arg in arguments):
                    return True
                if target in functions:
                    target_taints = taints.setdefault(target, set())
                    for parameter, argument in zip(functions[target][0], arguments):
                        if has_clock(argument) and parameter not in target_taints:
                            target_taints.add(parameter)
                            changed = True
            for assignment in re.finditer(r'\.(?:position|rotation|scale|style)(?:\s*\.\s*' + _NAME
                    + r')?\s*=\s*([^;\n]+)|\.(?:globalAlpha|fillStyle|strokeStyle)\s*=\s*([^;\n]+)', body):
                if has_clock(assignment.group(1) or assignment.group(2)):
                    return True
        if not changed:
            break
    return False


def motion_orchestration(candidate, demonstration):
    """Inspect source only after exact media/render admission has been bound."""
    source = candidate.get('source')
    if type(source) is not dict or type(demonstration.get('source')) is not dict:
        return []
    try:
        checksum = source_sha256(source)
        if source_sha256(demonstration['source']) != checksum:
            return []
        code, references = _source_program(source)
    except (ValueError, TypeError, KeyError, ServiceError):
        return []
    media = candidate.get('media', {}).get('sha256')
    operation = candidate.get('operation_id')
    if not _SHA.fullmatch(media or '') or type(operation) is not str or not operation:
        return []
    common = dict(source_sha256=checksum, media_sha256=media, render_operation_id=operation)
    runtime = demonstration.get('runtime', {})
    tools = observed_authoring_tools(runtime)
    if (any(row['id'] == 'animejs' for row in tools)
            and re.search(r'\bcreateFrameTimeline\s*\(', code)
            and re.search(r'\.\s*add\s*\(', code) and re.search(r'\.\s*renderAt\s*\(', code)):
        return [dict(id='animejs', label='Anime.js', version=next(t['version'] for t in tools if t['id'] == 'animejs'),
            status='render_observed', evidence_scope='render_loaded_dependency_not_visual_acceptance', **common)]
    uses_gsap = bool(re.search(r'\bgsap\s*\.\s*(?:timeline|to|from|fromTo)\s*\(', code))
    inventory = runtime.get('library_inventory', {})
    observation = runtime.get('authoring_library_observation', {})
    if (uses_gsap and '/assets/gsap.min.js' in references
            and type(inventory) is dict and inventory.get('gsap') == dict(version=GSAP_VERSION, sha256=GSAP_SHA256)
            and type(observation) is dict
            and observation.get('scope') == 'loaded_scripts_not_api_calls_or_visual_application'
            and '/assets/gsap.min.js' in observation.get('loaded_scripts', [])):
        return [dict(id='gsap', label='GSAP', version=GSAP_VERSION, status='render_observed',
            evidence_scope='render_loaded_dependency_not_visual_acceptance', **common)]
    # Unverified library use and autonomous CSS/WAAPI are not native evidence.
    if uses_gsap or re.search(r'\bcreateFrameTimeline\s*\(|\.\s*(?:renderAt|animate)\s*\(', code):
        return []
    if _has_native_motion(code):
        return [dict(id='native-js', label='原生 JavaScript', status='source_verified',
            evidence_scope='source_inspection_not_visual_acceptance', **common)]
    return []


def measured_library_inventory(receipt, request):
    """Project only the fixed GSAP bytes measured by the verified render host."""
    if type(receipt) is not dict or type(request) is not dict:
        return {}
    runtime = receipt.get('runtime')
    assets = request.get('assets_sha256')
    if (type(runtime) is dict and runtime.get('schema') == 'video.motion-runtime.v3'
            and runtime.get('gsap') == GSAP_VERSION and runtime.get('gsap_sha256') == GSAP_SHA256
            and type(assets) is dict
            and assets.get('gsap.min.js', GSAP_SHA256) == GSAP_SHA256):
        return {'gsap': dict(version=GSAP_VERSION, sha256=GSAP_SHA256)}
    return {}
