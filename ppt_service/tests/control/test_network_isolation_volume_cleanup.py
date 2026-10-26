"""Import-safe cleanup regressions; never touch old unidentified volumes."""
import datetime,json,time,unittest
from test_network_isolation_live import cleanup_containers,own_config
class Fake:
    def __init__(self,remaining=False,unavailable=False,volume=False):
        self.calls=[];self.remaining=remaining;self.unavailable=unavailable;self.volume=volume
    def call(self,*args,**kwargs):
        self.calls.append(args)
        if args[0]=='ps':
            if self.unavailable:raise RuntimeError('daemon unavailable')
            return 0,b'a'*64 if self.remaining else b''
        if args[:2]==('volume','ls'):return 0,b'b'*64 if self.volume else b''
        return 0,b''
class Cleanup(unittest.TestCase):
    def test_exact_cid_rmv_and_volume_absence(self):
        d=Fake();stopped,errors,removed=cleanup_containers(d,['a'*64],[{'name':'b'*64}])
        self.assertTrue(stopped);self.assertEqual(errors,[]);self.assertEqual(removed,{'b'*64:True})
        self.assertEqual(d.calls[0],('rm','--force','--volumes','a'*64))
    def test_unknown_writer_cannot_release_firewall(self):
        for d in (Fake(remaining=True),Fake(unavailable=True)):
            stopped,errors,_=cleanup_containers(d,['a'*64],[])
            self.assertFalse(stopped);self.assertTrue(errors)
    def test_remaining_volume_fails_cleanup(self):
        _,errors,removed=cleanup_containers(Fake(volume=True),['a'*64],[{'name':'b'*64}])
        self.assertIn('owned_volume_remains',errors);self.assertFalse(removed['b'*64])
    def test_volume_ownership_rejects_foreign_reference(self):
        class Inspect:
            def call(self,*args,**kwargs):
                if args[0]=='inspect':
                    if 'HostConfig' in args[2]:value={}
                    elif 'Mounts' in args[2]:value=[{'Type':'volume','Destination':'/opt/data','Name':'b'*64,'Source':'/owned'}]
                    else:return 0,b'10000:10000'
                elif args[0]=='volume':value=[{'CreatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'Mountpoint':'/owned','Labels':{'com.docker.volume.anonymous':''}}]
                else:return 0,('a'*64+'\n'+'c'*64).encode()
                return 0,json.dumps(value).encode()
        with self.assertRaisesRegex(RuntimeError,'ownership_unproven'):own_config(Inspect(),'a'*64,time.time())
if __name__=='__main__':unittest.main()
