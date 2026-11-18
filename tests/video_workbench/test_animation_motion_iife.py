"""Window IIFE exports must carry a proven absolute clock to visual writes."""
import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_creation_selection import validate_source_selection
from test_animation_motion_evidence import fixture, motion, source


MODULE = '''(function(host){
    function paint(context,clock){const x=Math.sin(clock)*20;context.fillRect(x,0,8,8);}
    host.layer=Object.freeze({advance:paint});
})(window);
(function(root){
    const layer=root.layer;
    function seek(seconds){layer.advance(ctx,seconds);}
    root.__animationExperiment={seek};
})(window);'''


def test_executed_iife_window_alias_and_explicit_export_forward_actual_clock():
    src = source(MODULE)
    validate_source_selection({'creation': ['canvas'], 'motion': ['native-js']}, src)
    result = motion(*fixture(src))
    assert result[0]['id'] == 'native-js'
    assert result[0]['evidence_scope'] == 'source_inspection_not_visual_acceptance'


@pytest.mark.parametrize('engine,visual', [
    ('canvas', 'context.fillRect(x,0,8,8);'),
    ('three', 'mesh.rotation.set(0,x,0);'),
    ('html-svg', 'shape.setAttribute("cx",x);'),
])
def test_iife_exported_clock_interface_is_not_canvas_specific(engine, visual):
    js = MODULE.replace('context.fillRect(x,0,8,8);', visual)
    assert motion(*fixture(source(js, engine=engine)))[0]['id'] == 'native-js'


def test_iife_direct_callback_uses_its_own_lexical_function_identity():
    js = '''(function(host){
        function seek(seconds){ctx.fillRect(seconds*20,0,8,8);}
        host.__animationExperiment={seek};
    })(window);'''
    assert motion(*fixture(source(js)))[0]['id'] == 'native-js'
    js += '''(function(host){
        function seek(seconds){ctx.fillRect(0,0,8,8);}
        host.__animationExperiment={seek};
    })(window);'''
    assert motion(*fixture(source(js))) == []


def test_same_named_exports_remain_bound_to_the_selected_module():
    js = '''(function(host){function paint(t){ctx.fillRect(0,0,8,8);}
        host.fixed=Object.freeze({paint});})(window);
    (function(host){function paint(t){ctx.fillRect(t*20,0,8,8);}
        host.moving=Object.freeze({paint});})(window);
    (function(host){const layer=host.fixed;
        function seek(seconds){layer.paint(seconds);}
        host.__animationExperiment={seek};})(window);'''
    assert motion(*fixture(source(js))) == []
    assert motion(*fixture(source(js.replace('layer=host.fixed', 'layer=host.moving'))))[0]['id'] == 'native-js'


@pytest.mark.parametrize('change', [
    lambda js: js.replace('})(window);', '});'),
    lambda js: js.replace('})(window);', '})(foreign);'),
    lambda js: js.replace('const layer=root.layer;', 'const layer=root.missing;'),
    lambda js: js.replace('layer.advance(ctx,seconds)', 'layer.advance(ctx,0)'),
    lambda js: js.replace('context.fillRect(x,0,8,8);', 'context.fillRect(0,0,8,8);'),
    lambda js: js.replace('const layer=root.layer;', 'let layer=root.layer;layer=unknown;'),
    lambda js: js.replace('host.layer=Object.freeze', 'if(false)host.layer=Object.freeze'),
    lambda js: js.replace('host.layer=Object.freeze({advance:paint});',
        'if(false){host.layer=Object.freeze({advance:paint});}'),
    lambda js: js.replace('host.layer=Object.freeze({advance:paint});',
        'host.layer=Object.freeze({advance:paint});host.layer=unknown;'),
    lambda js: js.replace('function seek(seconds){layer.advance(ctx,seconds);}',
        'function seek(seconds){function unused(){layer.advance(ctx,seconds);}ctx.fillRect(0,0,8,8);}'),
    lambda js: js.replace('function seek(seconds){layer.advance(ctx,seconds);}',
        'function draw(layer,t){layer.advance(ctx,t);}function seek(seconds){draw(unknown,seconds);}'),
    lambda js: js.replace('function seek(seconds){layer.advance(ctx,seconds);}',
        'function seek(seconds){const layer=unknown;layer.advance(ctx,seconds);}'),
    lambda js: js.replace('function seek(seconds){layer.advance(ctx,seconds);}',
        'function seek(seconds){let layer;layer.advance(ctx,seconds);}'),
    lambda js: js.replace('host.layer=Object.freeze', 'const Object=unknown;host.layer=Object.freeze'),
    lambda js: 'const window=foreign;' + js,
    lambda js: js.replace('root.__animationExperiment={seek};', 'root.__animationExperiment=unknown;'),
    lambda js: js.replace('root.__animationExperiment={seek};', 'if(false)root.__animationExperiment={seek};'),
    lambda js: js.replace('root.__animationExperiment={seek};', 'if(false){root.__animationExperiment={seek};}'),
])
def test_unsupported_or_ambiguous_iife_paths_never_establish_native_motion(change):
    src = source(change(MODULE))
    assert motion(*fixture(src)) == []
    with pytest.raises(ServiceError, match='motion_not_used'):
        validate_source_selection({'motion': ['native-js']}, src)


@pytest.mark.parametrize('wrapper', [
    '(function(window){BODY})(foreign);',
    'if(false){(function(window){BODY})(window);}',
    'if(false)(function(window){BODY})(window);',
])
def test_literal_window_parameter_cannot_impersonate_the_host_from_an_unexecuted_or_foreign_iife(wrapper):
    body = 'function seek(t){ctx.fillRect(t*20,0,8,8);}window.__animationExperiment={seek};'
    assert motion(*fixture(source(wrapper.replace('BODY', body)))) == []
