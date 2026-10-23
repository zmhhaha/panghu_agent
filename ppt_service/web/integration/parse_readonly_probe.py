"""Read-only scoped probe; outputs only owned fixture parse state, no headers or logs."""
import json
import time
import urllib.request
import urllib.error
BASE='http://127.0.0.1:18505'
paths=['/api/v1/me','/api/v1/projects/204f3776-0e69-4b5b-8066-715b98208372','/api/v1/projects/3e70a772-268b-4151-b6b9-ac2a740904ad']
for path in paths:
 start=time.monotonic()
 try:
  with urllib.request.urlopen(BASE+path,timeout=8) as response:
   body=json.load(response)
   record={'path':path,'status':response.status,'elapsed':round(time.monotonic()-start,3)}
   if '/projects/' in path: record.update({'revision':body.get('revision'),'stage':body.get('stage'),'assets':[{'id':a['id'],'parse_status':a['parse_status'],'parse_error':a.get('parse_error'),'summary_present':bool(a.get('text_summary'))} for a in body.get('assets',[])]})
   else: record['development_identity']=body.get('issuer')=='urn:ppt:development'
   print(json.dumps(record,ensure_ascii=False),flush=True)
 except Exception as error:
  print(json.dumps({'path':path,'elapsed':round(time.monotonic()-start,3),'error_type':type(error).__name__},ensure_ascii=False),flush=True)
