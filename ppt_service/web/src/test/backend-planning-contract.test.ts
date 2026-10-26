import { describe, it, expect } from 'vitest';
import backendPlan from './fixtures/backend-stage2-api-plan-unconfirmed.json';
import { adaptProject } from '../api';
import { restoredDirection } from '../planning';

describe('actual backend unconfirmed stage-2 DTO',()=>{
 it('adapts original candidates and complete shared pages without treating the recommendation as confirmation',()=>{
  const project=adaptProject({id:'contract-only-fixture',title:'离线契约测试',revision:1,stage:'awaiting_confirmation',input_snapshot_hash:backendPlan.input_snapshot_hash,confirmed_plan_revision:null,plans:[backendPlan]});
  expect(backendPlan.confirmed_at).toBeNull();expect(backendPlan.confirmation_selection).toBeNull();
  expect(project.plan?.directions?.map(d=>d.id)).toEqual(['evidence-handbook','acceptance-console','guided-walkthrough']);
  expect(project.plan?.directions?.map(d=>d.pages.length)).toEqual([6,6,6]);
  expect(project.plan?.directions?.[0].description).toBe(backendPlan.design.directions[0].note);
  expect(project.plan?.directions?.[0].production_settings).toEqual(backendPlan.design.directions[0].production_parameters);
  expect(project.plan?.recommendation?.direction_id).toBe('evidence-handbook');
  expect(project.plan?.confirmed_direction_id).toBeUndefined();expect(restoredDirection(project)).toBe('');
 });
});
