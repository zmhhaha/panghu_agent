import os,time,gradio as gr,requests
API_BASE=os.getenv('API_BASE','http://api.bingbichunqiu-agent.svc.cluster.local')
def do_bingbichunqiu(text,request=None):
 text=(text or '').strip()
 if not text:return '先落一笔，史官不能对着空白竹简起笔。'
 try:
  r=requests.post(f'{API_BASE}/bingbichunqiu_agent',json={'text':text},timeout=10);r.raise_for_status();i=r.json()['id']
  for _ in range(120):
   d=requests.get(f'{API_BASE}/bingbichunqiu_agent/{i}',timeout=10).json()
   if d['status']=='done':return d.get('report','')
   if d['status']=='failed':return '❌ '+d.get('error','未知错误')
   time.sleep(5)
  return '⏰ 查考未完，请稍后再来。'
 except Exception as e:return f'❌ {e}'
with gr.Blocks(title='📜 秉笔春秋',theme=gr.themes.Soft(primary_hue='blue',secondary_hue='stone')) as demo:
 gr.Markdown('# 📜 秉笔春秋\n以史官的眼光读人物、制度与世事，分清史实、传说与后人评价。');t=gr.Textbox(lines=4);b=gr.Button('📜 请史官落笔');o=gr.Markdown();b.click(do_bingbichunqiu,t,o)
if __name__=='__main__':demo.launch(server_name='0.0.0.0',server_port=int(os.getenv('GRADIO_SERVER_PORT','7869')))
