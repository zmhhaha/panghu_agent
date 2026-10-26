"""Bound request bytes before multipart parsing/spooling, including chunked uploads."""
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
class BodyLimitMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method') not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        upload=scope.get('path','').endswith('/assets') and scope.get('method')=='POST'
        limit=26*1024**2 if upload else 128*1024
        headers=dict(scope.get('headers',[]))
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:
            return await JSONResponse({'detail':{'code':'invalid_content_length'}},400)(scope,receive,send)
        if declared<0 or declared>limit:
            return await JSONResponse({'detail':{'code':'request_too_large'}},413)(scope,receive,send)
        count=0
        async def bounded_receive():
            nonlocal count
            message=await receive()
            if message['type']=='http.request':
                count+=len(message.get('body',b''))
                if count>limit:raise HTTPException(413,detail={'code':'request_too_large'})
            return message
        return await self.app(scope,bounded_receive,send)
