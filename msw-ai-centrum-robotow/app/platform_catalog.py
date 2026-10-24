"""Read-only bridge to the independent MSW Technical Exchange (Node service).
No WordPress, no user-supplied URL, no fake values.
"""
from __future__ import annotations
import math
import re
import httpx
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from .main import app

UPSTREAM="https://msw-ai-gielda-technologii.onrender.com"
async def fetch_json(path:str,method:str="GET",body:dict|None=None,params:dict|None=None):
    url=UPSTREAM+path
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(16,connect=6),follow_redirects=False) as client:
            resp=await client.request(method,url,params=params,json=body,headers={"Accept":"application/json","User-Agent":"MSW-AI-COM-Technical-Catalog/1.0"})
        if resp.status_code>=300 and resp.status_code<400:raise HTTPException(502,"Niedozwolone przekierowanie katalogu")
        if len(resp.content)>280000:raise HTTPException(502,"Za duża odpowiedź katalogu")
        if resp.status_code>=500:raise HTTPException(503,"Moduł techniczny chwilowo nieosiągalny")
        if resp.status_code>=400:raise HTTPException(422,"Niepoprawne dane operacji technicznej")
        value=resp.json()
        if not isinstance(value,dict):raise HTTPException(502,"Niespójna odpowiedź API katalogu")
        return value
    except (httpx.HTTPError,ValueError):
        raise HTTPException(503,"Moduł 300 dziedzin / 500 narzędzi chwilowo nieosiągalny")
@app.get("/api/portal/technical/status")
async def technology_status():
    data=await fetch_json("/api/market/health")
    return {"source":"verified_external_msw_microservice","data":data}
def params(q:str,page:int,per_page:int,group:str):
    return {"q":q[:80],"page":max(1,min(page,300)),"per_page":max(1,min(per_page,50)),"group":group.upper()[:16]}
@app.get("/api/portal/technical/domains")
async def technology_domains(q:str="",page:int=1,per_page:int=20,group:str=""):
    return await fetch_json("/api/market/domains",params=params(q,page,per_page,group))
@app.get("/api/portal/technical/operations")
async def technology_operations(q:str="",page:int=1,per_page:int=20,group:str=""):
    return await fetch_json("/api/market/operations",params=params(q,page,per_page,group))
@app.get("/api/portal/technical/bots")
async def technology_bots(q:str="",page:int=1,per_page:int=20,group:str=""):
    return await fetch_json("/api/market/bots",params=params(q,page,per_page,group))
class Evaluate(BaseModel):
    id:str=Field(min_length=6,max_length=6)
    values:list[float]=Field(min_length=1,max_length=3)
@app.post("/api/portal/technical/evaluate")
async def technology_evaluate(data:Evaluate):
    if not re.fullmatch(r"OP-[0-9]{3}",data.id):raise HTTPException(422,"Niepoprawny identyfikator operacji")
    if not all(math.isfinite(x) and abs(x)<=1e9 for x in data.values):raise HTTPException(422,"Nieprawidłowe liczby")
    result=await fetch_json("/api/market/evaluate",method="POST",body=data.model_dump())
    return {"source":"actual_calculation_in_independent_msw_microservice","result":result}
