"""Admin-only usage analytics UI."""
import hashlib, hmac, os, time
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import user_tracking

app=FastAPI(title="GPU Profiler Analytics",docs_url=None,redoc_url=None)
COOKIE="gpu_debug_admin"
def _sig(day): return hmac.new(os.getenv('GPU_MONITOR_ADMIN_PASSWORD','0000').encode(),f'admin:{day}'.encode(),hashlib.sha256).hexdigest()
def _auth(r):
    token=r.cookies.get(COOKIE,''); day=int(time.time()//86400)
    return any(hmac.compare_digest(token,_sig(x)) for x in (day,day-1))
class Login(BaseModel): username:str; password:str
@app.post('/api/login')
async def login(body:Login,response:Response):
    password=os.getenv('GPU_MONITOR_ADMIN_PASSWORD','0000')
    if body.username.casefold()!='admin' or not hmac.compare_digest(body.password,password): raise HTTPException(403,'Доступ разрешён только администратору')
    response.set_cookie(COOKIE,_sig(int(time.time()//86400)),httponly=True,samesite='strict',max_age=86400)
    return {'ok':True}
@app.post('/api/logout')
async def logout(response:Response): response.delete_cookie(COOKIE); return {'ok':True}
@app.get('/api/stats')
async def stats(request:Request):
    if not _auth(request): raise HTTPException(401,'Требуется вход администратора')
    return user_tracking.dashboard()
@app.get('/api/export.csv')
async def export(request:Request):
    if not _auth(request): raise HTTPException(401,'Требуется вход администратора')
    return Response(user_tracking.export_csv(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=gpu-profiler-usage.csv'})
@app.get('/',response_class=HTMLResponse)
async def index(): return HTML

HTML="""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width'><title>GPU Profiler · Analytics</title><style>body{margin:0;background:#0b1020;color:#dce6f5;font:14px system-ui}main{max-width:1100px;margin:40px auto;padding:20px}.card{background:#151d2f;border:1px solid #2b3854;border-radius:12px;padding:18px;margin-bottom:16px}input,button{background:#0d1424;color:#fff;border:1px solid #40506f;border-radius:7px;padding:10px;margin:4px}button{cursor:pointer}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.num{font-size:28px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid #293650}.error{color:#ff8585}.active{color:#71e3aa}@media(max-width:700px){.stats{grid-template-columns:1fr 1fr}.table{overflow:auto}}</style><main><h1>Статистика GPU Profiler</h1><section id=login class=card><input id=user placeholder='Пользователь'><input id=pass type=password placeholder='Пароль'><button onclick=signin()>Войти</button><span id=err class=error></span></section><section id=panel hidden><div class=stats><div class=card><div id=total class=num>—</div>всего</div><div class=card><div id=active class=num>—</div>активны сейчас</div><div class=card><div id=day class=num>—</div>среднее в день</div><div class=card><div id=month class=num>—</div>среднее в месяц</div></div><div class=card><button onclick="location.href='/api/export.csv'">Скачать CSV</button><button onclick=logout()>Выйти</button></div><div class='card table'><table><thead><tr><th>Пользователь</th><th>Первый вход</th><th>Последняя активность</th><th>Всего сессий</th><th>Общее время</th><th>Средняя сессия</th><th>Статус сейчас</th></tr></thead><tbody id=rows></tbody></table></div></section></main><script>const $=x=>document.getElementById(x),dt=x=>new Date(x*1000).toLocaleString('ru-RU'),dur=x=>`${Math.floor(x/3600)}ч ${Math.floor(x%3600/60)}м`,esc=s=>{let d=document.createElement('div');d.textContent=s;return d.innerHTML};async function load(){let r=await fetch('/api/stats');if(r.status===401)return;let d=await r.json();$('login').hidden=true;$('panel').hidden=false;$('total').textContent=d.total_users;$('active').textContent=d.active_users;$('day').textContent=d.average_users_per_day.toFixed(1);$('month').textContent=d.average_users_per_month.toFixed(1);$('rows').innerHTML=d.users.map(u=>`<tr><td>${esc(u.username)}</td><td>${dt(u.first_visit)}</td><td>${dt(u.last_visit)}</td><td>${u.sessions}</td><td>${dur(u.total_seconds)}</td><td>${dur(u.avg_seconds)}</td><td class=${u.active?'active':''}>${u.active?'онлайн':'офлайн'}</td></tr>`).join('')}async function signin(){let r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('user').value,password:$('pass').value})});if(!r.ok){$('err').textContent='Неверный пользователь или пароль';return}load()}async function logout(){await fetch('/api/logout',{method:'POST'});location.reload()}load();setInterval(load,30000)</script></html>"""
