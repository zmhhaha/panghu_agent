"""Trusted development deployment checks. Never prints database configuration."""
import argparse,hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import create_engine,text

def snapshot(engine):
    names=('projects','assets','plans','jobs','versions','checkpoints','job_events','idempotency_keys','usage_records','user_quotas')
    with engine.connect() as c:
        counts={name:c.scalar(text('SELECT count(*) FROM '+name)) for name in names}
        projects=[dict(r) for r in c.execute(text('SELECT id, issuer, subject, title, settings, revision FROM projects ORDER BY id')).mappings()]
        assets=[dict(r) for r in c.execute(text('SELECT id, project_id, storage_key, display_name, sha256, media_type, bytes FROM assets ORDER BY id')).mappings()]
        revision=c.scalar(text('SELECT max(revision) FROM api_schema_revision'))
    return {'schema_revision':revision,'counts':counts,'projects':projects,'assets':assets}
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str));path.chmod(0o600)
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('snapshot','migrate','create','check','status'));p.add_argument('--directory',type=Path,default=Path('/backup'));p.add_argument('--url',default='http://ppt-service-webdev-api-20261021:8000');args=p.parse_args();args.directory.mkdir(parents=True,exist_ok=True)
    if args.action in ('snapshot','migrate','status'):
        engine=create_engine(os.environ['DATABASE_URL'],pool_pre_ping=True)
        if args.action=='snapshot':
            before=snapshot(engine);save(args.directory/'before.json',before);print(json.dumps({'action':'before','schema_revision':before['schema_revision'],'counts':before['counts']}))
        elif args.action=='migrate':
            from ppt_service.server.migrate import migrate
            before=json.loads((args.directory/'before.json').read_text());migrate(engine);after=snapshot(engine)
            for table,count in before['counts'].items():assert after['counts'][table]>=count,table+' count decreased'
            old={a['id']:a for a in before['assets']};new={a['id']:a for a in after['assets']};assert all(new.get(k)==v for k,v in old.items()),'source mapping changed'
            previous={r['id']:r for r in before['projects']};current={r['id']:r for r in after['projects']};assert all(current.get(k)==v for k,v in previous.items()),'project inputs changed'
            assert after['schema_revision']==2;save(args.directory/'after.json',after);print(json.dumps({'action':'after','schema_revision':2,'counts':after['counts'],'original_assets_preserved':len(old),'original_projects_preserved':len(previous)}))
        else:
            with engine.connect() as c:rows=[dict(row) for row in c.execute(text('SELECT parse_status, count(*) AS count FROM assets WHERE removed=false GROUP BY parse_status ORDER BY parse_status')).mappings()]
            state=snapshot(engine)
            print(json.dumps({'schema_revision':state['schema_revision'],'counts':state['counts'],'asset_parse_counts':rows}))
        engine.dispose();return
    import httpx
    with httpx.Client(base_url=args.url,timeout=20,trust_env=False,headers={'Origin':'http://127.0.0.1:18505'}) as c:
        response=c.get('/healthz');assert response.status_code==200;assert response.json()=={'status':'ok'}
        if args.action=='create':
            title='Material acceptance '+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            response=c.post('/api/v1/projects',json={'title':title,'page_count':3,'external_model_ack':True});assert response.status_code==201;project=response.json()
            source='公开材料解析验收。只验证本地隔离解析、持久化与网页展示，不调用外部模型。'
            response=c.post('/api/v1/projects/'+project['id']+'/assets',files={'file':('material-acceptance.md',source.encode(),'text/markdown')});assert response.status_code==201;asset=response.json();assert asset['parse_status']=='queued';assert 'text_summary' in asset
            state={'project_id':project['id'],'asset_id':asset['id'],'sha256':asset['sha256'],'source':source};save(args.directory/'acceptance.json',state)
            print(json.dumps({'project_id':project['id'],'asset_id':asset['id'],'parse_status':asset['parse_status']}))
        else:
            state=json.loads((args.directory/'acceptance.json').read_text());response=c.get('/api/v1/projects/'+state['project_id']);assert response.status_code==200;project=response.json();asset=next(a for a in project['assets'] if a['id']==state['asset_id'])
            assert asset['parse_status']=='ready',asset['parse_status'];assert asset['text_summary']==state['source'];assert asset['sha256']==state['sha256'];assert asset['parse_error'] is None
            response=c.post('/api/v1/projects/'+state['project_id']+'/plans',json={'expected_revision':project['revision']},headers={'Idempotency-Key':'material-acceptance-no-model'});assert response.status_code==503;assert response.json()['detail']['code']=='execution_not_connected'
            index=c.get('/').text;assert 'index-Cb8shtpo.js' in index and 'index-C0RgSHL_.css' in index
            save(args.directory/'acceptance-result.json',{'project_id':project['id'],'asset_id':asset['id'],'parse_status':'ready','text_summary':asset['text_summary'],'sha256_unchanged':True,'model_execution_http':503,'frontend_build':'Cb8shtpo/C0RgSHL_'})
            print(json.dumps({'project_id':project['id'],'asset_id':asset['id'],'parse_status':'ready','text_summary':asset['text_summary'],'sha256_unchanged':True,'model_execution_http':503,'frontend_build':'Cb8shtpo/C0RgSHL_'},ensure_ascii=False))
if __name__=='__main__':main()
