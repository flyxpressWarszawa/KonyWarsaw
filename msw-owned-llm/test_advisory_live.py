"""Run only inside an isolated CI model container, not against the public API."""
import json
import advisory
import gateway
class Capture(gateway.ModelRuntime):
    def backend(self,path,payload=None):
        out=super().backend(path,payload)
        if path=='/completion': self.last=out
        return out
r=Capture()
passed=0
for name,version,adapters in [('MSW AI V7','7.1.0',1),('MSW Fotonika','0.8.0',0),('404 Solution','4.3.6',0)]:
    d={'name':name,'version':version,'active':True,'registered_adapters':adapters,'previous_transport_failures':0}
    prompt=advisory.PREFIX+json.dumps(d)+advisory.SUFFIX
    try:
        out=advisory.generate(r,prompt)
        print(json.dumps({'case':name,'passed':True,'result':out,'model_is_real':True,'scope':'surface_quality_not_business_success'}),flush=True)
        passed+=1
    except ValueError as e:
        print(json.dumps({'case':name,'passed':False,'reason':str(e),'synthetic_completion':getattr(r,'last',{}).get('content')}),flush=True)
assert passed==3, 'One or more real advisory cases failed; do not deploy.'
