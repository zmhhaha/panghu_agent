"""Real development-site DOM smoke. No mocks, model calls or screenshots.
Run only against the dedicated local development API; creates one uniquely named fixture.
"""
import json
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

PUBLIC = 'http://127.0.0.1:18505'
INTERNAL = 'http://ppt-service-webdev-api-20261021:8000'
result = {'fixture_title': 'DOM-INTEGRATION-' + str(time.time_ns()), 'checks': [], 'responses': [], 'console_errors': [], 'page_errors': []}

def check(name):
    result['checks'].append(name)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        page.on('console', lambda msg: result['console_errors'].append(msg.text) if msg.type == 'error' else None)
        def forward(route):
            original = route.request
            parsed = urlsplit(original.url)
            target = INTERNAL + parsed.path + (('?' + parsed.query) if parsed.query else '')
            headers = dict(original.headers)
            # Keep the browser-facing Origin for the actual API CSRF validation.
            if original.method not in ('GET', 'HEAD'):
                headers['origin'] = PUBLIC
            response = route.fetch(url=target, headers=headers)
            result['responses'].append({'method': original.method, 'path': parsed.path, 'status': response.status})
            route.fulfill(response=response)
        page.route(PUBLIC + '/**', forward)
        page.goto(PUBLIC, wait_until='networkidle')
        expect(page.get_by_role('heading', name='我的项目')).to_be_visible()
        expect(page.get_by_text('开发测试中', exact=True)).to_be_visible()
        expect(page.get_by_text('本地开发测试账户 · 正式 SSO 未接通；执行器尚未接通，不会调用模型')).to_be_visible()
        expect(page.get_by_text('当前为试用版本，欢迎反馈使用问题与改进建议。')).to_be_visible()
        check('hydration_and_explicit_development_release_gate')
        page.get_by_role('button', name='新建项目', exact=True).click()
        page.get_by_label('项目名称', exact=True).fill(result['fixture_title'])
        page.get_by_role('button', name='创建私有项目', exact=True).click()
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        check('real_project_create_and_workspace')
        uploader = page.get_by_label('上传源资料', exact=True)
        expect(uploader).to_be_disabled()
        check('upload_requires_explicit_external_model_consent')
        page.get_by_role('checkbox', name='我同意将本项目必要的正文及图片发送给服务指定的外部模型，用于规划、生成与修改。请仅上传有权使用的资料。').check()
        uploader.set_input_files({'name': 'dom-integration-material.md', 'mimeType': 'text/markdown', 'buffer': '# 浏览器集成测试\n\n仅用于测试项目和上传；不启动真实模型。'.encode('utf-8')})
        expect(page.get_by_text('dom-integration-material.md', exact=True)).to_be_visible(timeout=20000)
        check('real_ack_patch_and_multipart_upload')
        result['project_id'] = page.evaluate("localStorage.getItem('ppt:selected-project')")
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        expect(page.get_by_text('dom-integration-material.md', exact=True)).to_be_visible()
        check('reload_restores_actual_project_and_uploaded_asset')
        # Change a real setting without starting execution.
        page.get_by_label('目标受众', exact=True).fill('浏览器 DOM 集成验证人员')
        page.get_by_role('button', name='保存要求', exact=True).click()
        expect(page.get_by_role('button', name='保存要求', exact=True)).to_be_disabled()
        check('real_settings_patch')
        page.get_by_role('tab', name=re.compile('沟通与方案')).click()
        page.get_by_role('checkbox', name='我同意将本项目必要的正文及图片发送给服务指定的外部模型，用于规划、生成与修改。请仅上传有权使用的资料。').check()
        page.get_by_role('button', name='开始规划').click()
        expect(page.get_by_role('alert')).to_contain_text('执行器尚未接通', timeout=15000)
        expect(page.get_by_role('heading', name='还没有规划方案')).to_be_visible()
        assert not page.get_by_role('button', name='生成演示稿', exact=True).count()
        check('real_execution_503_actionable_error_and_no_fake_success')
        page.get_by_role('button', name='全部项目', exact=True).first.click()
        expect(page.get_by_role('heading', name='我的项目', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        check('real_project_list_contains_created_fixture')
        # Return to project before responsive bounds checks.
        page.get_by_role('button', name=result['fixture_title'], exact=False).last.click()
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        for width in (390, 320):
            page.set_viewport_size({'width': width, 'height': 844})
            for tab in ('资料与要求', '沟通与方案', '预览与修改'):
                page.get_by_role('tab', name=re.compile(tab)).click()
                bounds = page.evaluate('''() => ({width:innerWidth, scroll:document.documentElement.scrollWidth, overflow:[...document.querySelectorAll('main *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0 && (r.right>innerWidth+1 || r.left< -1)}).slice(0,12).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,100)}))})''')
                result.setdefault('bounds', []).append({'tab':tab, **bounds})
                assert bounds['scroll'] <= width + 1, f'Horizontal overflow at {width}: {bounds}'
            check(f'narrow_{width}px_document_bounds')
        # A 503 HTTP resource console error is browser-native and expected; JS errors are not.
        result['unexpected_console_errors'] = [e for e in result['console_errors'] if '503 (Service Unavailable)' not in e]
        assert not result['page_errors'], result['page_errors']
        assert not result['unexpected_console_errors'], result['unexpected_console_errors']
        check('no_javascript_or_unexpected_console_errors')
        assert any(r['method']=='POST' and r['path'].endswith('/plans') and r['status']==503 for r in result['responses'])
        assert any(r['method']=='POST' and r['path'].endswith('/assets') and 200<=r['status']<300 for r in result['responses'])
        result['status'] = 'passed'
        browser.close()

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        result['status'] = 'failed'
        result['failure'] = str(error)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
