"""Reference-learning resources on the shared recipe/Cairo resource ledger.

The reference author first observes actual pixels and freezes a resource plan.
Only an adopted plan reaches this stage. Resource production is not evidence
of visual similarity, motion quality, transfer, developer adoption or release.
"""
from copy import deepcopy
import math

from agent_service.contracts import digest
from .lab_resource_stage import LabResourceStage
from .manim_method_coordinator import authoring_request
from .manim_method_resources import PRODUCER_REQUIREMENT
from .manim_resource import MAX_DECODED_BYTES, require


def _text(value, maximum, *, empty=False):
    require(type(value) is str and (empty or value.strip()) and len(value) <= maximum
        and not any((ord(char) < 32 and char not in '\r\n\t')
                    or 0xD800 <= ord(char) <= 0xDFFF for char in value),
        'learning_resource_plan_invalid')
    return value


def validate_learning_resource_plan(value, *, output_kind, duration_seconds=1,
                                    width=1280, height=720):
    """Normalize an author-proposed plan without starting a producer.

    One frame is enough for a still. A video may contain a short Manim passage
    in a longer browser composition; the admitted producer remains <=60 frames.
    Resolution is bounded by its actual decoded-pixel budget, not only duration.
    """
    require(output_kind in {'picture', 'video'}, 'learning_resource_output_kind_invalid')
    require(type(duration_seconds) in (int, float) and math.isfinite(duration_seconds)
        and 1 <= duration_seconds <= 60 and duration_seconds * 24 == int(duration_seconds * 24),
        'learning_resource_duration_invalid')
    require(type(width) is int and 320 <= width <= 1920
        and type(height) is int and 180 <= height <= 1080,
        'learning_resource_dimensions_invalid')
    if value is None:
        return None
    require(type(value) is dict and {'kind', 'goal', 'guidance'} <= set(value)
        <= {'kind', 'goal', 'guidance', 'options'} and value['kind'] == 'manim',
        'learning_resource_plan_invalid')
    _text(value['goal'], 8000)
    _text(value['guidance'], 8000, empty=True)
    supplied = value.get('options', {})
    require(type(supplied) is dict and set(supplied) <= {
        'width', 'height', 'frame_count', 'global_start_frame', 'seed'},
        'learning_resource_options_invalid')
    resource_width, resource_height = min(width, 640), min(height, 360)
    max_frames = MAX_DECODED_BYTES // (resource_width * resource_height * 4)
    count = 1 if output_kind == 'picture' else min(48, int(duration_seconds * 24), max_frames)
    options = dict(kind='manim', width=resource_width, height=resource_height,
        frame_count=count, global_start_frame=0, seed=17)
    options.update(deepcopy(supplied))
    options = authoring_request(options, 1 if output_kind == 'picture' else duration_seconds)
    if output_kind == 'picture':
        require(options['frame_count'] == 1 and options['global_start_frame'] == 0,
            'learning_picture_resource_single_frame_required')
    return dict(kind='manim', goal=value['goal'], guidance=value['guidance'],
        options={key: item for key, item in options.items() if key != 'kind'})


