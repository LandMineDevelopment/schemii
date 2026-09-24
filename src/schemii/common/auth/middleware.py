"""Authenticate APIs and enforce product capabilities at the HTTP boundary."""
import re
import os
import asyncio
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from schemii.common.metadata.models import Principal
from .service import COOKIE, PRODUCT_CAPABILITIES, SCHEMER_AUTHOR, PROVISION_CAPABILITY

PUBLIC = {'/api/v1/auth/status','/api/v1/auth/setup','/api/v1/auth/login','/api/v1/readiness'}
REPORT_READ = re.compile(r'^/api/v1/schemer/dashboards(?:/[^/]+(?:/context)?)?$')
REPORT_RUN = re.compile(r'^/api/v1/schemer/dashboards/[^/]+/(?:parameter-values|executions(?:/stream)?|tiles/[^/]+/(?:plan|parameter-values|executions(?:/stream)?|export))$')
SCHEMER_AI = re.compile(r'^/api/v1/schemer/ai(?:/settings|/chats(?:/[^/]+(?:/(?:messages|preferences|approval|cancel))?)?)?$')
SELF_AI = re.compile(r'^/api/v1/ai/(?:status|credentials/[^/]+|prototype/(?:credentials|catalog|login|logins/[^/]+))$')
RESULT_READ = re.compile(r'^/api/v1/common/query-executions/[^/]+(?:/activity|/results/[^/]+(?:/rows|/export\.csv)?)?$')
ACCOUNT_ENDPOINTS = {'/api/v1/session','/api/v1/auth/me','/api/v1/auth/logout','/api/v1/auth/change-password'}
DEVELOPER_ENDPOINTS = {'/api-map','/db-map','/system-map','/openapi.json','/docs','/redoc'}


def permits_api(path, method, capabilities):
    rights = set(capabilities)
    author_products = bool(rights.intersection({'schemii:access','schemoo:access'})) or (
        'schemer:access' in rights and SCHEMER_AUTHOR in rights)
    if path in ACCOUNT_ENDPOINTS: return True
    if path.startswith(('/api/v1/admin/','/_developer/','/api/v1/developer')) or path in DEVELOPER_ENDPOINTS:
        return PROVISION_CAPABILITY in rights
    if path.startswith('/api/v1/schemii/'):
        return 'schemii:access' in rights
    if path.startswith('/api/v1/schemoo/'):
        return 'schemoo:access' in rights
    if path.startswith('/api/v1/schemer/'):
        if SCHEMER_AUTHOR in rights and 'schemer:access' in rights: return True
        if 'schemer:access' not in rights: return False
        if SCHEMER_AI.fullmatch(path): return True
        return ((method in {'GET','HEAD'} and bool(REPORT_READ.fullmatch(path)))
                or (method == 'POST' and bool(REPORT_RUN.fullmatch(path))))
    if path == '/api/v1/connections' or path.startswith('/api/v1/connections/'):
        return author_products
    if path.startswith('/api/v1/common/query-executions/'):
        return author_products or (method == 'DELETE' and 'schemer:access' in rights
                                   and bool(RESULT_READ.fullmatch(path)))
    if path == '/api/v1/ai' or path.startswith('/api/v1/ai/'):
        return author_products or ('schemer:access' in rights and bool(SELF_AI.fullmatch(path)))
    if path == '/api/v1/activity':
        return bool(rights.intersection(PRODUCT_CAPABILITIES))
    return False


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        service=request.app.state.auth
        if not service.enabled: return await call_next(request)
        path=request.url.path
        protected=path.startswith(('/api/','/_developer/')) or path in DEVELOPER_ENDPOINTS
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
        capabilities=await asyncio.to_thread(service.capabilities,user['id'])
        allowed=permits_api(path,request.method,capabilities)
        if not allowed: return JSONResponse({'detail':'Your role does not permit this action'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
