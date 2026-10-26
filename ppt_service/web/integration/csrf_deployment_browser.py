"""Real static deployment + memory-CSRF + local parsing browser regression.
Dedicated development site only. Never logs tokens, cookies or request headers.
"""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

PUBLIC='http://127.0.0.1:18505'
INTERNAL='http://ppt-service-webdev-api-20261021:8000'
EXISTING='3e70a772-268b-4151-b6b9-ac2a740904ad'
EXPECTED_JS='dae3182e070ee2b0d2a2d6584df13f8a4bfa67ab77b43f483fbf4679e00c888f'
result={'fixture_title':'CSRF-DEPLOY-DOM-'+str(time.time_ns()),'checks':[],'responses':[],'csrf_write_checks':[],'resource_hashes':{},'snapshots':[],'page_errors':[],'console_errors':[]}

def main():
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  page=browser.new_page(viewport={'width':1360,'height':1000})
  page.set_default_timeout(20000)
  issued={'token':None}
  summaries={}
  page.on('pageerror',lambda error:result['page_errors'].append(type(error).__name__))
  page.on('console',lambda msg:result['console_errors'].append(msg.text) if msg.type=='error' else None)
  def forward(route):
   request=route.request
   parsed=urlsplit(request.url)
   headers=dict(request.headers)
   mutation=request.method not in ('GET','HEAD','OPTIONS')
   if mutation:
    matches=bool(issued['token']) and headers.get('x-csrf-token')==issued['token']
    result['csrf_write_checks'].append({'method':request.method,'path':parsed.path,'csrf_header_matches_issued_token':matches})
    headers['origin']=PUBLIC
   try:
    response=route.fetch(url=INTERNAL+parsed.path+('?' +parsed.query if parsed.query else ''),headers=headers,timeout=10000)
   except Exception as error:
    result.setdefault('proxy_errors',[]).append({'path':parsed.path,'error_type':type(error).__name__})
    try:route.abort('failed')
    except Exception:pass
    return
   result['responses'].append({'method':request.method,'path':parsed.path,'status':response.status})
   if parsed.path=='/api/v1/me' and response.status==200:
    raw=response.json()
    issued['token']=raw.get('csrf_token') if isinstance(raw.get('csrf_token'),str) else None
    result['me_issued_real_token']=bool(issued['token'])
   if parsed.path=='/' or parsed.path.startswith('/assets/'):
    result['resource_hashes'][parsed.path]=hashlib.sha256(response.body()).hexdigest()
   if request.method=='GET' and re.fullmatch(r'/api/v1/projects/[^/]+',parsed.path) and response.status==200:
    raw=response.json()
    result['snapshots'].append({'project_id':raw['id'],'assets':[{'name':a['display_name'],'status':a['parse_status']} for a in raw.get('assets',[])]})
    for asset in raw.get('assets',[]):summaries[(raw['id'],asset['display_name'])]=asset.get('text_summary')
   route.fulfill(response=response)
  page.route(PUBLIC+'/**',forward)
  page.goto(PUBLIC,wait_until='networkidle')
  assert result['me_issued_real_token']
  assert result['resource_hashes']['/assets/index-C66NMsHg.js']==EXPECTED_JS
  expect(page.get_by_text('本地开发测试账户 · 正式 SSO 未接通；制稿执行器尚未接通，不会调用模型')).to_be_visible()
  expect(page.get_by_text('PowerPoint 人工验收待完成，正式发布与邀请尚未开放。')).to_be_visible()
  result['checks'].append('actual_new_resource_hash_and_me_token_test_sso_powerpoint_gates')
  old_status=page.evaluate("async()=> (await fetch('/assets/index-Cb8shtpo.js')).status")
  assert old_status==200
  result['checks'].append('previous_hashed_javascript_retained_http_200')
  page.evaluate('(id)=>localStorage.setItem("ppt:selected-project",id)',EXISTING)
  page.reload(wait_until='networkidle')
  expect(page.get_by_role('heading',name='Material acceptance 20261022T040417Z',exact=True)).to_be_visible()
  expect(page.locator('.plain-text').filter(has_text='公开材料解析验收')).to_be_visible()
  result['checks'].append('existing_ready_project_and_summary_preserved')
  page.get_by_role('button',name='全部项目',exact=True).first.click()
  page.get_by_role('button',name='新建项目',exact=True).click()
  page.get_by_label('项目名称',exact=True).fill(result['fixture_title'])
  page.get_by_role('button',name='创建私有项目',exact=True).click()
  expect(page.get_by_role('heading',name=result['fixture_title'],exact=True)).to_be_visible()
  result['project_id']=page.evaluate('localStorage.getItem("ppt:selected-project")')
  result['checks'].append('real_project_creation_with_matching_csrf_header')
  page.get_by_label('目标受众',exact=True).fill('真实开发站 CSRF 与解析回归人员')
  page.get_by_role('button',name='保存要求',exact=True).click()
  expect(page.get_by_role('button',name='保存要求',exact=True)).to_be_disabled()
  result['checks'].append('real_settings_patch_with_matching_csrf_header')
  upload=page.get_by_label('上传源资料',exact=True)
  expect(upload).to_be_disabled()
  page.get_by_role('checkbox',name=re.compile('我同意将本项目必要的正文及图片')).check()
  upload.set_input_files({'name':'csrf-deploy-material.md','mimeType':'text/markdown','buffer':'# 真实静态包回归\n\n仅验证 CSRF header、隔离本地解析与摘要持久化，不调用外部模型。'.encode('utf-8')})
  expect(page.get_by_text('csrf-deploy-material.md',exact=True)).to_be_visible()
  expect(page.get_by_role('status',name='csrf-deploy-material.md解析状态')).to_have_text('解析完成',timeout=35000)
  summary=page.get_by_label('csrf-deploy-material.md文本摘要',exact=True)
  assert summary.text_content()==summaries[(result['project_id'],'csrf-deploy-material.md')][:2000]
  result['checks'].append('real_ack_and_multipart_upload_then_actual_ready_summary')
  page.reload(wait_until='networkidle')
  expect(page.get_by_role('heading',name=result['fixture_title'],exact=True)).to_be_visible()
  expect(page.get_by_label('目标受众',exact=True)).to_have_value('真实开发站 CSRF 与解析回归人员')
  expect(page.get_by_role('status',name='csrf-deploy-material.md解析状态')).to_have_text('解析完成')
  expect(page.get_by_label('csrf-deploy-material.md文本摘要',exact=True)).to_contain_text('隔离本地解析')
  stored=page.evaluate('Object.keys(localStorage)')
  assert all(key=='ppt:selected-project' or key.startswith('ppt:cursor:') for key in stored)
  result['checks'].append('reload_restores_actual_settings_ready_asset_and_only_ui_storage')
  page.get_by_role('tab',name=re.compile('沟通与方案')).click()
  page.get_by_role('checkbox',name=re.compile('我同意将本项目必要的正文及图片')).check()
  expect(page.get_by_role('heading',name='还没有规划方案',exact=True)).to_be_visible()
  page.get_by_role('button',name='开始规划',exact=True).click()
  expect(page.get_by_role('alert')).to_contain_text('执行器尚未接通')
  assert any(r['method']=='POST' and r['path'].endswith('/plans') and r['status']==503 for r in result['responses'])
  assert not page.get_by_role('radio').count()
  assert not page.get_by_role('button',name='生成演示稿',exact=True).count()
  result['checks'].append('actual_planning_503_with_csrf_no_fake_plan_or_generation')
  assert len(result['csrf_write_checks'])>=5 and all(item['csrf_header_matches_issued_token'] for item in result['csrf_write_checks'])
  result['checks'].append('all_actual_write_headers_match_latest_me_issued_token_without_logging_it')
  result['unexpected_console_errors']=[error for error in result['console_errors'] if '503 (Service Unavailable)' not in error]
  assert not result['page_errors'] and not result['unexpected_console_errors'] and not result.get('proxy_errors')
  result['checks'].append('no_javascript_or_unexpected_transport_console_errors')
  result['not_live_verified']=['real_plan_confirmation','formal_SSO_JWT_CSRF_proxy_chain','generation','PowerPoint_manual_acceptance','visual_acceptance']
  result['status']='passed'
  browser.close()

if __name__=='__main__':
 try:main()
 except Exception as error:
  result['status']='failed'
  result['failure_type']=type(error).__name__
  print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
  raise SystemExit(1)
 print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
