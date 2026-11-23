"""Admitted drawing choices and real preproduced resources for learning.

The observed reference author chooses a resource goal. The shared recipe
producer then creates it before another author composes the actual result.
Neither a choice nor successful transport establishes visual acceptance.
"""
from copy import deepcopy

from .animation_creation_selection import (validate_new_selection, freeze_constraints,
    validate_source_selection, motion_tools)
from .learning_resources import validate_learning_resource_plan
from .learning_strategy import options
from .picture_learning_store import require


class LearningCapabilitiesMixin:
    def _runtime_capabilities(self):
        return deepcopy(self.renderer.capabilities() if hasattr(self.renderer, 'capabilities') else {})

    def _capabilities_public(self):
        runtime = self._runtime_capabilities()
        resource = (self.resource_stage.capabilities() if self.resource_stage is not None
            else dict(kind='manim', admitted=False))
        return dict(creation=list(runtime.get('allowed_engines', ['html-svg', 'canvas']))
            + (['manim'] if resource['admitted'] else []), motion=motion_tools(runtime),
            manim=resource, strategy=options())

    def _freeze_learning_options(self, body, frozen, *, prior=None):
        selection = validate_new_selection(body.get('capability_selection',
            (prior or {}).get('capability_selection', {})))
        caps = self._capabilities_public()
        require(all(item in caps['creation'] for item in selection.get('creation', [])),
            'picture_learning_creation_not_admitted', 409)
        require(all(item in caps['motion'] for item in selection.get('motion', [])),
            'picture_learning_motion_not_admitted', 409)
        config = options(allow_collaboration=body.get('allow_collaboration',
                (prior or {}).get('allow_collaboration', True)),
            allow_probes=body.get('allow_probes', (prior or {}).get('allow_probes', True)))
        frozen.update(capability_selection=selection, learning_strategy=config,
            allow_collaboration=config['allow_collaboration'], allow_probes=config['allow_probes'],
            resource_capabilities=deepcopy(caps['manim']))

    def _resource_document(self, run, document):
        frozen = run['frozen']
        constraints = document['constraints']
        selection = frozen.get('capability_selection')
        if selection is not None:
            constraints = freeze_constraints(selection, constraints, document['assets'])
            document['constraints'] = constraints
        available = frozen.get('resource_capabilities', {})
        offered = available.get('admitted') and (not (selection or {}).get('creation')
            or 'manim' in selection['creation'])
        if offered:
            constraints['learning_resource_offer'] = dict(capabilities=deepcopy(available),
                result_field='method.resource_plan', plan=dict(kind='manim', goal='Observed visual objective',
                    guidance='Describe the observed geometry/material and exact intended result.',
                    options=dict(width=640, height=360, frame_count=1 if self.prefix.endswith('picture-lab') else 48,
                        global_start_frame=0, seed=17)),
                instructions=[
                    'Choose resources after observing the actual reference, not before. Omit resource_plan unless adopting real Manim.',
                    'With Manim as the sole drawing choice, propose a resource plan; HTML/SVG is only its supporting carrier.',
                    'In mode=create, source must still be a non-null valid source object with an allowed engine, strategy and complete scene files. method.resource_plan supplements source; it never replaces source. This also applies to changed-content transfer with no baseline source.',
                    'Before resource production, author a valid minimal browser carrier source yourself without claiming that the resource has been generated or consumed. Do not invent a manifest, PNG, resource ID or rendered geometry. The host freezes this draft with your plan and does not render it; after producing the actual resource, it requests the complete composition using the supplied descriptor and editable Python.',
                    'The source-only recipe role receives your frozen goal/guidance, not the reference images. Describe important geometry and visual criteria concretely.',
                    'A proposed resource is not yet available. The host runs the real recipe producer, then supplies its descriptor and editable Python for complete composition.',
                    'A still uses exactly one completed frame at local frame zero. A video resource is at most 60 frames at 24fps and may occupy only part of a longer composition.',
                    'Do not reuse resource IDs from a previous picture, round or changed-content test. Request a new resource plan when the geometry needs rebuilding.',
                ])
        if run.get('resource_ready'):
            context = deepcopy(run['resource_context'])
            document['assets'].update(context.pop('assets'))
            constraints.update(context)
            constraints['learning_resource_offer']['instructions'].append(
                'The supplied resource is ready. Compose it now; omit method.resource_plan and do not request another producer in this cycle.')
            if document['mode'] == 'create' and run['work_kind'] == 'reproduction':
                document['base_source'] = deepcopy(run.get('resource_draft_source'))
        return document

    def _adopt_resource_proposal(self, run, operation, result):
        proposal = (result.get('method') or {}).get('resource_plan')
        if proposal is None:
            return False
        require(operation['stage'] == 'author' and not run.get('resource_ready'),
            'picture_learning_nested_resource_forbidden', 409)
        require(self.resource_stage is not None
            and run['frozen'].get('resource_capabilities', {}).get('admitted'),
            'picture_learning_manim_not_admitted', 409)
        selection = run['frozen'].get('capability_selection', {})
        require(not selection.get('creation') or 'manim' in selection['creation'],
            'picture_learning_resource_selection_conflict', 409)
        plan = validate_learning_resource_plan(proposal,
            output_kind='video' if self.prefix.endswith('video-lab') else 'picture',
            duration_seconds=run['frozen'].get('duration_seconds', 1),
            width=run['frozen']['width'], height=run['frozen']['height'])
        run.update(resource_plan=plan, resource_draft_source=deepcopy(result['source']),
            resource_author_operation_id=operation['id'])
        return True

    def _advance_learning_resources(self, run, *, cancelled=False):
        require(self.resource_stage is not None, 'picture_learning_manim_not_admitted', 503)
        retry = run.get('resource_retry_pending')
        if retry and not cancelled:
            previous = self.store.read('experiments', run['owner'], retry)
            self.resource_stage.restore_retry(run['owner'], run, run['resource_plan'],
                previous, previous['resource_plan'])
            run.pop('resource_retry_pending', None)
        status = self.resource_stage.advance(run['owner'], run, run['resource_plan'], cancelled=cancelled)
        if cancelled:
            if status not in {'completed', 'failed', 'cancelled'}:
                run['state'] = 'stopping' if run['stop_requested'] else 'submission_unknown'
                return False
            return True
        if status == 'completed':
            run.update(resource_ready=True, resource_context=self.resource_stage.render_context(
                run['owner'], run, run['resource_plan']))
            if (run.get('learning_strategy') or {}).get('phase') == 'resources':
                self._learning_resources_ready(run)
            else:
                run['stage'] = 'author'
        elif status in {'failed', 'cancelled'}:
            run['error'] = dict(code='picture_learning_resource_' + status)
            self._finish(run, 'resource_' + status, 'failed' if status == 'failed' else 'cancelled')
        return status in {'completed', 'failed', 'cancelled'}

    def _validate_learning_source(self, run, source, *, planning=False):
        selection = deepcopy(run['frozen'].get('capability_selection', {}))
        if selection.get('creation') == ['manim']:
            require(planning or run.get('resource_ready'), 'picture_learning_manim_resource_required', 409)
        if run.get('resource_ready') and selection.get('creation') and 'manim' in selection['creation']:
            if 'html-svg' not in selection['creation']:
                selection['creation'].append('html-svg')
        if not planning:
            validate_source_selection(selection, source)

    @staticmethod
    def _next_learning_resource_cycle(run, candidate=None, *, transfer=False):
        for key in ('resource_ready', 'resource_context', 'resource_plan', 'resource_draft_source',
                    'resource_author_operation_id', 'resource_baseline_descriptor', 'resource_prior_review'):
            run.pop(key, None)
        if not transfer and candidate and candidate.get('resource_descriptor'):
            run['resource_baseline_descriptor'] = deepcopy(candidate['resource_descriptor'])
            run['resource_prior_review'] = deepcopy(candidate.get('review'))

    def _restore_learning_resources(self, prior, run):
        for key in ('resource_plan', 'resource_context', 'resource_ready', 'resource_draft_source',
                    'resource_author_operation_id', 'resource_baseline_descriptor', 'resource_prior_review'):
            if key in prior:
                run[key] = deepcopy(prior[key])
        if prior.get('resource_plan'):
            run['resource_retry_pending'] = prior['id']