class LearningResourceStage:
    """A small adapter; original-ID recovery and cancellation stay shared.

    The caller holds its existing owner/project/run lock, freezes ``plan`` from
    one stopped author result, and persists it before calling ``advance``.
    A reproduction round and a changed-content round never share producer IDs.
    """
    def __init__(self, path, *, bindings_path, coordinator, output_kind, clock=None):
        require(output_kind in {'picture', 'video'}, 'learning_resource_output_kind_invalid')
        self.output_kind, self.coordinator = output_kind, coordinator
        options = {} if clock is None else dict(clock=clock)
        self.shared = LabResourceStage(path, bindings_path=bindings_path,
            coordinator=coordinator, **options)

    def capabilities(self):
        coordinator = self.coordinator()
        admitted = coordinator is not None and (coordinator.enabled or coordinator.candidate)
        return dict(kind='manim', admitted=bool(admitted),
            output_kind=self.output_kind, version='0.22.1', renderer='cairo',
            fps=24, max_frames=1 if self.output_kind == 'picture' else 60,
            max_decoded_bytes=MAX_DECODED_BYTES, latex_available=False,
            producer_requirement=deepcopy(PRODUCER_REQUIREMENT),
            evidence_scope='resource_not_visual_acceptance')

    def preflight(self, deadline_at):
        require(self.capabilities()['admitted'], 'learning_manim_not_admitted', 503)
        return self.coordinator().preflight(deadline_at)

    def identity(self, run):
        require(type(run) is dict and type(run.get('id')) is str
            and run.get('work_kind') in {'reproduction', 'transfer'}
            and type(run.get('round')) is int and 1 <= run['round'] <= 8,
            'learning_resource_identity_invalid')
        return 'learning-resource-' + digest(dict(output_kind=self.output_kind, experiment_id=run['id'],
            work_kind=run['work_kind'], round=run['round']))[:40]

    def _run(self, owner, run, plan):
        frozen = run['frozen']
        duration = 1 if self.output_kind == 'picture' else frozen['duration_seconds']
        plan = validate_learning_resource_plan(plan, output_kind=self.output_kind,
            duration_seconds=duration, width=frozen['width'], height=frozen['height'])
        require(plan is not None, 'learning_resource_plan_required')
        guidance = plan['guidance']
        if self.output_kind == 'picture':
            guidance = ('Produce a reference-learning still, not an animated reveal. '
                'The one exported frame at local frame 0 must already contain the complete '
                'intended geometry and material; keep the full editable Manim Python source. '
                'A generated PNG does not establish similarity or publish a Skill.\n' + guidance)
        # This is the reference author's frozen description of observed pixels,
        # not a claim that the source-only recipe role itself saw those images.
        resource_frozen = dict(goal=plan['goal'], feedback=guidance,
            duration_seconds=duration, resource_authoring=dict(kind='manim', **plan['options']))
        selection = frozen.get('capability_selection')
        if selection is not None:
            resource_frozen['capability_selection'] = deepcopy(selection)
        baseline = run.get('resource_baseline_descriptor')
        if baseline is not None:
            if run.get('mode') == 'fresh':
                # Starting fresh excludes all pre-experiment recipes. It does
                # not prohibit refining a newly authored result in this same
                # bounded experiment. Check that earlier owned round against
                # its actual shared ledger, not only a caller-supplied label.
                candidates = [candidate for candidate in run.get('candidates', [])
                    if candidate.get('resource_descriptor') == baseline
                    and candidate.get('work_kind') == run['work_kind']
                    and type(candidate.get('round')) is int and 1 <= candidate['round'] < run['round']]
                require(len(candidates) == 1, 'learning_resource_fresh_baseline_forbidden', 409)
                prior = dict(run, round=candidates[0]['round'])
                require(self.context(owner, prior) == baseline,
                    'learning_resource_fresh_baseline_forbidden', 409)
            resource_frozen['manim_iteration'] = dict(baseline_descriptor=deepcopy(baseline),
                criteria=deepcopy(frozen.get('criteria', [])),
                review=deepcopy(run.get('resource_prior_review')),
                developer_feedback=[frozen['feedback']] if frozen.get('feedback') else [])
        value = dict(id=self.identity(run), frozen=resource_frozen,
            deadline_at=run['deadline_at'], state=run['state'])
        binding = frozen.get('library_binding')
        if binding is not None:
            value['library_binding'] = deepcopy(binding)
        return value

    def advance(self, owner, run, plan, *, cancelled=False):
        if not cancelled:
            require(self.capabilities()['admitted'], 'learning_manim_not_admitted', 503)
        return self.shared.advance(owner, self._run(owner, run, plan), cancelled=cancelled)

    def context(self, owner, run):
        return self.shared.context(owner, self.identity(run))

    def view(self, owner, run):
        return self.shared.view(owner, self.identity(run))

    def render_context(self, owner, run, plan):
        resource_run = self._run(owner, run, plan)
        descriptor = self.context(owner, run)
        assets = self.coordinator().resolve(descriptor)
        placement = self.shared.placement(owner, resource_run)
        playback_example = (
            "import {prepareManimMethodFrames} from '/assets/manim_method_frames.mjs';\n"
            "const resourceRoot = document.getElementById('manim-resource');\n"
            "const layer = prepareManimMethodFrames(resourceRoot, manifest, "
            "{placementStartFrame: " + str(placement['global_start_frame']) + "});\n"
            "window.__animationExperiment = {\n"
            "  ready: layer.ready,\n"
            "  seek(seconds) { layer.seekFrame(Math.floor(Math.max(0, seconds) * 24)); }\n"
            "};"
        )
        return dict(generated_resources=[descriptor], assets={name: digest_bytes(raw)
                for name, raw in assets.items()}, resource_placement=placement,
            manim_source_context=self.coordinator().source_context(owner, descriptor),
            consumption_guidance=(
                'Use the exact generated Manim geometry. Import prepareManimMethodFrames '
                'from /assets/manim_method_frames.mjs; embed the supplied manifest object '
                'inline as manifest rather than fetching JSON (connect-src is disabled). '
                'Its exact API is prepareManimMethodFrames(rootElement, manifest, '
                '{placementStartFrame}), with three positional arguments; do not pass '
                'one object containing container and manifest. It appends its own images '
                'to the positioned root and returns {ready, seekFrame, dispose}, not an '
                'element or a promise. Register window.__animationExperiment synchronously '
                'with ready: layer.ready before awaiting image decoding; the host awaits '
                'that promise before synchronous seek(seconds). Call seekFrame from the '
                'HyperFrames absolute clock with placementStartFrame equal to '
                'resource_placement.global_start_frame. This interface-only example '
                'assumes your positioned container has id manim-resource and manifest is '
                'the exact supplied generated_resources[0].manifest; retain your own '
                'composition and add any other permitted motion to the same seek:\n'
                '```javascript\n' + playback_example + '\n```\nPreserve '
                'the half-open interval; do not draw replacement geometry in HTML/SVG. '
                'Loaded resources prove transport only, not expressive quality.'))

    def restore_retry(self, owner, run, plan, previous_run, previous_plan):
        require(previous_run['state'] in {'failed', 'cancelled'}
            and run['id'] != previous_run['id'], 'learning_resource_retry_invalid', 409)
        current = self._run(owner, run, plan)
        previous = self._run(owner, previous_run, previous_plan)
        current['frozen']['retry_of_commission_id'] = previous['id']
        return self.shared.restore_retry(owner, current, previous)


def digest_bytes(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()
