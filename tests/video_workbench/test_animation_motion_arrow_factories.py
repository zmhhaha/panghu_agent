"""Invoked lexical factories must forward the registered clock to real writes."""
import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_creation_selection import validate_source_selection
from test_animation_motion_evidence import fixture, motion, source


SCENE = ''''use strict';
(() => {
    const Field = (() => {
        const draw = (context,seconds) => {context.drawImage(sprite,seconds*20,0);};
        return Object.freeze({draw});
    })();
    const Nodes = ((field) => {
        const draw = (context,seconds) => {field.draw(context,seconds);};
        return Object.freeze({advance:draw});
    })(Field);
    const seek = seconds => {const t=Math.max(0,Number(seconds));Nodes.advance(ctx,t);};
    window.__animationExperiment={seek};
})();'''


def assert_motion(js, expected, *, engine='canvas'):
    src = source(js, engine=engine)
    rows = motion(*fixture(src))
    if expected:
        validate_source_selection({'motion': ['native-js']}, src)
        assert rows[0]['id'] == 'native-js'
        assert rows[0]['evidence_scope'] == 'source_inspection_not_visual_acceptance'
    else:
        assert rows == []
        with pytest.raises(ServiceError, match='motion_not_used'):
            validate_source_selection({'motion': ['native-js']}, src)


@pytest.mark.parametrize('engine,visual', [
    ('canvas', 'context.drawImage(sprite,seconds*20,0);'),
    ('html-svg', 'element.setAttribute("cx",seconds*20);'),
    ('three', 'mesh.rotation.set(0,seconds*20,0);'),
])
def test_invoked_frozen_arrow_interfaces_preserve_clock_and_explicit_object_dependencies(engine, visual):
    assert_motion(SCENE.replace('context.drawImage(sprite,seconds*20,0);', visual), True, engine=engine)


def test_ordinary_named_factory_can_return_a_frozen_local_interface():
    js = '''function makeField(){
        const draw = t => {ctx.fillRect(t*20,0,8,8);};
        return Object.freeze({draw});}
        const field=makeField();
        window.__animationExperiment={seek(t){field.draw(t);}};'''
    assert_motion(js, True)


def test_same_named_arrows_and_explicit_dependency_stay_bound_to_exact_lexical_owner():
    js = SCENE.replace('const Nodes =', '''const Fixed = (() => {
        const draw = (context,seconds) => {context.drawImage(sprite,0,0);};
        return Object.freeze({draw});
    })();
    const Nodes =''').replace('})(Field);', '})(Fixed);')
    assert_motion(js, False)
    assert_motion(js.replace('})(Fixed);', '})(Field);'), True)


@pytest.mark.parametrize('change', [
    lambda s: s.replace('Nodes.advance(ctx,t)', 'Nodes.advance(ctx,0)'),
    lambda s: s.replace('field.draw(context,seconds)', 'field.draw(context,0)'),
    lambda s: s.replace('context.drawImage(sprite,seconds*20,0)', 'context.drawImage(sprite,0,0)'),
    lambda s: s.replace('Nodes.advance(ctx,t)', 'unknown.advance(ctx,t)'),
    lambda s: s.replace('})(Field);', '})(unknown);'),
    lambda s: s.replace('})(Field);', '});'),
    lambda s: s.replace('})(Field);', '})(Field).advance;'),
    lambda s: s.replace('})(Field);', '})(Field) && unknown;'),
    lambda s: s.replace('})();', '});'),
    lambda s: s.replace('const Nodes =', 'if(false){const Nodes =').replace('})(Field);', '})(Field);}'),
    lambda s: s.replace('(() => {', 'if(false)(() => {', 1),
    lambda s: s.replace('(() => {', 'if(false){(() => {', 1) + '}',
    lambda s: s.replace('window.__animationExperiment={seek};', 'if(false)window.__animationExperiment={seek};'),
    lambda s: s.replace('window.__animationExperiment={seek};', 'if(false){window.__animationExperiment={seek};}'),
    lambda s: s.replace('const seek =', 'Nodes=unknown;const seek ='),
    lambda s: s.replace('const seek =', 'Field=unknown;const seek ='),
    lambda s: s.replace('const draw = (context,seconds) => {field.draw',
                         'field=unknown;const draw = (context,seconds) => {field.draw'),
    lambda s: s.replace('Nodes.advance(ctx,t)', 'const Nodes=unknown;Nodes.advance(ctx,t)'),
    lambda s: s.replace('Nodes.advance(ctx,t)', 'let Nodes;Nodes.advance(ctx,t)'),
    lambda s: s.replace('field.draw(context,seconds)', 'const field=unknown;field.draw(context,seconds)'),
    lambda s: s.replace('Nodes.advance(ctx,t)', 'function dead(){Nodes.advance(ctx,t);}ctx.fillRect(0,0,8,8)'),
    lambda s: s.replace('return Object.freeze({draw});', 'if(false)return Object.freeze({draw});'),
    lambda s: s.replace('return Object.freeze({draw});', 'if(flag)return unknown;return Object.freeze({draw});'),
    lambda s: s.replace("'use strict';", "'use strict';const Object=unknown;"),
    lambda s: s.replace('return Object.freeze({draw});', 'const Object=unknown;return Object.freeze({draw});'),
    lambda s: s.replace("'use strict';", "'use strict';Object=unknown;"),
    lambda s: s.replace("'use strict';", "'use strict';const window=foreign;"),
])
def test_unknown_uninvoked_static_reassigned_or_shadowed_factory_paths_do_not_establish_motion(change):
    assert_motion(change(SCENE), False)


def test_uninvoked_function_wrapper_does_not_install_its_native_clock_root():
    js = SCENE.replace('(() => {', 'function dead(){', 1)
    js = js[:js.rfind('})();')] + '}'
    assert_motion(js, False)
