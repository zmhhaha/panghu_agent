"""Live parse-only DOM acceptance. Dedicated development API only; no fake responses.
Creates one uniquely named fixture; does not delete source data or alter services.
"""
import json
import re
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

PUBLIC = 'http://127.0.0.1:18505'
INTERNAL = 'http://ppt-service-webdev-api-20261021:8000'
EXISTING_PROJECT = '3e70a772-268b-4151-b6b9-ac2a740904ad'
result = {'fixture_title': 'PARSE-DOM-' + str(time.time_ns()), 'checks': [], 'responses': [], 'snapshots': [], 'console_errors': [], 'page_errors': []}


def check(name):
    result['checks'].append(name)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1360, 'height': 1000})
        page.set_default_timeout(20000)
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        page.on('console', lambda msg: result['console_errors'].append(msg.text) if msg.type == 'error' else None)

        fault = {'armed': False, 'count': 0}
        summaries = {}
        def forward(route):
            request = route.request
            parsed = urlsplit(request.url)
            if fault['armed'] and request.method == 'GET' and parsed.path == '/api/v1/projects/' + result.get('project_id', ''):
                fault['armed'] = False
                fault['count'] += 1
                result['responses'].append({'method':'GET','path':parsed.path,'status':'injected_network_disconnect'})
                route.abort('internetdisconnected')
                return
            headers = dict(request.headers)
            if request.method not in ('GET', 'HEAD'):
                headers['origin'] = PUBLIC
            try:
                response = route.fetch(url=INTERNAL + parsed.path + (('?' + parsed.query) if parsed.query else ''), headers=headers, timeout=10000)
            except Exception as error:
                result.setdefault('proxy_transport_errors', []).append({'path':parsed.path,'error_type':type(error).__name__})
                try:
                    route.abort('failed')
                except Exception:
                    pass
                return
            record = {'method': request.method, 'path': parsed.path, 'status': response.status}
            result['responses'].append(record)
            if request.method == 'GET' and re.fullmatch(r'/api/v1/projects/[^/]+', parsed.path) and response.status == 200:
                raw = response.json()
                for asset in raw.get('assets', []):
                    summaries[(raw['id'], asset['display_name'])] = asset.get('text_summary')
                result['snapshots'].append({'at': round(time.monotonic(), 3), 'project_id': raw['id'], 'revision': raw['revision'], 'assets': [{'id': a['id'], 'name': a['display_name'], 'status': a['parse_status'], 'summary_present': bool(a.get('text_summary')), 'error': a.get('parse_error')} for a in raw.get('assets', [])]})
            if request.method == 'POST' and parsed.path.endswith('/assets'):
                record['parse_status'] = response.json().get('parse_status') if response.status < 300 else 'upload_rejected'
            route.fulfill(response=response)

        page.route(PUBLIC + '/**', forward)
        page.goto(PUBLIC, wait_until='networkidle')
        expect(page.get_by_text('本地开发测试账户 · 正式 SSO 未接通；制稿执行器尚未接通，不会调用模型')).to_be_visible()
        expect(page.get_by_text('当前为试用版本，欢迎反馈使用问题与改进建议。')).to_be_visible()
        check('actual_parse_bundle_loaded_with_sso_test_and_powerpoint_gates')
        page.evaluate('(id)=>localStorage.setItem("ppt:selected-project",id)', EXISTING_PROJECT)
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading', name='Material acceptance 20261022T040417Z', exact=True)).to_be_visible()
        expect(page.locator('.plain-text').filter(has_text='公开材料解析验收')).to_be_visible()
        expect(page.locator('.assets [role=status]').first).to_have_text('解析完成')
        page.reload(wait_until='networkidle')
        expect(page.locator('.plain-text').filter(has_text='公开材料解析验收')).to_be_visible()
        check('existing_real_ready_summary_and_reload_persistence')
        page.get_by_role('button', name='全部项目', exact=True).first.click()
        page.get_by_role('button', name='新建项目', exact=True).click()
        page.get_by_label('项目名称', exact=True).fill(result['fixture_title'])
        page.get_by_role('button', name='创建私有项目', exact=True).click()
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        result['project_id'] = page.evaluate('localStorage.getItem("ppt:selected-project")')
        check('unique_real_fixture_project_created')
        uploader = page.get_by_label('上传源资料', exact=True)
        expect(uploader).to_be_disabled()
        page.get_by_role('checkbox', name=re.compile('我同意将本项目必要的正文及图片')).check()
        material = '# 本地解析真实 DOM 验收\n\n公开资料。<script>window.PARSE_INJECTION_EXECUTED=true</script>\n<img src=x onerror="window.PARSE_INJECTION_EXECUTED=true">\n仅验证隔离本地解析，不调用模型。\n' + ('受限摘要与状态更新检查。\n' * 120)
        uploader.set_input_files({'name': 'parse-dom-material.md', 'mimeType': 'text/markdown', 'buffer': material.encode('utf-8')})
        expect(page.get_by_text('parse-dom-material.md', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='刷新状态', exact=True)).to_be_enabled()
        status = page.get_by_role('status', name='parse-dom-material.md解析状态')
        result['initial_md_dom_status'] = status.inner_text()
        assert result['initial_md_dom_status'] in ['等待解析', '解析中', '正在停止解析'], 'Parser completed before the pending UI could be observed; do not claim transition coverage.'
        page.get_by_label('目标受众', exact=True).fill('尚未提交的解析轮询保护输入')
        before_poll = len([r for r in result['responses'] if r['method']=='GET' and r['path']==f"/api/v1/projects/{result['project_id']}"])
        fault['armed'] = True
        expect(page.get_by_role('alert')).to_contain_text('无法连接服务', timeout=15000)
        expect(status).to_have_text('解析完成', timeout=35000)
        result['injected_network_failures'] = fault['count']
        assert fault['count'] == 1
        check('explicit_test_network_disconnect_recovers_through_real_snapshot_poll')
        after_poll = len([r for r in result['responses'] if r['method']=='GET' and r['path']==f"/api/v1/projects/{result['project_id']}"])
        assert after_poll > before_poll, 'No actual GET snapshot was observed during automatic completion.'
        expect(page.get_by_label('目标受众', exact=True)).to_have_value('尚未提交的解析轮询保护输入')
        expect(page.get_by_role('button', name='保存要求', exact=True)).to_be_enabled()
        check('actual_pending_to_ready_via_automatic_poll_preserves_unsaved_settings')
        summary = page.get_by_label('parse-dom-material.md文本摘要', exact=True)
        expect(summary).to_contain_text('window.PARSE_INJECTION_EXECUTED=true')
        expected_summary = summaries[(result['project_id'], 'parse-dom-material.md')]
        assert summary.text_content() == expected_summary[:2000]
        result['summary_character_count'] = len(summary.text_content())
        assert len(summary.inner_text()) <= 2000
        assert not page.locator('.assets script').count()
        assert not page.locator('.assets img').count()
        assert page.evaluate('window.PARSE_INJECTION_EXECUTED === undefined')
        assert not page.get_by_role('progressbar').count()
        expect(page.get_by_text('材料解析采用隔离本地沙箱；下方状态来自服务端，不会调用模型。')).to_be_visible()
        check('bounded_plain_escaped_summary_no_injection_no_fake_progress')
        # Do not save the draft setting: reload should restore server values and retain ready material.
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('heading', name=result['fixture_title'], exact=True)).to_be_visible()
        expect(page.get_by_label('parse-dom-material.md文本摘要', exact=True)).to_contain_text('window.PARSE_INJECTION_EXECUTED=true')
        assert page.get_by_label('parse-dom-material.md文本摘要', exact=True).text_content() == summaries[(result['project_id'], 'parse-dom-material.md')][:2000]
        expect(page.get_by_role('status', name='parse-dom-material.md解析状态')).to_have_text('解析完成')
        check('new_ready_summary_survives_reload_from_actual_storage')
        page.get_by_role('checkbox', name=re.compile('我同意将本项目必要的正文及图片')).check()
        page.get_by_label('上传源资料', exact=True).set_input_files({'name':'parse-dom-damaged.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'})
        expect(page.get_by_text('parse-dom-damaged.pdf', exact=True)).to_be_visible()
        expect(page.get_by_role('status', name='parse-dom-damaged.pdf解析状态')).to_have_text('解析失败', timeout=35000)
        failed_asset = page.locator('.assets li').filter(has_text='parse-dom-damaged.pdf')
        failure = failed_asset.get_by_role('alert').inner_text()
        result['failed_pdf_message'] = failure
        assert 'PDF' in failure or '解析' in failure
        assert not re.search(r'Traceback|/work/|/tmp/|storage_key|lease|token|stack', failure, re.I)
        assert not failed_asset.locator('.plain-text').count()
        check('corrupt_signature_pdf_actual_failed_safe_reason_no_stale_summary')
        page.get_by_role('tab', name=re.compile('沟通与方案')).click()
        page.get_by_role('button', name='开始规划', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('执行器尚未接通')
        assert any(r['method']=='POST' and r['path'].endswith('/plans') and r['status']==503 for r in result['responses'])
        assert not page.get_by_role('button', name='生成演示稿', exact=True).count()
        check('actual_plan_guard_503_no_model_or_fake_success')
        result['unexpected_console_errors'] = [e for e in result['console_errors'] if '503 (Service Unavailable)' not in e and 'ERR_INTERNET_DISCONNECTED' not in e]
        assert not result['page_errors'], result['page_errors']
        assert not result['unexpected_console_errors'], result['unexpected_console_errors']
        check('no_page_errors_or_unexpected_console_errors')
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
