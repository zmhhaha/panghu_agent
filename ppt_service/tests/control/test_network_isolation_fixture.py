"""Fixed no-model local HTTP counters and bounded socket probes for one test."""
from __future__ import annotations
import argparse,http.client,http.server,json,os,signal,socket,socketserver,threading,time
from pathlib import Path
BODY=b'PPT_LOCAL_NETWORK_FIXTURE_V1'
URL='/_ppt_network_fixture'
def write(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value));os.replace(temporary,path)
def call(host,port):
    connection=http.client.HTTPConnection(host,port,timeout=0.8)
    try:
        connection.request('GET',URL);response=connection.getresponse();data=response.read(128)
        return {'reachable':True,'fixed_body':data==BODY,'http_status':response.status}
    except OSError as exc:return {'reachable':False,'error_type':type(exc).__name__,'errno':exc.errno}
    finally:connection.close()
class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
    daemon_threads=True;allow_reuse_address=False
    def server_bind(self):
        socketserver.TCPServer.server_bind(self);self.server_name='PPT_LOCAL_FIXTURE';self.server_port=self.server_address[1]
def serve(bind,ports,out):
    out.mkdir(exist_ok=True);lock=threading.Lock();counts={};servers=[];stop=threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path!=URL:self.send_response_only(404);self.end_headers();return
            with lock:
                key=str(self.server.server_port);counts[key]+=1;write(out/'counts.json',{'counts':counts})
            self.send_response_only(200);self.send_header('Content-Length',str(len(BODY)));self.end_headers();self.wfile.write(BODY)
    for port in ports:
        server=Server((bind,port),Handler);counts[str(server.server_port)]=0;servers.append(server)
        threading.Thread(target=server.serve_forever,daemon=True).start()
    write(out/'counts.json',{'counts':counts});write(out/'ready.json',{'ready':True,'ports':[s.server_port for s in servers]})
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    try:stop.wait(45)
    finally:
        for server in servers:server.shutdown();server.server_close()

def worker(host_port,uid,out):
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    root_write=False
    try:
        with open('/ppt-network-fixture-write-proof','x') as stream:stream.write('fixed fixture')
        root_write=True;os.unlink('/ppt-network-fixture-write-proof')
    except OSError:pass
    dns_state={'resolved':False,'completed':False}
    def dns_probe():
        try:socket.getaddrinfo('ppt-'+uid+'.invalid',8080);dns_state['resolved']=True
        except socket.gaierror:pass
        finally:dns_state['completed']=True
    dns_thread=threading.Thread(target=dns_probe,daemon=True);dns_thread.start();dns_thread.join(timeout=1.5);dns_failed=not dns_state['resolved']
    ipv6=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);ipv6.settimeout(0.5)
    try:ipv6.connect(('::1',8080));ipv6_failed=False
    except OSError:ipv6_failed=True
    finally:ipv6.close()
    result={'allowed_proxy_8080':call('172.30.240.2',8080),'blocked_proxy_8081':call('172.30.240.2',8081),'blocked_proxy_egress_ip':call('172.30.241.2',8080),'blocked_host_sentinel':call('172.30.240.1',host_port),'dns_resolution_failed':dns_failed,'ipv6_connection_failed':ipv6_failed,'ipv6_disabled':Path('/proc/sys/net/ipv6/conf/all/disable_ipv6').read_text().strip()=='1','uid':os.getuid(),'caps_zero':all(int(status[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapBnd','CapAmb')),'no_new_privileges':status['NoNewPrivs'].strip()=='1','root_write_blocked':not root_write,'docker_socket_absent':not Path('/var/run/docker.sock').exists()}
    write(out/'worker-probes.json',result)
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['proxy','sentinel','host','worker','proxy-probe','health'],required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--host-port',type=int);p.add_argument('--uid',default='fixture');p.add_argument('--ports',nargs='*',type=int);a=p.parse_args()
    if a.mode in ('proxy','sentinel','host'):serve('172.30.240.1' if a.mode=='host' else '0.0.0.0',[0] if a.mode=='host' else [8080,8081] if a.mode=='proxy' else [8080],a.out)
    elif a.mode=='worker':print(json.dumps(worker(a.host_port,a.uid,a.out)))
    elif a.mode=='health':print(json.dumps({str(port):call('127.0.0.1',port) for port in a.ports}))
    else:
        result={'nonallow_egress_peer':call('172.30.241.3',8080),'host_sentinel':call('172.30.240.1',a.host_port)};write(a.out/'proxy-probes.json',result);print(json.dumps(result))
