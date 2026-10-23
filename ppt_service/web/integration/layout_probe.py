import json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=b.new_page(viewport={'width':320,'height':844})
 def forward(route):
  u=urlsplit(route.request.url)
  route.fulfill(response=route.fetch(url='http://ppt-service-webdev-api-20261021:8000'+u.path+('?' +u.query if u.query else '')))
 page.route('http://127.0.0.1:18505/**',forward)
 page.add_init_script("localStorage.setItem('ppt:selected-project','05fcfb5e-1646-4347-a4fb-b9975e350737')")
 page.goto('http://127.0.0.1:18505',wait_until='networkidle')
 expect(page.get_by_role('heading',name='DOM-INTEGRATION-1792603086012044594')).to_be_visible()
 print(json.dumps(page.evaluate("""()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('main *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0 && r.right>innerWidth+1}).slice(0,12).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,100)}))})"""),ensure_ascii=False))
 b.close()
