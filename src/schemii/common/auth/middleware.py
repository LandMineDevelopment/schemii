"""Authenticate all application APIs; report viewers use an explicit allowlist."""
import re
import os
import asyncio
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from schemii.common.metadata.models import Principal
from .service import COOKIE

PUBLIC = {'/api/v1/auth/status','/api/v1/auth/setup','/api/v1/auth/login','/api/v1/readiness'}
REPORT_READ = re.compile(r'^/api/v1/schemer/dashboards(?:/[^/]+(?:/context)?)?$')
REPORT_RUN = re.compile(r'^/api/v1/schemer/dashboards/[^/]+/(?:parameter-values|executions(?:/stream)?|tiles/[^/]+/(?:plan|parameter-values|executions(?:/stream)?|export))$')
RESULT_READ = re.compile(r'^/api/v1/common/query-executions/[^/]+(?:/activity|/results/[^/]+(?:/rows|/export\.csv)?)?$')


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        service=request.app.state.auth
        if not service.enabled: return await call_next(request)
        path=request.url.path
        protected=path.startswith(('/api/','/_developer/')) or path in {'/api-map','/db-map','/system-map','/openapi.json','/docs','/redoc'}
        if not protected: return await call_next(request)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            referer=request.headers.get('referer')
            if not origin and referer:
                parsed=urlsplit(referer)
                origin=f'{parsed.scheme}://{parsed.netloc}'
            # Same-origin only. The TLS proxy preserves the browser Host header.
            expected='https://'+request.headers.get('host','')
            trusted=set(os.getenv('SCHEMII_TRUSTED_ORIGINS','https://localhost:8001,https://omarchy.taile4f57f.ts.net').split(','))
            if origin != expected and origin not in trusted:
                return JSONResponse({'detail':'Same-origin request required'},status_code=403)
        if path in PUBLIC:
            response=await call_next(request)
            response.headers['Cache-Control']='no-store'
            return response
        user=await asyncio.to_thread(service.resolve,request.cookies.get(COOKIE))
        if not user: return JSONResponse({'detail':'Sign in required'},status_code=401)
        request.state.principal=Principal(user_id=user['id'],authentication_source='session')
        if path.startswith(('/api/v1/admin/','/_developer/')) or path in {'/api-map','/db-map','/system-map','/openapi.json','/docs','/redoc'} or path.startswith('/api/v1/developer'):
            allowed=user['is_admin']
        else:
            allowed=user['is_admin'] or 'author' in await asyncio.to_thread(service.capabilities,user['id'])
            allowed=allowed or path in {'/api/v1/session','/api/v1/auth/me','/api/v1/auth/logout','/api/v1/auth/change-password'}
            allowed=allowed or (request.method=='GET' and bool(REPORT_READ.fullmatch(path)))
            allowed=allowed or (request.method=='POST' and bool(REPORT_RUN.fullmatch(path)))
            allowed=allowed or (request.method == 'DELETE' and bool(RESULT_READ.fullmatch(path)))
        if not allowed: return JSONResponse({'detail':'Your role does not permit this action'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
