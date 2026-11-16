"""Bounded lexical dataflow for locally authored factory object interfaces.

This is source inspection, not JavaScript execution or visual acceptance.
Ambiguous receivers never borrow an unrelated same-named method.
"""
import re


_NAME = r'[A-Za-z_$][\w$]*'
_CALL = re.compile(r'\b(' + _NAME + r'(?:\s*\.\s*' + _NAME + r')*)\s*\(')
_VISUAL = re.compile(r'\.(?:setAttribute|drawImage|fillRect|clearRect|strokeRect|moveTo|lineTo|arc|ellipse|'
                     r'bezierCurveTo|quadraticCurveTo|translate|rotate|scale|transform|setTransform|fillText|'
                     r'strokeText|render|(?:position|rotation|scale)\.set)\Z')


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


def factory_motion(code):
    """Trace a bounded subset of returned object APIs without executing JS.

    Function identity includes its lexical owner. A member call is followed
    only when its receiver is bound to a specific locally defined factory and
    that factory returns that exact function. Unsupported/ambiguous bindings
    are not guessed from common method names.
    """
    records, bodies = [], set()
    patterns = (
        re.compile(r'\bfunction\s+(' + _NAME + r')\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(' + _NAME + r')\s*(?:=|:)\s*(?:async\s*)?function\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(' + _NAME + r')\s*\(([^()]*)\)\s*\{'),
        re.compile(r'\b(' + _NAME + r')\s*(?:=|:)\s*(?:async\s*)?(?:\(([^()]*)\)|(' + _NAME + r'))\s*=>\s*'),
    )
    for pattern in patterns:
        for match in pattern.finditer(code):
            if match.group(1) in {'if', 'for', 'while', 'switch', 'catch', 'with'}:
                continue
            parameters = match.group(2) or (match.group(3) if match.lastindex == 3 else '')
            parameters = [p.strip().split('=', 1)[0].strip() for p in _parts(parameters)] if parameters.strip() else []
            if any(not re.fullmatch(_NAME, p) for p in parameters):
                continue
            start = match.end() - 1 if code[match.end() - 1] == '{' else match.end()
            if start < len(code) and code[start] == '{':
                end = _closing(code, start, '{', '}')
                if end is None:
                    continue
                body_start = start + 1
            else:
                end = code.find(';', start)
                if end < 0:
                    continue
                body_start = start
            if body_start in bodies:
                continue
            if len(records) >= 256:
                return False
            bodies.add(body_start)
            records.append(dict(name=match.group(1), start=match.start(), body_start=body_start,
                end=end, parameters=parameters, body=code[body_start:end], parent=None))
    def scope_at(position, *, exclude=None):
        scopes = [(row['end'] - row['body_start'], i) for i, row in enumerate(records)
                  if i != exclude and row['body_start'] <= position < row['end']]
        return min(scopes)[1] if scopes else None
    for index, row in enumerate(records):
        row['parent'] = scope_at(row['start'], exclude=index)
    def ancestors(scope):
        result = []
        while scope is not None and len(result) < 256:
            result.append(scope)
            scope = records[scope]['parent']
        return result + [None]
    def function(name, scope):
        for owner in ancestors(scope):
            found = [i for i, row in enumerate(records) if row['name'] == name and row['parent'] == owner]
            if found:
                return found[0] if len(found) == 1 else None
        return None
    # Only returns belonging to this function can define its public API.
    apis = {}
    for index, row in enumerate(records):
        body = row['body']
        candidates = []
        for match in re.finditer(r'\breturn\s*\{', body):
            position = row['body_start'] + match.start()
            if scope_at(position) != index:
                continue
            start = row['body_start'] + match.end() - 1
            end = _closing(code, start, '{', '}')
            if end is None:
                continue
            api = {}
            for part in _parts(code[start + 1:end]):
                field = re.fullmatch(r'\s*(' + _NAME + r')\s*(?::\s*(' + _NAME + r'))?\s*', part)
                if not field:
                    continue
                target = function(field.group(2) or field.group(1), index)
                if target is not None:
                    api[field.group(1)] = target
            if api:
                candidates.append(api)
        if len(candidates) == 1:
            apis[index] = candidates[0]
    bindings = {}
    assignment = re.compile(r'(?<![\w$.])(' + _NAME + r')\s*=\s*(?!=)')
    for match in assignment.finditer(code):
        scope = scope_at(match.start())
        call = re.match(r'\s*(' + _NAME + r')\s*\(', code[match.end():])
        factory = function(call.group(1), scope) if call else None
        # A nonfactory assignment poisons this receiver instead of letting it
        # retain an earlier factory, or borrow a same-named outer binding.
        bindings.setdefault((scope, match.group(1)), []).append(factory if factory in apis else None)
        if len(bindings) > 512:
            return False
    shadows = {(i, name) for i, row in enumerate(records) for name in row['parameters']}
    shadows.update((scope_at(match.start()), match.group(1)) for match in re.finditer(
        r'\b(?:const|let|var)\s+(' + _NAME + r')\b', code))
    if not apis:
        return None  # Existing direct-function recognizer handles other shapes.
    def member(receiver, method, scope):
        for owner in ancestors(scope):
            factories = bindings.get((owner, receiver))
            if factories is not None:
                if len(factories) != 1 or factories[0] is None:
                    return None
                return apis[factories[0]].get(method)
            if (owner, receiver) in shadows:
                return None
        return None
    registrations = list(re.finditer(r'\bwindow\s*\.\s*__animationExperiment\s*=\s*\{', code))
    if not registrations:
        return False
    registration = registrations[-1]
    start = registration.end() - 1
    end = _closing(code, start, '{', '}')
    if end is None:
        return False
    roots = [i for i, row in enumerate(records) if row['name'] == 'seek'
             and start < row['start'] < end and row['parent'] == scope_at(registration.start())]
    if not roots:
        for part in _parts(code[start + 1:end]):
            field = re.fullmatch(r'\s*seek\s*(?::\s*(' + _NAME + r'))?\s*', part)
            if field:
                target = function(field.group(1) or 'seek', scope_at(registration.start()))
                if target is not None:
                    roots.append(target)
    if len(roots) != 1 or not records[roots[0]]['parameters']:
        return False
    taints = {roots[0]: {records[roots[0]]['parameters'][0]}}
    for _ in range(32):
        changed = False
        for index in list(taints):
            row, known = records[index], taints[index]
            # A closure's declarations are processed when that closure is
            # reached, not just because its source sits in the caller's body.
            body = list(row['body'])
            for nested in records:
                if nested['parent'] == index:
                    a, b = nested['start'] - row['body_start'], nested['end'] + 1 - row['body_start']
                    if a >= 0:
                        body[a:b] = ' ' * (b - a)
            body = ''.join(body)
            has_clock = lambda text: any(re.search(r'(?<![\w$])' + re.escape(key) + r'(?![\w$])', text) for key in known)
            for statement in re.split(r'[;\n]', body):
                for part in _parts(statement):
                    match = re.search(r'(?:^|[{}])\s*(?:(?:const|let|var)\s+)?(' + _NAME + r')\s*=\s*(?!=)(.*)', part)
                    if match and has_clock(match.group(2)) and match.group(1) not in known:
                        known.add(match.group(1))
                        changed = True
            for loop in re.finditer(r'\bfor\s*\(\s*(?:const|let|var)\s*\[\s*' + _NAME
                    + r'\s*,\s*(' + _NAME + r')\s*\]\s+of\s+Object\s*\.\s*entries\s*\(([^()]*)\)', body):
                if has_clock(loop.group(2)) and loop.group(1) not in known:
                    known.add(loop.group(1))
                    changed = True
            for call in _CALL.finditer(body):
                call_end = _closing(body, call.end() - 1)
                if call_end is None:
                    continue
                target = re.sub(r'\s+', '', call.group(1))
                arguments = _parts(body[call.end():call_end])
                visual_target = ('member.' if call.start() and body[call.start() - 1] == '.' else '') + target
                if _VISUAL.search(visual_target) and any(has_clock(arg) for arg in arguments):
                    return True
                parts = target.split('.')
                callee = (function(target, index) if len(parts) == 1 else
                          member(parts[0], parts[1], index) if len(parts) == 2 else None)
                if callee is not None:
                    reached = taints.setdefault(callee, set())
                    for parameter, argument in zip(records[callee]['parameters'], arguments):
                        if has_clock(argument) and parameter not in reached:
                            reached.add(parameter)
                            changed = True
            for match in re.finditer(r'\.(?:position|rotation|scale|style)(?:\s*\.\s*' + _NAME
                    + r')?\s*=\s*([^;\n]+)|\.(?:globalAlpha|fillStyle|strokeStyle)\s*=\s*([^;\n]+)', body):
                if has_clock(match.group(1) or match.group(2)):
                    return True
        if not changed:
            break
    return False
