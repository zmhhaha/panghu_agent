"""Parse author-written browser JavaScript without executing it."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from .contracts import ServiceError, canonical_bytes


SYNTAX_ERROR = 'learning_source_javascript_syntax_invalid'
CHECK_UNAVAILABLE = 'learning_source_syntax_check_unavailable'
_CHECKER = Path(__file__).with_name('learning_source_syntax.mjs')
_JS_TYPES = {
    'application/ecmascript', 'application/javascript', 'application/x-ecmascript',
    'application/x-javascript', 'text/ecmascript', 'text/javascript', 'text/javascript1.0',
    'text/javascript1.1', 'text/javascript1.2', 'text/javascript1.3', 'text/javascript1.4',
    'text/javascript1.5', 'text/jscript', 'text/livescript', 'text/x-ecmascript', 'text/x-javascript',
}


class _ScriptPrograms(HTMLParser):
    CDATA_CONTENT_ELEMENTS = ('script', 'style', 'textarea', 'title', 'xmp', 'iframe',
        'noembed', 'noframes', 'noscript', 'plaintext')

    def __init__(self, html, javascript):
        super().__init__(convert_charrefs=False)
        self.javascript = javascript
        self.programs = []
        self._inline = None
        self._external = set()
        self._in_template = 0
        self.feed(html)
        self.close()
        if javascript is not None and not self._external:
            # A JavaScript file used through import() or another module is a module.
            self._add_external('module')

    def _add_external(self, goal):
        if goal not in self._external:
            self._external.add(goal)
            self.programs.append(('scene.js', goal, 1, 0, self.javascript))

    def handle_starttag(self, tag, attrs):
        if tag == 'template':
            self._in_template += 1
        if tag != 'script' or self._in_template:
            return
        attributes = {}
        for name, value in attrs:
            attributes.setdefault(name, value or '')
        script_type = attributes.get('type')
        if script_type is None:
            language = attributes.get('language', '')
            script_type = 'text/' + language if language else ''
        script_type = script_type.strip().lower()
        if script_type == 'module':
            goal = 'module'
        elif not script_type or script_type in _JS_TYPES:
            if 'nomodule' in attributes:
                return
            goal = 'classic'
        else:
            return
        if 'src' in attributes:
            if self.javascript is not None:
                try:
                    url = urlsplit(attributes['src'].replace('\\', '/'))
                except ValueError:
                    return
                if not url.scheme and not url.netloc and posixpath.normpath('/' + url.path.lstrip('/')) == '/scene.js':
                    self._add_external(goal)
            return
        self._inline = []
        self._goal = goal
        line, column = self.getpos()
        opening = self.get_starttag_text().replace('\r\n', '\n').replace('\r', '\n')
        self._line = line + opening.count('\n')
        self._column = len(opening.rsplit('\n', 1)[-1]) + (0 if '\n' in opening else column)

    def handle_data(self, text):
        if self._inline is not None:
            self._inline.append(text)

    def handle_endtag(self, tag):
        if tag == 'template':
            self._in_template = max(0, self._in_template - 1)
        if tag == 'script' and self._inline is not None:
            self.programs.append(('scene.html', self._goal, self._line, self._column, ''.join(self._inline)))
            self._inline = None


def validate_source_syntax(source):
    files = source['files']
    html = files['scene.html'].replace('\r\n', '\n').replace('\r', '\n')
    programs = _ScriptPrograms(html, files.get('scene.js')).programs
    if not programs:
        return
    node = '/usr/local/bin/node' if os.name == 'posix' and Path('/usr/local/bin/node').is_file() else shutil.which('node')
    if node is None:
        raise ServiceError(CHECK_UNAVAILABLE, 503)
    data = canonical_bytes([{'goal': goal, 'text': text} for _, goal, _, _, text in programs])
    # Windows requires SystemRoot to initialize the child process's crypto library.
    environment = {'SystemRoot': os.environ['SYSTEMROOT']} if 'SYSTEMROOT' in os.environ else {}
    try:
        result = subprocess.run([node, '--max-old-space-size=96', '--experimental-vm-modules',
            '--no-warnings', str(_CHECKER)], input=data, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, cwd=_CHECKER.parent, timeout=5, check=True)
        diagnostic = json.loads(result.stdout)
        if not diagnostic:
            return
        file, _, start, indent, _ = programs[diagnostic['index']]
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, IndexError):
        raise ServiceError(CHECK_UNAVAILABLE, 503) from None
    error = ServiceError(SYNTAX_ERROR, 422)
    line, column = diagnostic.get('line'), diagnostic.get('column')
    error.details = dict(phase='collect_output', path='/' + file, reason='javascript_syntax')
    if type(line) is int and 1 <= line <= 262145:
        error.details['line'] = start + line - 1
        if type(column) is int and 1 <= column <= 262145:
            error.details['column'] = column + (indent if line == 1 else 0)
    raise error


def check_workspace(root):
    from .profiles import video_picture_learning, video_reference_learning
    from .profiles.video import _read, decode_video_json
    from .profiles.video_learning_source_handoff import collect_source, decode_author_output, read_manifest
    from .contracts import digest

    profile = (video_picture_learning if (root / 'inputs' / video_picture_learning.INPUT_NAME).is_file()
        else video_reference_learning)
    context = profile.validate_input(decode_video_json(
        _read(root / 'inputs', profile.INPUT_NAME, profile.INPUT_MAX_BYTES)))
    file_handoff = 'source_handoff' in context
    output = (read_manifest(root, profile.OUTPUT_MAX_BYTES) if file_handoff else
        decode_author_output(_read(root, 'output.json', profile.OUTPUT_MAX_BYTES)))
    collect_source(root, output, file_handoff=file_handoff)
    result = profile.validate_result(output, digest(context), context)
    if result['source'] is not None:
        validate_source_syntax(result['source'])


if __name__ == '__main__':
    try:
        check_workspace(Path(sys.argv[1]))
        print('{"source_syntax":"checked"}')
    except ServiceError as error:
        report = {'source_syntax': 'check_failed', 'code': error.code}
        if error.code == SYNTAX_ERROR:
            report.update({key: error.details[key] for key in ('path', 'line', 'column') if key in error.details})
        print(json.dumps(report))
