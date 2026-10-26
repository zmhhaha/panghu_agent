"""Small structural regressions supplementing the actual local-only network test."""
import unittest
from unittest.mock import patch
from ppt_service.control.pods import Firewall,secure_container
from test_network_isolation_preflight import digest_rules
class Rules(unittest.TestCase):
    def test_rule_fingerprint_ignores_comments_and_counters_not_policy(self):
        first=b'# generated now\n:INPUT ACCEPT [1:20]\n-A INPUT -j ACCEPT\n'
        second=b'# generated later\n:INPUT ACCEPT [900:3000]\n-A INPUT -j ACCEPT\n'
        self.assertEqual(digest_rules(first),digest_rules(second));self.assertNotEqual(digest_rules(first),digest_rules(first.replace(b'-j ACCEPT',b'-j DROP')))
    def test_actual_firewall_has_exact_allow_before_default_drops(self):
        calls=[]
        def record(args,**kwargs):calls.append(args);return 0,b''
        uid='01234567-89ab-cdef-0123-456789abcdef';fw=Firewall(record);wb,eb,chain=fw.names(uid)
        with patch('pathlib.Path.read_text',return_value='1'):fw.install(uid,'62.234.50.20')
        appends=[args[4:] for args in calls if args[3]=='-A']
        self.assertEqual(len(appends),8);self.assertEqual(appends[0],[chain,'-i',wb,'-s','172.30.240.3','-d','172.30.240.2','-p','tcp','--dport','8080','-j','ACCEPT'])
        self.assertIn('ESTABLISHED,RELATED',appends[1]);self.assertEqual(appends[2],[chain,'-i',wb,'-j','DROP']);self.assertIn('62.234.50.20',appends[3]);self.assertIn('443',appends[3])
    def test_remove_never_flushes_any_global_chain(self):
        calls=[];fw=Firewall(lambda args,**kwargs:(calls.append(args) or (0,b'')));uid='01234567-89ab-cdef-0123-456789abcdef';fw.remove(uid);chain=fw.names(uid)[2]
        flushes=[args[4] for args in calls if args[3]=='-F'];self.assertEqual(flushes,[chain]);self.assertNotIn('DOCKER-USER',flushes);self.assertNotIn('INPUT',flushes)
    def test_fixture_uses_production_nonroot_readonly_no_caps_template(self):
        args=secure_container('owned','uid',memory='256m',cpus='0.5')
        for value in ('10000:10000','--read-only','ALL','no-new-privileges','127.0.0.1','net.ipv6.conf.all.disable_ipv6=1'):self.assertIn(value,args)
        self.assertNotIn('--privileged',args);self.assertNotIn('--network=host',args)
if __name__=='__main__':unittest.main()
